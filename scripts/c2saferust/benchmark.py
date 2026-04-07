#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

from __future__ import annotations

import csv
import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import intake
import oracle_runners
import profiles


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BENCHMARK_ROOT = Path("Documentation/rust/c2saferust/benchmarks")


def _repo_root(repo_root: str | Path | None) -> Path:
    return Path(repo_root).resolve() if repo_root else REPO_ROOT


def _rel(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path.resolve())


def _path_from_root(root: Path, path_like: str | Path) -> Path:
    path = Path(path_like)
    return path if path.is_absolute() else (root / path)


def _write_json(path: str | Path, payload: dict) -> None:
    intake.write_json(path, payload)


def _load_json_if_exists(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _load_jsonl_if_exists(path: Path) -> list[dict]:
    if not path.exists():
        return []
    events: list[dict] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        events.append(json.loads(line))
    return events


def _append_jsonl(path: str | Path, payload: dict) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True))
        handle.write("\n")


def _append_provenance_text(path: str | Path, payload: dict) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    details: list[str] = []
    for key in ["label", "command", "note", "contamination_status"]:
        value = payload.get(key)
        if value:
            details.append(f"{key}={value}")
    for entry in payload.get("paths", []):
        details.append(f"path={entry}")
    line = (
        f"[{payload['timestamp']}] kind={payload['kind']} actor={payload['actor']}"
        + (f" {' '.join(details)}" if details else "")
        + "\n"
    )
    with output_path.open("a", encoding="utf-8") as handle:
        handle.write(line)


def _resolve_manifest_artifact_path(manifest: dict, artifact_path: str | Path) -> Path:
    path = Path(artifact_path)
    if path.is_absolute():
        return path
    repo_root = Path(manifest["source_tree"]["kernel_tree"])
    return (repo_root / path).resolve()


def _git_output(repo: Path, *args: str) -> str | None:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def _git_config_value(repo: Path, key: str) -> str | None:
    return _git_output(repo, "config", "--get", key)


