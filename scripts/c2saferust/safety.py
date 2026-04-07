#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

from __future__ import annotations

from collections import Counter
import shutil
import subprocess
import tempfile
from pathlib import Path

import intake


REPO_ROOT = Path(__file__).resolve().parents[2]
VERIFIER_SOURCE = Path(__file__).with_name("safety_verifier.rs")
VERIFIER_BINARY = Path("/tmp/c2saferust-safety-verifier")


_VIOLATION_KIND_DEFAULTS = {
    "unaccounted-unsafe-site": {
        "classification": "artifact_drift",
        "root_cause": "proof-site-present-in-code-but-missing-from-soundness-discharge",
        "prevention_rule": "Refresh soundness-discharge.json after abstraction proof-site edits.",
    },
    "stale-proof-site": {
        "classification": "artifact_drift",
        "root_cause": "soundness-discharge-still-references-a-removed-or-shifted-proof-site",
        "prevention_rule": "Recompute proof-site locations whenever abstraction unsafe blocks move.",
    },
    "undischarged-proof-site": {
        "classification": "missing_soundness_rule",
        "root_cause": "proof-site-detected-but-left-blocked-in-soundness-discharge",
        "prevention_rule": "Discharge or explicitly reject each abstraction proof-site before oracle execution.",
    },
    "blocked-structural-rule": {
        "classification": "missing_soundness_rule",
        "root_cause": "required-structural-soundness-rule-is-blocked",
        "prevention_rule": "Keep structural soundness rules synchronized with the abstraction surface.",
    },
    "missing-structural-pattern": {
        "classification": "missing_soundness_rule",
        "root_cause": "required-soundness-guard-pattern-is-absent",
        "prevention_rule": "Template the required guard pattern into the abstraction before driver codegen.",
    },
    "forbidden-structural-pattern": {
        "classification": "real_unsoundness_risk",
        "root_cause": "abstraction-or-driver-contains-a-pattern-explicitly-forbidden-by-soundness-policy",
        "prevention_rule": "Reject the build until the forbidden pattern is removed or the rule is re-justified.",
    },
    "driver-forbidden-token": {
        "classification": "real_unsoundness_risk",
        "root_cause": "driver-crossed-the-no-unsafe-or-no-bindings-policy-boundary",
        "prevention_rule": "Enforce `#![forbid(unsafe_code)]` and zero direct `bindings::` usage in generated drivers.",
    },
    "missing-driver-file": {
        "classification": "real_unsoundness_risk",
        "root_cause": "driver-rust-landing-file-is-missing",
        "prevention_rule": "Do not claim driver readiness until the landing Rust file exists and is wired into the profile.",
    },
    "rust-verifier-error": {
        "classification": "artifact_drift",
        "root_cause": "safety-verifier-infrastructure-failed-before-a-stable-verdict-could-be-produced",
        "prevention_rule": "Keep the verifier binary and its config format versioned with the artifact schema.",
    },
}


def _repo_root(repo_root: str | Path | None) -> Path:
    return Path(repo_root).resolve() if repo_root else REPO_ROOT


def _path_from_repo(repo_root: Path, path_like: str | Path) -> Path:
    path = Path(path_like)
    return path if path.is_absolute() else (repo_root / path)


def _find_token_lines(text: str, token: str) -> list[int]:
    stripped = intake._strip_rust_noncode(text)
    line_numbers = []
    for line_number, line in enumerate(stripped.splitlines(), start=1):
        if token == "unsafe":
            import re

            if re.search(r"\bunsafe\b", line):
                line_numbers.append(line_number)
        elif token in line:
            line_numbers.append(line_number)
    return line_numbers


def _compile_verifier() -> Path:
    rustc = shutil.which("rustc")
    if rustc is None:
        raise RuntimeError("`rustc` is not available; cannot run the Rust safety verifier")

    if VERIFIER_BINARY.exists() and VERIFIER_BINARY.stat().st_mtime >= VERIFIER_SOURCE.stat().st_mtime:
        return VERIFIER_BINARY

    subprocess.run(
        [rustc, "--edition=2021", str(VERIFIER_SOURCE), "-O", "-o", str(VERIFIER_BINARY)],
        check=True,
        capture_output=True,
        text=True,
    )
    return VERIFIER_BINARY