def _run_command(
    command: list[str],
    *,
    timeout_sec: int | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        output_lines = [*stdout.splitlines()[-20:], *stderr.splitlines()[-20:]]
        suffix = "\n".join(output_lines)
        if suffix:
            suffix = f": {suffix}"
        raise RuntimeError(f"Timed out after {timeout_sec}s while running `{ ' '.join(command) }`{suffix}") from exc


def _raise_command_failure(command: list[str], completed: subprocess.CompletedProcess[str], *, prefix: str) -> None:
    output_lines = [*completed.stdout.splitlines()[-20:], *completed.stderr.splitlines()[-20:]]
    suffix = "\n".join(output_lines)
    if suffix:
        suffix = f": {suffix}"
    raise RuntimeError(f"{prefix} `{ ' '.join(command) }`{suffix}")


def _remove_tree(path: Path) -> None:
    if not path.exists():
        return
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    shutil.rmtree(path)


def _best_effort_prune_worktree(repo: Path, worktree_path: Path) -> None:
    _run_command(
        ["git", "-C", str(repo), "worktree", "remove", "--force", str(worktree_path)],
        timeout_sec=30,
    )


def _cleanup_checkout_root(repo: Path, checkout_root: Path) -> None:
    try:
        _best_effort_prune_worktree(repo, checkout_root)
    except RuntimeError:
        pass
    _remove_tree(checkout_root)
    _run_command(
        ["git", "-C", str(repo), "worktree", "prune", "--expire", "now"],
        timeout_sec=30,
    )


def _populate_tree_via_worktree(repo: Path, checkout_root: Path, baseline_rev: str) -> None:
    commands = [
        [
            "git",
            "-C",
            str(repo),
            "worktree",
            "add",
            "--force",
            "--detach",
            "--no-checkout",
            str(checkout_root),
            baseline_rev,
        ],
        ["git", "-C", str(checkout_root), "checkout", "--detach", "--force", baseline_rev],
    ]
    for command in commands:
        completed = _run_command(command, timeout_sec=120)
        if completed.returncode != 0:
            _raise_command_failure(command, completed, prefix="Failed to initialize worktree via")


def _populate_tree_via_shared_clone(repo: Path, checkout_root: Path, baseline_rev: str) -> None:
    commands = [["git", "clone", "--shared", "--no-checkout", str(repo), str(checkout_root)]]
    for command in commands:
        completed = _run_command(command, timeout_sec=240)
        if completed.returncode != 0:
            _raise_command_failure(command, completed, prefix="Failed to initialize fallback clone via")
    source_remote = _git_config_value(repo, "remote.origin.url")
    source_promisor = _git_config_value(repo, "remote.origin.promisor")
    source_filter = _git_config_value(repo, "remote.origin.partialclonefilter")
    config_commands: list[list[str]] = []
    if source_remote:
        config_commands.append(["git", "-C", str(checkout_root), "remote", "set-url", "origin", source_remote])
    if source_promisor:
        config_commands.append(["git", "-C", str(checkout_root), "config", "remote.origin.promisor", source_promisor])
    if source_filter:
        config_commands.append(
            ["git", "-C", str(checkout_root), "config", "remote.origin.partialclonefilter", source_filter]
        )
    config_commands.append(["git", "-C", str(checkout_root), "checkout", "--detach", "--force", baseline_rev])
    for command in config_commands:
        completed = _run_command(command, timeout_sec=240)
        if completed.returncode != 0:
            _raise_command_failure(command, completed, prefix="Failed to finalize fallback clone via")


def _remove_physical_absence_targets(blind_root: Path, removal_targets: list[str]) -> tuple[list[str], list[str]]:
    removed_paths: list[str] = []
    missing_paths: list[str] = []
    for relpath in removal_targets:
        target = blind_root / relpath
        try:
            target.unlink()
            removed_paths.append(relpath)
        except FileNotFoundError:
            missing_paths.append(relpath)
    return removed_paths, missing_paths


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _benchmark_output_dir(repo: Path, benchmark_id: str, output_dir: str | Path | None) -> Path:
    if output_dir is not None:
        return Path(output_dir)
    return repo / DEFAULT_BENCHMARK_ROOT / benchmark_id


def _resolve_policy_paths(entries: list[str], base_root: Path) -> list[str]:
    resolved: list[str] = []
    for entry in entries:
        if entry.startswith("commit:") or "*" in entry or "any git show" in entry:
            resolved.append(entry)
            continue
        resolved.append(str(_path_from_root(base_root, entry).resolve()))
    return resolved


def _manifest_path_for_benchmark(
    repo_root: Path,
    benchmark_id: str,
) -> Path:
    return (_benchmark_output_dir(repo_root, benchmark_id, None) / "module-manifest.json").resolve()


def _planning_artifact_refs(repo: Path, module_id: str, artifact_root: str | Path | None) -> dict[str, str]:
    filenames = [
        "kbuild-plan.json",
        "binding-gap-audit.json",
        "external-header-plan.json",
        "helper-audit.json",
        "kbuild-patch-plan.json",
        "bindings-patch-plan.json",
        "helpers-patch-plan.json",
        "abstraction-plan.json",
        "unsafe-obligations.json",
        "translation-plan.json",
        "command-semantics.json",
        "safety-policy.json",
        "soundness-discharge.json",
        "agent-workflow-plan.json",
        "safety-verdict.json",
        "agent-gate-report.json",
        "toolchain-run-record.json",
        "module-lifecycle.config",
    ]
    refs = {}
    for filename in filenames:
        key = filename
        if key.endswith(".json"):
            key = key.removesuffix(".json")
        elif key.endswith(".config"):
            key = key.removesuffix(".config") + "_config"
        refs[key.replace("-", "_").replace(".", "_")] = intake._artifact_path_for(
            module_id,
            filename,
            repo_root=repo,
            artifact_root=artifact_root,
        )
    return refs


def _manual_module_context(
    benchmark_profile: dict,
    repo: Path,
    *,
    module_path: str | Path | None = None,
) -> dict:
    resolved_module_path = module_path or benchmark_profile["module_path"]
    module_id = benchmark_profile.get("module_id", Path(resolved_module_path).stem)
    driver_rust_path = benchmark_profile["driver_rust_path"]
    artifact_dir = benchmark_profile.get("artifact_dir", f"Documentation/rust/c2saferust/{module_id}")
    return {
        "repo_root": repo,
        "profile": {},
        "profile_id": benchmark_profile.get("module_profile_id"),
        "module_id": module_id,
        "module_path": str(resolved_module_path),
        "module_c_path": str(resolved_module_path),
        "module_source_path": _path_from_root(repo, resolved_module_path),
        "driver_rust_path": driver_rust_path,
        "artifact_dir": artifact_dir,
        "source_tree": str(repo),
    }


def resolve_benchmark_context(
    benchmark_id: str,
    *,
    repo_root: str | Path | None = None,
    artifact_root: str | Path | None = None,
    module_path: str | Path | None = None,
    profile_id: str | None = None,
    source_tree: str | Path | None = None,
    output_dir: str | Path | None = None,
    reference_tree: str | Path | None = None,
    official_reference: str | Path | None = None,
    official_reference_initial: str | Path | None = None,
    build_dir_c: str | Path | None = None,
    build_dir_rust: str | Path | None = None,
) -> dict:
    repo = _repo_root(repo_root)
    benchmark_profile = profiles.load_benchmark_profile(benchmark_id)
    module_profile_id = profile_id or benchmark_profile.get("module_profile_id")

    if module_profile_id or module_path or benchmark_profile.get("module_profile_id"):
        try:
            module_context = intake.resolve_module_context(
                module_path or benchmark_profile.get("module_path"),
                repo_root=repo,
                profile_id=module_profile_id,
                source_tree=source_tree,
            )
        except FileNotFoundError:
            module_context = _manual_module_context(benchmark_profile, repo, module_path=module_path)
    else:
        module_context = _manual_module_context(benchmark_profile, repo, module_path=module_path)

    benchmark_dir = _benchmark_output_dir(repo, benchmark_id, output_dir).resolve()
    reference_root = Path(reference_tree).resolve() if reference_tree else repo
    mature_reference = official_reference or benchmark_profile.get("official_reference")
    initial_reference = official_reference_initial or benchmark_profile.get("official_reference_initial")

    benchmark_artifacts = {
        "planner_contract": str((benchmark_dir / "planner-contract.json").resolve()),
        "module_manifest": str((benchmark_dir / "module-manifest.json").resolve()),
        "validator_ready_summary": str((benchmark_dir / "validator-ready-summary.json").resolve()),
        "difference_metrics": str((benchmark_dir / "difference-metrics.json").resolve()),
        "differential_oracle_run_record": str((benchmark_dir / "differential-oracle-run-record.json").resolve()),
        "lifecycle_oracle_run_record": str((benchmark_dir / "lifecycle-oracle-run-record.json").resolve()),
        "provenance_log": str((benchmark_dir / "provenance-log.jsonl").resolve()),
        "provenance_log_text": str((benchmark_dir / "provenance_log.txt").resolve()),
    }

    return {
        "repo_root": repo,
        "artifact_root": artifact_root,
        "benchmark_profile": benchmark_profile,
        "module_context": module_context,
        "benchmark_dir": benchmark_dir,
        "reference_tree": reference_root,
        "official_reference": str(_path_from_root(reference_root, mature_reference).resolve())
        if mature_reference
        else None,
        "official_reference_initial": str(_path_from_root(reference_root, initial_reference).resolve())
        if initial_reference
        else None,
        "build_dir_c": str(Path(build_dir_c).resolve()) if build_dir_c else None,
        "build_dir_rust": str(Path(build_dir_rust).resolve()) if build_dir_rust else None,
        "benchmark_artifacts": benchmark_artifacts,
        "planning_artifacts": _planning_artifact_refs(repo, module_context["module_id"], artifact_root),
    }


def resolve_benchmark_manifest_path(
    *,
    manifest_path: str | Path | None = None,
    benchmark_id: str | None = None,
    repo_root: str | Path | None = None,
    artifact_root: str | Path | None = None,
    module_path: str | Path | None = None,
    profile_id: str | None = None,
    source_tree: str | Path | None = None,
    output_dir: str | Path | None = None,
    reference_tree: str | Path | None = None,
    official_reference: str | Path | None = None,
    official_reference_initial: str | Path | None = None,
    build_dir_c: str | Path | None = None,
    build_dir_rust: str | Path | None = None,
) -> Path:
    if manifest_path is not None:
        return Path(manifest_path).resolve()
    if benchmark_id is None:
        raise ValueError("Either `manifest_path` or `benchmark_id` must be provided.")
    context = resolve_benchmark_context(
        benchmark_id,
        repo_root=repo_root,
        artifact_root=artifact_root,
        module_path=module_path,
        profile_id=profile_id,
        source_tree=source_tree,
        output_dir=output_dir,
        reference_tree=reference_tree,
        official_reference=official_reference,
        official_reference_initial=official_reference_initial,
        build_dir_c=build_dir_c,
        build_dir_rust=build_dir_rust,
    )
    return Path(context["benchmark_artifacts"]["module_manifest"]).resolve()


def build_module_manifest(
    benchmark_id: str,
    *,
    repo_root: str | Path | None = None,
    artifact_root: str | Path | None = None,
    module_path: str | Path | None = None,
    profile_id: str | None = None,
    source_tree: str | Path | None = None,
    output_dir: str | Path | None = None,
    reference_tree: str | Path | None = None,
    official_reference: str | Path | None = None,
    official_reference_initial: str | Path | None = None,
    build_dir_c: str | Path | None = None,
    build_dir_rust: str | Path | None = None,
) -> dict:
    context = resolve_benchmark_context(
        benchmark_id,
        repo_root=repo_root,
        artifact_root=artifact_root,
        module_path=module_path,
        profile_id=profile_id,
        source_tree=source_tree,
        output_dir=output_dir,
        reference_tree=reference_tree,
        official_reference=official_reference,
        official_reference_initial=official_reference_initial,
        build_dir_c=build_dir_c,
        build_dir_rust=build_dir_rust,
    )
    repo = context["repo_root"]
    benchmark_profile = context["benchmark_profile"]
    module_context = context["module_context"]
    benchmark_dir = context["benchmark_dir"]

    search_hygiene = benchmark_profile.get("search_hygiene", {})
    blind_policy = benchmark_profile.get("blind_policy", {})
    runtime_plan = benchmark_profile.get("runtime_plan", {})
    branch = _git_output(repo, "branch", "--show-current")
    head = _git_output(repo, "rev-parse", "HEAD")

    return {
        "schema_version": 1,
        "artifact_type": "module-manifest",
        "benchmark_id": benchmark_id,
        "module_id": module_context["module_id"],
        "module_title": benchmark_profile["module_title"],
        "benchmark_track": benchmark_profile["benchmark_track"],
        "sample_tier": benchmark_profile["sample_tier"],
        "scope": benchmark_profile["scope"],
        "quota_class": benchmark_profile["quota_class"],
        "target_runtime_state": benchmark_profile["target_runtime_state"],
        "pipeline_mode": benchmark_profile.get("pipeline_mode", "ground-truth"),
        "conversion_stage": benchmark_profile.get("initial_conversion_stage", "blind"),
        "style_alignment": benchmark_profile.get("initial_style_alignment", "unreviewed"),
        "runtime_state": benchmark_profile.get("initial_runtime_state", "unvalidated"),
        "claim_ceiling": benchmark_profile["claim_ceiling"],
        "profile_id": module_context.get("profile_id"),
        "module_c_path": module_context["module_c_path"],
        "driver_rust_path": module_context["driver_rust_path"],
        "source_tree": {
            "kernel_tree": str(repo),
            "blind_tree": str(repo),
            "reference_tree": str(context["reference_tree"]),
            "working_branch": branch,
            "head": head,
        },
        "search_hygiene": {
            "forbidden_reference_paths": _resolve_policy_paths(
                search_hygiene.get("forbidden_reference_paths", []),
                context["reference_tree"],
            ),
            "search_allowlist": _resolve_policy_paths(search_hygiene.get("search_allowlist", []), repo),
            "incidental_path_exposure_policy": blind_policy.get(
                "incidental_path_exposure_policy",
                "path-only-no-body-no-diff",
            ),
        },
        "blind_policy": {
            "freeze_before_compare": blind_policy.get("freeze_before_compare", True),
            "provenance_log_required": blind_policy.get("provenance_log_required", True),
            "contamination_mode": blind_policy.get("contamination_mode", "contaminated_do_not_reuse"),
            "physical_absence_required_paths": blind_policy.get("physical_absence_required_paths", []),
        },
        "runtime_plan": runtime_plan,
        "build": {
            "build_dir_c": context["build_dir_c"],
            "build_dir_rust": context["build_dir_rust"],
        },
        "planning_artifacts_root": intake._artifact_dir_for(
            module_context["module_id"],
            repo_root=repo,
            artifact_root=artifact_root,
        ),
        "planning_artifacts": context["planning_artifacts"],
        "benchmark_artifacts": context["benchmark_artifacts"],
        "official_compare": {
            "initial_reference": context["official_reference_initial"],
            "mature_reference": context["official_reference"],
        },
        "difference_metrics": benchmark_profile.get("difference_metrics", {}),
        "artifact_directory": _rel(repo, benchmark_dir),
    }


def build_planner_contract(manifest: dict) -> dict:
    target_runtime_state = manifest["target_runtime_state"]
    runtime_required_results: list[str] = []
    if target_runtime_state == "differential_validated":
        runtime_required_results = ["toolchain-run-record-pass", "differential-oracle-pass"]
    elif target_runtime_state == "behavior_validated":
        runtime_required_results = ["toolchain-run-record-pass"]
    elif target_runtime_state == "lifecycle_validated":
        runtime_required_results = ["lifecycle-oracle-pass"]

    return {
        "schema_version": 1,
        "artifact_type": "planner-contract",
        "benchmark_id": manifest["benchmark_id"],
        "module_id": manifest["module_id"],
        "pipeline_mode": manifest["pipeline_mode"],
        "target_runtime_state": target_runtime_state,
        "acceptance_targets": {
            "static": {
                "required_results": [
                    "planner-artifact-validate-pass",
                    "driver-unsafe-scan-pass",
                    "search-hygiene-clean",
                ]
            },
            "build": {
                "required_results": [
                    "c-build-pass",
                    "rust-toolchain-available-pass",
                    "rust-object-build-pass",
                    "rust-module-package-pass",
                ]
            },
            "runtime": {
                "required_results": runtime_required_results,
            },
        },
        "reference_policy": {
            "forbidden_reference_paths": manifest["search_hygiene"]["forbidden_reference_paths"],
            "search_allowlist": manifest["search_hygiene"]["search_allowlist"],
            "incidental_path_exposure_policy": manifest["search_hygiene"]["incidental_path_exposure_policy"],
            "freeze_before_compare": manifest["blind_policy"]["freeze_before_compare"],
        },
        "workspace": {
            "kernel_worktree": manifest["source_tree"]["kernel_tree"],
            "reference_tree": manifest["source_tree"]["reference_tree"],
            "planning_artifact_root": manifest["planning_artifacts_root"],
            "benchmark_artifact_root": manifest["artifact_directory"],
            "build_dir_c": manifest["build"]["build_dir_c"],
            "build_dir_rust": manifest["build"]["build_dir_rust"],
        },
        "inputs": {
            "module_manifest": manifest["benchmark_artifacts"]["module_manifest"],
            "translation_plan": manifest["planning_artifacts"]["translation_plan"],
            "safety_verdict": manifest["planning_artifacts"]["safety_verdict"],
            "agent_gate_report": manifest["planning_artifacts"]["agent_gate_report"],
        },
    }


def load_provenance_events(manifest: dict) -> list[dict]:
    return _load_jsonl_if_exists(_resolve_manifest_artifact_path(manifest, manifest["benchmark_artifacts"]["provenance_log"]))


def _provenance_summary(manifest: dict) -> dict:
    events = load_provenance_events(manifest)
    log_text_ref = manifest["benchmark_artifacts"].get(
        "provenance_log_text",
        str(
            Path(manifest["benchmark_artifacts"]["provenance_log"])
            .with_name("provenance_log.txt")
            .resolve()
        ),
    )
    contaminated_events = [
        event
        for event in events
        if event.get("contamination_status") == "contaminated" or event.get("kind") == "contamination"
    ]
    freeze_events = [event for event in events if event.get("kind") == "freeze"]
    compare_unlock_events = [event for event in events if event.get("kind") == "compare-unlock"]
    return {
        "log_path": str(
            _resolve_manifest_artifact_path(manifest, manifest["benchmark_artifacts"]["provenance_log"]).resolve()
        ),
        "log_text_path": str(_resolve_manifest_artifact_path(manifest, log_text_ref).resolve()),
        "event_count": len(events),
        "events": events,
        "contamination_status": "contaminated" if contaminated_events else "clean",
        "contamination_event_count": len(contaminated_events),
        "freeze_status": "frozen" if freeze_events else "unfrozen",
        "freeze_event_count": len(freeze_events),
        "compare_unlock_event_count": len(compare_unlock_events),
    }


def record_provenance_event(
    *,
    manifest_path: str | Path | None = None,
    benchmark_id: str | None = None,
    repo_root: str | Path | None = None,
    artifact_root: str | Path | None = None,
    module_path: str | Path | None = None,
    profile_id: str | None = None,
    source_tree: str | Path | None = None,
    output_dir: str | Path | None = None,
    reference_tree: str | Path | None = None,
    official_reference: str | Path | None = None,
    official_reference_initial: str | Path | None = None,
    build_dir_c: str | Path | None = None,
    build_dir_rust: str | Path | None = None,
    kind: str,
    actor: str = "agent",
    label: str | None = None,
    command: str | None = None,
    paths: list[str] | None = None,
    note: str | None = None,
    contamination_status: str | None = None,
) -> dict:
    resolved_manifest_path = resolve_benchmark_manifest_path(
        manifest_path=manifest_path,
        benchmark_id=benchmark_id,
        repo_root=repo_root,
        artifact_root=artifact_root,
        module_path=module_path,
        profile_id=profile_id,
        source_tree=source_tree,
        output_dir=output_dir,
        reference_tree=reference_tree,
        official_reference=official_reference,
        official_reference_initial=official_reference_initial,
        build_dir_c=build_dir_c,
        build_dir_rust=build_dir_rust,
    )
    manifest = json.loads(resolved_manifest_path.read_text())
    log_path = _resolve_manifest_artifact_path(manifest, manifest["benchmark_artifacts"]["provenance_log"])
    log_text_ref = manifest["benchmark_artifacts"].get(
        "provenance_log_text",
        str(Path(manifest["benchmark_artifacts"]["provenance_log"]).with_name("provenance_log.txt").resolve()),
    )
    event = {
        "schema_version": 1,
        "artifact_type": "benchmark-provenance-event",
        "timestamp": _utc_timestamp(),
        "benchmark_id": manifest["benchmark_id"],
        "module_id": manifest["module_id"],
        "kind": kind,
        "actor": actor,
    }
    if label:
        event["label"] = label
    if command:
        event["command"] = command
    if paths:
        event["paths"] = list(paths)
    if note:
        event["note"] = note
    if contamination_status:
        event["contamination_status"] = contamination_status
    _append_jsonl(log_path, event)
    _append_provenance_text(_resolve_manifest_artifact_path(manifest, log_text_ref), event)
    return {
        "schema_version": 1,
        "artifact_type": "benchmark-provenance-log-update",
        "benchmark_id": manifest["benchmark_id"],
        "module_id": manifest["module_id"],
        "log_path": str(log_path.resolve()),
        "log_text_path": str(_resolve_manifest_artifact_path(manifest, log_text_ref).resolve()),
        "event": event,
    }


def initialize_blind_worktree_pair(
    *,
    repo_root: str | Path,
    baseline_rev: str,
    blind_tree: str | Path,
    reference_tree: str | Path,
    benchmark_id: str | None = None,
    forbidden_relpath: str | None = None,
    seal_filename: str = ".c2saferust-blind-seal.json",
) -> dict:
    repo = _repo_root(repo_root)
    blind_root = Path(blind_tree).resolve()
    reference_root = Path(reference_tree).resolve()
    if blind_root.exists():
        raise FileExistsError(f"Blind worktree path already exists: {blind_root}")
    if reference_root.exists():
        raise FileExistsError(f"Reference worktree path already exists: {reference_root}")

    benchmark_profile = profiles.load_benchmark_profile(benchmark_id) if benchmark_id else {}
    physical_absence_paths = benchmark_profile.get("blind_policy", {}).get("physical_absence_required_paths", [])
    removal_targets = list(physical_absence_paths)
    if forbidden_relpath and forbidden_relpath not in removal_targets:
        removal_targets.insert(0, forbidden_relpath)
    if not removal_targets:
        raise ValueError("No physical-absence target path was provided for the blind worktree.")

    resolved_baseline = _git_output(repo, "rev-parse", f"{baseline_rev}^{{commit}}")
    if resolved_baseline is None:
        raise ValueError(f"Could not resolve baseline revision {baseline_rev!r}.")

    source_promisor = (_git_config_value(repo, "remote.origin.promisor") or "").strip().lower()
    source_filter = _git_config_value(repo, "remote.origin.partialclonefilter")
    skip_worktree = source_promisor == "true"
    init_method = "git-worktree"
    fallback_reason: str | None = None
    if skip_worktree:
        init_method = "shared-clone-fallback"
        _populate_tree_via_shared_clone(repo, blind_root, resolved_baseline)
        _populate_tree_via_shared_clone(repo, reference_root, resolved_baseline)
        fallback_reason = "source-repo-promisor-remote"
        if source_filter:
            fallback_reason = f"{fallback_reason}:{source_filter}"
    else:
        try:
            _populate_tree_via_worktree(repo, blind_root, resolved_baseline)
            _populate_tree_via_worktree(repo, reference_root, resolved_baseline)
        except RuntimeError as worktree_error:
            init_method = "shared-clone-fallback"
            fallback_reason = str(worktree_error)
            _cleanup_checkout_root(repo, blind_root)
            _cleanup_checkout_root(repo, reference_root)
            _populate_tree_via_shared_clone(repo, blind_root, resolved_baseline)
            _populate_tree_via_shared_clone(repo, reference_root, resolved_baseline)

    removed_paths, missing_paths = _remove_physical_absence_targets(blind_root, removal_targets)

    seal_path = blind_root / seal_filename
    seal_payload = {
        "schema_version": 1,
        "artifact_type": "blind-worktree-seal",
        "timestamp": _utc_timestamp(),
        "benchmark_id": benchmark_id,
        "baseline_commit": resolved_baseline,
        "source_repo": str(repo),
        "blind_tree": str(blind_root),
        "reference_tree": str(reference_root),
        "initialization_method": init_method,
        "removed_paths": removed_paths,
        "missing_paths": missing_paths,
    }
    if fallback_reason:
        seal_payload["fallback_reason"] = fallback_reason
    _write_json(seal_path, seal_payload)

    payload = {
        "schema_version": 1,
        "artifact_type": "blind-worktree-init",
        "benchmark_id": benchmark_id,
        "baseline_commit": resolved_baseline,
        "source_repo": str(repo),
        "blind_tree": str(blind_root),
        "reference_tree": str(reference_root),
        "initialization_method": init_method,
        "removed_paths": removed_paths,
        "missing_paths": missing_paths,
        "seal_path": str(seal_path),
    }
    if fallback_reason:
        payload["fallback_reason"] = fallback_reason
    return payload


def _classify_run_record(record: dict | None, *, source: str) -> dict:
    if record is None:
        return {
            "source": source,
            "status": "not_run",
            "pass": None,
            "blocked": None,
            "ready_for_oracle": None,
            "evidence_tier": None,
            "runner_id": None,
            "runner_status": None,
            "blocker_kind": None,
            "blockers": [],
        }

    runner = record.get("runner", {})
    runner_id = record.get("runner_id") or record.get("oracle_runner") or runner.get("id")
    runner_status = runner.get("status")
    blocker_kind = record.get("blocker_kind")
    blocked = record.get("blocked", False)
    if blocked:
        if blocker_kind == "runner-unavailable" or runner_status == "runner-unavailable":
            status = "runner_unavailable"
        else:
            status = "environment_blocked"
    elif record.get("pass"):
        if source == "differential":
            status = "differential_validated"
        elif source == "lifecycle":
            status = "lifecycle_validated"
        else:
            status = "behavior_validated"
    else:
        status = "candidate_failed"

    return {
        "source": source,
        "status": status,
        "pass": record.get("pass"),
        "blocked": blocked,
        "ready_for_oracle": record.get("ready_for_oracle"),
        "evidence_tier": record.get("evidence_tier"),
        "runner_id": runner_id,
        "runner_status": runner_status,
        "blocker_kind": blocker_kind,
        "blockers": list(record.get("blockers", [])),
    }


def _runtime_assessment(
    manifest: dict,
    *,
    toolchain_run: dict | None,
    lifecycle_run: dict | None,
    differential_run: dict | None,
    gate_report: dict | None,
) -> dict:
    if differential_run is not None:
        selected = _classify_run_record(differential_run, source="differential")
    elif lifecycle_run is not None:
        selected = _classify_run_record(lifecycle_run, source="lifecycle")
    elif toolchain_run is not None:
        selected = _classify_run_record(toolchain_run, source="toolchain")
    elif _package_gate_pass(gate_report):
        selected = _classify_run_record(None, source="build")
        selected["status"] = "build_validated"
    else:
        selected = _classify_run_record(None, source="toolchain")

    runtime_plan = manifest.get("runtime_plan", {})
    available_runners = set(oracle_runners.available_runner_ids())
    primary_runner = runtime_plan.get("primary_runner")
    fallback_runner = runtime_plan.get("fallback_runner")
    observed_runner = selected.get("runner_id")
    selected["target_runtime_state"] = manifest["target_runtime_state"]
    selected["primary_runner"] = primary_runner
    selected["fallback_runner"] = fallback_runner
    selected["primary_runner_available"] = primary_runner in available_runners if primary_runner else None
    selected["fallback_runner_available"] = fallback_runner in available_runners if fallback_runner else None
    selected["fallback_runner_used"] = bool(
        observed_runner and primary_runner and fallback_runner and observed_runner != primary_runner and observed_runner == fallback_runner
    )
    return selected


def _gate_pass(gate_report: dict | None, gate_id: str) -> bool:
    if not gate_report:
        return False
    for gate in gate_report.get("gates", []):
        if gate.get("id") == gate_id and gate.get("status") == "passed":
            return True
    return False


def _compile_gate_pass(gate_report: dict | None) -> bool:
    return _gate_pass(gate_report, "compile-driver-object")


def _package_gate_pass(gate_report: dict | None) -> bool:
    return _gate_pass(gate_report, "package-module")


def _rust_toolchain_gate_pass(gate_report: dict | None) -> bool:
    return _gate_pass(gate_report, "rust-toolchain-available")


def build_validator_ready_summary(
    manifest_path: str | Path,
    *,
    output: str | Path | None = None,
) -> dict:
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text())
    safety_verdict = _load_json_if_exists(
        _resolve_manifest_artifact_path(manifest, manifest["planning_artifacts"]["safety_verdict"])
    )
    gate_report = _load_json_if_exists(
        _resolve_manifest_artifact_path(manifest, manifest["planning_artifacts"]["agent_gate_report"])
    )
    toolchain_run = _load_json_if_exists(
        _resolve_manifest_artifact_path(manifest, manifest["planning_artifacts"]["toolchain_run_record"])
    )
    lifecycle_run = _load_json_if_exists(
        _resolve_manifest_artifact_path(manifest, manifest["benchmark_artifacts"]["lifecycle_oracle_run_record"])
    )
    differential_run = _load_json_if_exists(
        _resolve_manifest_artifact_path(manifest, manifest["benchmark_artifacts"]["differential_oracle_run_record"])
    )
    difference_metrics = _load_json_if_exists(
        _resolve_manifest_artifact_path(manifest, manifest["benchmark_artifacts"]["difference_metrics"])
    )
    provenance = _provenance_summary(manifest)

    runtime_state = manifest["runtime_state"]
    if differential_run and differential_run.get("pass"):
        runtime_state = "differential_validated"
    elif lifecycle_run and lifecycle_run.get("pass"):
        runtime_state = "lifecycle_validated"
    elif toolchain_run and toolchain_run.get("pass"):
        runtime_state = "behavior_validated"
    elif _package_gate_pass(gate_report):
        runtime_state = "build_validated"

    style_alignment = manifest["style_alignment"]
    if difference_metrics:
        style_alignment = "benchmark-calibrated"

    current_claim_ceiling = "No validated claim yet."
    if runtime_state == "differential_validated":
        current_claim_ceiling = manifest["claim_ceiling"]
    elif runtime_state == "lifecycle_validated":
        current_claim_ceiling = "Lifecycle validation only; no functional equivalence claim yet."
    elif runtime_state in {"behavior_validated", "build_validated"}:
        current_claim_ceiling = "Static/build evidence only; runtime equivalence not claimed."

    runtime_assessment = _runtime_assessment(
        manifest,
        toolchain_run=toolchain_run,
        lifecycle_run=lifecycle_run,
        differential_run=differential_run,
        gate_report=gate_report,
    )

    payload = {
        "schema_version": 1,
        "artifact_type": "validator-ready-summary",
        "benchmark_id": manifest["benchmark_id"],
        "module_id": manifest["module_id"],
        "conversion_stage": manifest["conversion_stage"],
        "style_alignment": style_alignment,
        "runtime_state": runtime_state,
        "target_runtime_state": manifest["target_runtime_state"],
        "contamination_status": provenance["contamination_status"],
        "checks": {
            "safety_pass": safety_verdict.get("pass") if safety_verdict else None,
            "ready_for_smoke": gate_report.get("ready_for_smoke") if gate_report else None,
            "rust_toolchain_available": _rust_toolchain_gate_pass(gate_report),
            "compile_pass": _compile_gate_pass(gate_report),
            "package_pass": _package_gate_pass(gate_report),
            "toolchain_run_pass": toolchain_run.get("pass") if toolchain_run else None,
            "lifecycle_run_pass": lifecycle_run.get("pass") if lifecycle_run else None,
            "differential_run_pass": differential_run.get("pass") if differential_run else None,
        },
        "claims": {
            "declared_claim_ceiling": manifest["claim_ceiling"],
            "current_claim_ceiling": current_claim_ceiling,
        },
        "runtime_assessment": runtime_assessment,
        "provenance": {
            "log_path": provenance["log_path"],
            "log_text_path": provenance["log_text_path"],
            "event_count": provenance["event_count"],
            "freeze_status": provenance["freeze_status"],
            "freeze_event_count": provenance["freeze_event_count"],
            "contamination_event_count": provenance["contamination_event_count"],
        },
        "evidence_refs": {
            "module_manifest": str(manifest_path.resolve()),
            "planner_contract": str(
                _resolve_manifest_artifact_path(manifest, manifest["benchmark_artifacts"]["planner_contract"])
            ),
            "safety_verdict": str(
                _resolve_manifest_artifact_path(manifest, manifest["planning_artifacts"]["safety_verdict"])
            ),
            "agent_gate_report": str(
                _resolve_manifest_artifact_path(manifest, manifest["planning_artifacts"]["agent_gate_report"])
            ),
            "toolchain_run_record": str(
                _resolve_manifest_artifact_path(manifest, manifest["planning_artifacts"]["toolchain_run_record"])
            ),
            "lifecycle_oracle_run_record": str(
                _resolve_manifest_artifact_path(manifest, manifest["benchmark_artifacts"]["lifecycle_oracle_run_record"])
            ),
            "differential_oracle_run_record": str(
                _resolve_manifest_artifact_path(manifest, manifest["benchmark_artifacts"]["differential_oracle_run_record"])
            ),
            "difference_metrics": str(
                _resolve_manifest_artifact_path(manifest, manifest["benchmark_artifacts"]["difference_metrics"])
            ),
            "provenance_log_text": provenance["log_text_path"],
        },
    }
    output_path = Path(output) if output else Path(manifest["benchmark_artifacts"]["validator_ready_summary"])
    _write_json(output_path, payload)
    payload["output_path"] = str(output_path.resolve())
    return payload


def _strip_rust_noncode(text: str) -> str:
    return intake._strip_rust_noncode(text)


def _code_loc(text: str) -> int:
    stripped = _strip_rust_noncode(text)
    return sum(1 for line in stripped.splitlines() if line.strip())


def _count_regex(text: str, pattern: str) -> int:
    return len(re.findall(pattern, text))


def _function_names(text: str) -> list[str]:
    stripped = _strip_rust_noncode(text)
    return re.findall(r"\bfn\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", stripped)


def _struct_field_types(text: str) -> set[str]:
    stripped = _strip_rust_noncode(text)
    types: set[str] = set()
    for match in re.finditer(r"\bstruct\s+[A-Za-z_][A-Za-z0-9_]*\s*\{", stripped):
        body = intake._extract_brace_body(stripped, match.end() - 1)
        for line in body.splitlines():
            field = re.match(r"\s*(?:pub\s+)?[A-Za-z_][A-Za-z0-9_]*\s*:\s*([^,]+),", line)
            if field:
                types.add(field.group(1).strip())
    return types


def _state_flag_count(text: str) -> int:
    stripped = _strip_rust_noncode(text)
    pattern = re.compile(
        r"\s*(?:pub\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*:\s*[^,]+,",
    )
    count = 0
    for field_name in pattern.findall(stripped):
        if re.search(r"(state|flag|status|active|enabled|ready)", field_name):
            count += 1
    return count