def _write_verifier_config(
    repo_root: Path,
    policy: dict,
    discharge: dict,
) -> Path:
    config_path = Path(tempfile.mkstemp(prefix="c2saferust-safety-", suffix=".cfg")[1])
    lines: list[str] = []

    lines.append(f"DRIVER_FILE\t{policy['driver_rust_path']}")
    for token in policy["driver_policy"]["forbidden_tokens"]:
        lines.append(f"DRIVER_FORBIDDEN\t{token}")
    for relative_path in policy["abstraction_policy"]["allowlisted_files"]:
        lines.append(f"ALLOW_UNSAFE_FILE\t{relative_path}")
    for proof_site in discharge["proof_sites"]:
        lines.append(
            "\t".join(
                [
                    "PROOF_SITE",
                    proof_site["file"],
                    str(proof_site["line"]),
                    proof_site["unsafe_kind"],
                    proof_site["status"],
                    proof_site["id"],
                ]
            )
        )
    for rule in discharge["structural_rules"]:
        lines.append(
            "\t".join(
                [
                    "STRUCTURAL_RULE",
                    rule["id"],
                    rule["status"],
                    rule["file"],
                    "\x1f".join(rule["must_contain"]),
                    "\x1f".join(rule["must_not_contain"]),
                ]
            )
        )

    config_path.write_text("\n".join(lines) + "\n")
    return config_path


def _classify_owner_scope(file_path: str | None, driver_rust_path: str | None) -> str:
    if file_path and driver_rust_path and file_path == driver_rust_path:
        return "driver"
    return "abstraction"


def _classify_violation(violation: dict, *, driver_rust_path: str | None) -> dict:
    file_path = violation.get("file")
    kind = violation.get("kind", "unknown-violation")
    owner_scope = _classify_owner_scope(file_path, driver_rust_path)
    defaults = dict(
        _VIOLATION_KIND_DEFAULTS.get(
            kind,
            {
                "classification": "real_unsoundness_risk",
                "root_cause": "unclassified-violation-kind",
                "prevention_rule": "Extend the violation classifier before treating this verdict as stable.",
            },
        )
    )

    if owner_scope == "driver" and defaults["classification"] == "missing_soundness_rule":
        defaults["classification"] = "real_unsoundness_risk"

    location = file_path or "<unknown-file>"
    line = violation.get("line")
    line_suffix = str(line) if line is not None else "no-line"
    return {
        "violation_id": f"{kind}:{location}:{line_suffix}",
        "kind": kind,
        "file": file_path,
        "line": line,
        "message": violation.get("message"),
        "owner_scope": owner_scope,
        "classification": defaults["classification"],
        "root_cause": defaults["root_cause"],
        "prevention_rule": defaults["prevention_rule"],
        "status": "open",
    }


def build_safety_violation_ledger(
    verdict: dict,
    *,
    verdict_artifact: str | Path | None = None,
) -> dict:
    driver_rust_path = verdict.get("driver_rust_path")
    entries = [
        _classify_violation(violation, driver_rust_path=driver_rust_path)
        for violation in verdict.get("violations", [])
    ]
    return {
        "schema_version": 1,
        "artifact_type": "safety-violation-ledger",
        "module_id": verdict["module_id"],
        "driver_rust_path": driver_rust_path,
        "verdict_artifact": str(verdict_artifact) if verdict_artifact is not None else None,
        "violations": entries,
        "summary": {
            "total_violations": len(entries),
            "by_kind": dict(Counter(entry["kind"] for entry in entries)),
            "by_classification": dict(Counter(entry["classification"] for entry in entries)),
            "by_owner_scope": dict(Counter(entry["owner_scope"] for entry in entries)),
            "unclassified": sum(1 for entry in entries if entry["root_cause"] == "unclassified-violation-kind"),
        },
    }