def _marker_presence(text: str, markers: list[str]) -> list[str]:
    return [marker for marker in markers if marker in text]


def _teardown_edge_count(text: str, markers: list[str]) -> int:
    stripped = _strip_rust_noncode(text)
    count = 0
    for marker in markers:
        count += len(re.findall(rf"\b{re.escape(marker)}\b", stripped))
    return count


def _lifecycle_transition_count(function_names: list[str], callback_markers: list[str]) -> int:
    lifecycle_verbs = [
        "probe",
        "open",
        "stop",
        "close",
        "release",
        "unbind",
        "remove",
        "suspend",
        "resume",
        "soft_reset",
        "read_status",
        "link_change_notify",
        "config_aneg",
    ]
    from_functions = sum(1 for name in function_names if any(verb in name for verb in lifecycle_verbs))
    from_callbacks = len(callback_markers)
    return max(from_functions, from_callbacks)


def _profile_markers(benchmark_profile: dict) -> tuple[list[str], list[str], list[str]]:
    metrics = dict(benchmark_profile.get("difference_metrics", {}))
    compare_only_metrics = benchmark_profile.get("compare_only", {}).get("difference_metrics", {})
    for key in ["api_markers", "callback_markers", "teardown_markers"]:
        merged: list[str] = []
        for collection in [metrics.get(key, []), compare_only_metrics.get(key, [])]:
            for entry in collection:
                if entry not in merged:
                    merged.append(entry)
        if merged:
            metrics[key] = merged
    return (
        metrics.get("api_markers", []),
        metrics.get("callback_markers", []),
        metrics.get(
            "teardown_markers",
            ["drop", "remove", "release", "stop", "unbind", "destroy", "deallocate", "unregister"],
        ),
    )


def _require_compare_unlock(
    *,
    benchmark_id: str | None,
    repo_root: Path,
) -> None:
    if not benchmark_id:
        return

    manifest_path = _manifest_path_for_benchmark(repo_root, benchmark_id)
    if not manifest_path.exists():
        return

    manifest = json.loads(manifest_path.read_text())
    blind_policy = manifest.get("blind_policy", {})
    if not blind_policy.get("freeze_before_compare", True):
        return

    provenance = _provenance_summary(manifest)
    if provenance["contamination_status"] != "clean":
        raise RuntimeError(
            "Difference metrics are locked because the benchmark provenance is contaminated before compare."
        )
    if provenance["freeze_status"] != "frozen":
        raise RuntimeError(
            "Difference metrics are locked until a freeze event is recorded for this benchmark."
        )


def build_difference_metrics(
    candidate: str | Path,
    reference: str | Path,
    *,
    benchmark_id: str | None = None,
    repo_root: str | Path | None = None,
) -> dict:
    repo = _repo_root(repo_root)
    _require_compare_unlock(benchmark_id=benchmark_id, repo_root=repo)
    candidate_path = _path_from_root(repo, candidate)
    reference_path = _path_from_root(repo, reference)
    candidate_text = candidate_path.read_text()
    reference_text = reference_path.read_text()

    benchmark_profile = profiles.load_benchmark_profile(benchmark_id) if benchmark_id else {}
    api_markers, callback_markers, teardown_markers = _profile_markers(benchmark_profile)

    candidate_functions = _function_names(candidate_text)
    reference_functions = _function_names(reference_text)
    candidate_api_hits = _marker_presence(candidate_text, api_markers)
    reference_api_hits = _marker_presence(reference_text, api_markers)
    candidate_callback_hits = _marker_presence(candidate_text, callback_markers)
    reference_callback_hits = _marker_presence(reference_text, callback_markers)
    official_api_count = len(reference_api_hits)
    official_callback_count = len(reference_callback_hits)

    payload = {
        "schema_version": 1,
        "artifact_type": "difference-metrics",
        "benchmark_id": benchmark_id,
        "candidate": {
            "path": str(candidate_path.resolve()),
            "loc": _code_loc(candidate_text),
            "function_count": len(candidate_functions),
            "unsafe_sites": _count_regex(_strip_rust_noncode(candidate_text), r"\bunsafe\b"),
            "bindings_direct_uses": _count_regex(_strip_rust_noncode(candidate_text), r"\bbindings::"),
            "raw_pointer_sites": _count_regex(_strip_rust_noncode(candidate_text), r"\*(?:mut|const)\s"),
            "owned_resource_kinds": len(_struct_field_types(candidate_text)),
            "state_flags_count": _state_flag_count(candidate_text),
            "entrypoint_count": len(candidate_callback_hits) if callback_markers else len(candidate_functions),
            "teardown_edges_count": _teardown_edge_count(candidate_text, teardown_markers),
            "lifecycle_transition_count": _lifecycle_transition_count(candidate_functions, candidate_callback_hits),
            "api_marker_hits": candidate_api_hits,
            "callback_marker_hits": candidate_callback_hits,
        },
        "reference": {
            "path": str(reference_path.resolve()),
            "loc": _code_loc(reference_text),
            "function_count": len(reference_functions),
            "unsafe_sites": _count_regex(_strip_rust_noncode(reference_text), r"\bunsafe\b"),
            "bindings_direct_uses": _count_regex(_strip_rust_noncode(reference_text), r"\bbindings::"),
            "raw_pointer_sites": _count_regex(_strip_rust_noncode(reference_text), r"\*(?:mut|const)\s"),
            "owned_resource_kinds": len(_struct_field_types(reference_text)),
            "state_flags_count": _state_flag_count(reference_text),
            "entrypoint_count": len(reference_callback_hits) if callback_markers else len(reference_functions),
            "teardown_edges_count": _teardown_edge_count(reference_text, teardown_markers),
            "lifecycle_transition_count": _lifecycle_transition_count(reference_functions, reference_callback_hits),
            "api_marker_hits": reference_api_hits,
            "callback_marker_hits": reference_callback_hits,
        },
        "api_fit": {
            "markers": api_markers,
            "official_markers_present": reference_api_hits,
            "candidate_markers_present": candidate_api_hits,
            "api_hit_rate": (len(candidate_api_hits) / official_api_count) if official_api_count else None,
        },
        "callback_fit": {
            "markers": callback_markers,
            "official_markers_present": reference_callback_hits,
            "candidate_markers_present": candidate_callback_hits,
            "callback_coverage_rate": (
                len(candidate_callback_hits) / official_callback_count if official_callback_count else None
            ),
        },
        "size": {
            "size_ratio": (
                _code_loc(candidate_text) / _code_loc(reference_text) if _code_loc(reference_text) else None
            ),
            "function_ratio": (
                len(candidate_functions) / len(reference_functions) if reference_functions else None
            ),
        },
        "deltas": {
            "unsafe_sites": _count_regex(_strip_rust_noncode(candidate_text), r"\bunsafe\b")
            - _count_regex(_strip_rust_noncode(reference_text), r"\bunsafe\b"),
            "bindings_direct_uses": _count_regex(_strip_rust_noncode(candidate_text), r"\bbindings::")
            - _count_regex(_strip_rust_noncode(reference_text), r"\bbindings::"),
            "owned_resource_kinds": len(_struct_field_types(candidate_text)) - len(_struct_field_types(reference_text)),
            "state_flags_count": _state_flag_count(candidate_text) - _state_flag_count(reference_text),
            "entrypoint_count": (
                (len(candidate_callback_hits) if callback_markers else len(candidate_functions))
                - (len(reference_callback_hits) if callback_markers else len(reference_functions))
            ),
            "teardown_edges_count": _teardown_edge_count(candidate_text, teardown_markers)
            - _teardown_edge_count(reference_text, teardown_markers),
            "lifecycle_transition_count": _lifecycle_transition_count(candidate_functions, candidate_callback_hits)
            - _lifecycle_transition_count(reference_functions, reference_callback_hits),
        },
    }
    return payload