def write_violation_ledger(path: str | Path, ledger: dict) -> None:
    intake.write_json(path, ledger)


def verify_module_safety(
    module_path: str | Path | None = None,
    *,
    repo_root: str | Path | None = None,
    artifact_root: str | Path | None = None,
    profile_id: str | None = None,
    source_tree: str | Path | None = None,
) -> dict:
    context = intake.resolve_module_context(
        module_path,
        repo_root=repo_root,
        profile_id=profile_id,
        source_tree=source_tree,
    )
    repo = context["repo_root"]
    policy = intake.build_safety_policy(
        module_path,
        repo_root=repo,
        artifact_root=artifact_root,
        profile_id=context["profile_id"],
        source_tree=source_tree,
    )
    discharge = intake.build_soundness_discharge(
        module_path,
        repo_root=repo,
        artifact_root=artifact_root,
        profile_id=context["profile_id"],
        source_tree=source_tree,
    )
    module_id = context["module_id"]

    quick_violations = []
    driver_path = _path_from_repo(repo, policy["driver_rust_path"])
    if not driver_path.exists():
        quick_violations.append(
            {
                "kind": "missing-driver-file",
                "file": policy["driver_rust_path"],
                "line": None,
                "message": "Driver Rust file does not exist.",
            }
        )
    else:
        driver_text = driver_path.read_text()
        for token in policy["driver_policy"]["forbidden_tokens"]:
            for line_number in _find_token_lines(driver_text, token):
                quick_violations.append(
                    {
                        "kind": "driver-forbidden-token",
                        "file": policy["driver_rust_path"],
                        "line": line_number,
                        "message": f"Driver code contains forbidden token `{token}`.",
                    }
                )

    rust_verifier_findings = []
    rust_verifier_error = None
    try:
        verifier = _compile_verifier()
        config_path = _write_verifier_config(repo, policy, discharge)
        completed = subprocess.run(
            [str(verifier), "--repo-root", str(repo), "--config", str(config_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode not in (0, 1):
            rust_verifier_error = completed.stderr.strip() or completed.stdout.strip()
        else:
            for line in completed.stdout.splitlines():
                if not line.startswith("VIOLATION\t"):
                    continue
                _, kind, file_path, line_number, message = line.split("\t", 4)
                rust_verifier_findings.append(
                    {
                        "kind": kind,
                        "file": file_path,
                        "line": int(line_number) if line_number != "-" else None,
                        "message": message,
                    }
                )
    except Exception as exc:  # pragma: no cover - exercised in environments without rustc
        rust_verifier_error = str(exc)

    if rust_verifier_error:
        rust_verifier_findings.append(
            {
                "kind": "rust-verifier-error",
                "file": str(VERIFIER_SOURCE),
                "line": None,
                "message": rust_verifier_error,
            }
        )

    all_violations = quick_violations + rust_verifier_findings

    ledger = build_safety_violation_ledger(
        {
            "module_id": module_id,
            "driver_rust_path": policy["driver_rust_path"],
            "pass": not all_violations,
            "violations": all_violations,
        }
    )

    return {
        "schema_version": 1,
        "artifact_type": "safety-verdict",
        "module_id": module_id,
        "driver_rust_path": policy["driver_rust_path"],
        "policy_artifact": intake._artifact_path_for(
            module_id,
            "safety-policy.json",
            repo_root=repo,
            artifact_root=artifact_root,
        ),
        "discharge_artifact": intake._artifact_path_for(
            module_id,
            "soundness-discharge.json",
            repo_root=repo,
            artifact_root=artifact_root,
        ),
        "pass": not all_violations,
        "violations": all_violations,
        "violation_ledger": ledger,
        "summary": {
            "quick_gate_violations": len(quick_violations),
            "rust_verifier_violations": len(rust_verifier_findings),
            "total_violations": len(all_violations),
            "by_classification": ledger["summary"]["by_classification"],
            "by_owner_scope": ledger["summary"]["by_owner_scope"],
        },
    }


def write_verdict(path: str | Path, verdict: dict) -> None:
    intake.write_json(path, verdict)