def write_benchmark_bundle(
    benchmark_id: str,
    *,
    repo_root: str | Path | None = None,
    artifact_root: str | Path | None = None,
    module_path: str | Path | None = None,
    profile_id: str | None = None,
    source_tree: str | Path | None = None,
    output_dir: str | Path | None = None,
    reference_tree: str | Path | None = None,
    official_reference: str | Path | None = None,
    official_reference_initial: str | Path | None = None,
    build_dir_c: str | Path | None = None,
    build_dir_rust: str | Path | None = None,
) -> dict:
    manifest = build_module_manifest(
        benchmark_id,
        repo_root=repo_root,
        artifact_root=artifact_root,
        module_path=module_path,
        profile_id=profile_id,
        source_tree=source_tree,
        output_dir=output_dir,
        reference_tree=reference_tree,
        official_reference=official_reference,
        official_reference_initial=official_reference_initial,
        build_dir_c=build_dir_c,
        build_dir_rust=build_dir_rust,
    )
    contract = build_planner_contract(manifest)
    benchmark_dir = Path(manifest["benchmark_artifacts"]["module_manifest"]).parent
    benchmark_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = Path(manifest["benchmark_artifacts"]["module_manifest"])
    contract_path = Path(manifest["benchmark_artifacts"]["planner_contract"])
    _write_json(manifest_path, manifest)
    _write_json(contract_path, contract)
    summary = build_validator_ready_summary(manifest_path)
    provenance_update = record_provenance_event(
        manifest_path=manifest_path,
        kind="benchmark-bootstrap",
        actor="tool",
        command="bootstrap-benchmark",
        paths=[
            str(manifest_path.resolve()),
            str(contract_path.resolve()),
            str(Path(summary["output_path"]).resolve()),
        ],
        note="Benchmark manifest, planner contract, and initial validator summary were generated.",
    )

    payload = {
        "schema_version": 1,
        "artifact_type": "benchmark-bootstrap-manifest",
        "benchmark_id": benchmark_id,
        "module_id": manifest["module_id"],
        "generated_artifacts": [
            _rel(_repo_root(repo_root), manifest_path),
            _rel(_repo_root(repo_root), contract_path),
            _rel(_repo_root(repo_root), Path(summary["output_path"])),
            _rel(_repo_root(repo_root), Path(provenance_update["log_path"])),
            _rel(_repo_root(repo_root), Path(provenance_update["log_text_path"])),
        ],
        "benchmark_artifact_root": _rel(_repo_root(repo_root), benchmark_dir),
    }
    return payload


def _weakness_entry(category: str, status: str, summary: str, evidence: list[str]) -> dict:
    return {
        "category": category,
        "status": status,
        "summary": summary,
        "evidence": evidence,
    }


def build_experiment_report(
    manifest_path: str | Path,
    *,
    output: str | Path | None = None,
) -> dict:
    resolved_manifest_path = Path(manifest_path).resolve()
    manifest = json.loads(resolved_manifest_path.read_text())
    summary = build_validator_ready_summary(resolved_manifest_path)
    metrics = _load_json_if_exists(_resolve_manifest_artifact_path(manifest, manifest["benchmark_artifacts"]["difference_metrics"]))
    provenance = _provenance_summary(manifest)
    translation_plan = _load_json_if_exists(
        _resolve_manifest_artifact_path(manifest, manifest["planning_artifacts"]["translation_plan"])
    )

    runtime_assessment = summary["runtime_assessment"]
    compare_unlocked = (
        (not manifest["blind_policy"].get("freeze_before_compare", True) or provenance["freeze_status"] == "frozen")
        and provenance["contamination_status"] == "clean"
    )

    weakness_matrix = [
        _weakness_entry(
            "blindness gap",
            "issue" if provenance["contamination_status"] != "clean" else "pass",
            (
                "Reference contamination was recorded before compare."
                if provenance["contamination_status"] != "clean"
                else "No contamination event is currently recorded in provenance."
            ),
            [provenance["log_path"]],
        ),
        _weakness_entry(
            "planner/profile gap",
            "issue"
            if translation_plan and not translation_plan["readiness"]["ready_for_minimal_driver_codegen"]
            else "pass",
            (
                "The strict planner artifacts still block minimal driver codegen."
                if translation_plan and not translation_plan["readiness"]["ready_for_minimal_driver_codegen"]
                else "The strict planner artifacts permit minimal driver codegen."
            ),
            [manifest["planning_artifacts"]["translation_plan"]],
        ),
        _weakness_entry(
            "codegen gap",
            "issue"
            if summary["checks"]["safety_pass"] is False
            or summary["checks"]["compile_pass"] is False
            or summary["checks"]["package_pass"] is False
            or (metrics is not None and metrics["candidate"]["unsafe_sites"] > 0)
            or (metrics is not None and metrics["candidate"]["bindings_direct_uses"] > 0)
            else ("pending" if metrics is None else "pass"),
            (
                "Candidate still violates safety/compile or introduces disallowed low-level patterns."
                if summary["checks"]["safety_pass"] is False
                or summary["checks"]["compile_pass"] is False
                or summary["checks"]["package_pass"] is False
                or (metrics is not None and metrics["candidate"]["unsafe_sites"] > 0)
                or (metrics is not None and metrics["candidate"]["bindings_direct_uses"] > 0)
                else ("Difference metrics are not available yet." if metrics is None else "Candidate stays within the expected zero-unsafe / zero-bindings envelope.")
            ),
            [
                manifest["planning_artifacts"]["safety_verdict"],
                manifest["planning_artifacts"]["agent_gate_report"],
                manifest["benchmark_artifacts"]["difference_metrics"],
            ],
        ),
        _weakness_entry(
            "oracle gap",
            "issue"
            if runtime_assessment["status"] in {"runner_unavailable", "environment_blocked", "candidate_failed", "not_run"}
            else "pass",
            (
                "Oracle evidence is not yet reliable enough to support the requested runtime claim."
                if runtime_assessment["status"] in {"runner_unavailable", "environment_blocked", "candidate_failed", "not_run"}
                else "Oracle evidence reached the current runtime gate."
            ),
            [
                manifest["planning_artifacts"]["toolchain_run_record"],
                manifest["benchmark_artifacts"]["lifecycle_oracle_run_record"],
                manifest["benchmark_artifacts"]["differential_oracle_run_record"],
            ],
        ),
        _weakness_entry(
            "benchmark gap",
            "issue"
            if runtime_assessment.get("primary_runner") and runtime_assessment.get("primary_runner_available") is False
            else ("warning" if runtime_assessment.get("fallback_runner_used") else "pass"),
            (
                "The benchmark profile requests a primary runner that is not currently implemented in the tool."
                if runtime_assessment.get("primary_runner") and runtime_assessment.get("primary_runner_available") is False
                else (
                    "The benchmark succeeded only by using the fallback runner."
                    if runtime_assessment.get("fallback_runner_used")
                    else "The benchmark runner setup matches the declared plan."
                )
            ),
            [manifest["benchmark_artifacts"]["module_manifest"]],
        ),
        _weakness_entry(
            "operator-effort gap",
            "warning" if provenance["event_count"] else "issue",
            (
                "The experiment still depends on explicit provenance logging and manual expert-delta completion."
                if provenance["event_count"]
                else "No provenance activity was recorded; the operator protocol is incomplete."
            ),
            [provenance["log_path"]],
        ),
    ]

    payload = {
        "schema_version": 1,
        "artifact_type": "benchmark-experiment-report",
        "benchmark_id": manifest["benchmark_id"],
        "module_id": manifest["module_id"],
        "contamination_status": provenance["contamination_status"],
        "freeze_status": provenance["freeze_status"],
        "compare_unlocked": compare_unlocked,
        "runtime_assessment": runtime_assessment,
        "weakness_matrix": weakness_matrix,
        "expert_delta": {
            "more_convoluted_than_expert": "",
            "profile_hint_dependency": "",
            "boundary_leaks": "",
            "oracle_vs_driver_limitations": "",
        },
        "evidence_refs": {
            "module_manifest": str(resolved_manifest_path),
            "validator_ready_summary": summary["output_path"],
            "difference_metrics": str(
                _resolve_manifest_artifact_path(manifest, manifest["benchmark_artifacts"]["difference_metrics"]).resolve()
            ),
            "provenance_log": provenance["log_path"],
            "provenance_log_text": provenance["log_text_path"],
        },
    }
    output_path = Path(output) if output else resolved_manifest_path.parent / "experiment-report.json"
    _write_json(output_path, payload)
    payload["output_path"] = str(output_path.resolve())
    return payload


def _portfolio_row(manifest: dict, summary: dict | None, metrics: dict | None, manifest_path: Path) -> dict:
    candidate = (metrics or {}).get("candidate", {})
    reference = (metrics or {}).get("reference", {})
    api_fit = (metrics or {}).get("api_fit", {})
    callback_fit = (metrics or {}).get("callback_fit", {})
    checks = (summary or {}).get("checks", {})
    return {
        "benchmark_id": manifest["benchmark_id"],
        "module_id": manifest["module_id"],
        "benchmark_track": manifest["benchmark_track"],
        "sample_tier": manifest["sample_tier"],
        "scope": manifest["scope"],
        "quota_class": manifest["quota_class"],
        "conversion_stage": (summary or {}).get("conversion_stage", manifest.get("conversion_stage")),
        "style_alignment": (summary or {}).get("style_alignment", manifest.get("style_alignment")),
        "runtime_state": (summary or {}).get("runtime_state", manifest.get("runtime_state")),
        "target_runtime_state": manifest["target_runtime_state"],
        "safety_pass": checks.get("safety_pass"),
        "ready_for_smoke": checks.get("ready_for_smoke"),
        "rust_toolchain_available": checks.get("rust_toolchain_available"),
        "compile_pass": checks.get("compile_pass"),
        "package_pass": checks.get("package_pass"),
        "toolchain_run_pass": checks.get("toolchain_run_pass"),
        "lifecycle_run_pass": checks.get("lifecycle_run_pass"),
        "differential_run_pass": checks.get("differential_run_pass"),
        "api_hit_rate": api_fit.get("api_hit_rate"),
        "callback_coverage_rate": callback_fit.get("callback_coverage_rate"),
        "candidate_unsafe_sites": candidate.get("unsafe_sites"),
        "reference_unsafe_sites": reference.get("unsafe_sites"),
        "candidate_bindings_direct_uses": candidate.get("bindings_direct_uses"),
        "reference_bindings_direct_uses": reference.get("bindings_direct_uses"),
        "size_ratio": (metrics or {}).get("size", {}).get("size_ratio"),
        "claim_ceiling": (summary or {}).get("claims", {}).get("current_claim_ceiling", manifest["claim_ceiling"]),
        "manifest_path": str(manifest_path.resolve()),
    }


def collect_benchmark_portfolio(
    roots: list[str | Path] | None,
    *,
    output: str | Path,
    csv_output: str | Path,
) -> dict:
    root_paths = [Path(root).resolve() for root in roots] if roots else [(REPO_ROOT / DEFAULT_BENCHMARK_ROOT).resolve()]
    manifests: list[Path] = []
    for root in root_paths:
        manifests.extend(sorted(root.rglob("module-manifest.json")))

    rows = []
    for manifest_path in manifests:
        manifest = json.loads(manifest_path.read_text())
        summary_path = manifest_path.parent / "validator-ready-summary.json"
        metrics_path = manifest_path.parent / "difference-metrics.json"
        summary = _load_json_if_exists(summary_path)
        metrics = _load_json_if_exists(metrics_path)
        rows.append(_portfolio_row(manifest, summary, metrics, manifest_path))

    runtime_state_counts: dict[str, int] = {}
    for row in rows:
        runtime_state = row["runtime_state"] or "unknown"
        runtime_state_counts[runtime_state] = runtime_state_counts.get(runtime_state, 0) + 1

    payload = {
        "schema_version": 1,
        "artifact_type": "benchmark-portfolio",
        "benchmarks": rows,
        "summary": {
            "total_benchmarks": len(rows),
            "runtime_state_counts": runtime_state_counts,
        },
    }

    _write_json(output, payload)
    csv_path = Path(csv_output)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "benchmark_id",
        "module_id",
        "benchmark_track",
        "sample_tier",
        "scope",
        "quota_class",
        "conversion_stage",
        "style_alignment",
        "runtime_state",
        "target_runtime_state",
        "safety_pass",
        "ready_for_smoke",
        "rust_toolchain_available",
        "compile_pass",
        "package_pass",
        "toolchain_run_pass",
        "lifecycle_run_pass",
        "differential_run_pass",
        "api_hit_rate",
        "callback_coverage_rate",
        "candidate_unsafe_sites",
        "reference_unsafe_sites",
        "candidate_bindings_direct_uses",
        "reference_bindings_direct_uses",
        "size_ratio",
        "claim_ceiling",
        "manifest_path",
    ]
    with csv_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    payload["output_path"] = str(Path(output).resolve())
    payload["csv_output_path"] = str(csv_path.resolve())
    return payload
