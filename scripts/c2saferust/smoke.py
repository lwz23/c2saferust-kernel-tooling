#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

from __future__ import annotations

import os
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import intake
import oracle_runners
import profiles
import safety
import semantics


REPO_ROOT = Path(__file__).resolve().parents[2]
LOCAL_LLVM_ROOT = Path("/tmp/llvm-15-local/usr")
EVIDENCE_TIER_ORDER = {
    "env_blocked": 0,
    "env_ready": 1,
    "device_present": 2,
    "baseline_abi_pass": 3,
    "differential_pass": 4,
}


def _repo_root(repo_root: str | Path | None) -> Path:
    return Path(repo_root).resolve() if repo_root else REPO_ROOT


def _artifact_output(
    repo_root: Path,
    module_id: str,
    filename: str,
    output: str | Path | None = None,
    *,
    artifact_root: str | Path | None = None,
) -> Path:
    if output is not None:
        return Path(output)
    return intake._path_from_repo(
        repo_root,
        intake._artifact_path_for(
            module_id,
            filename,
            repo_root=repo_root,
            artifact_root=artifact_root,
        ),
    )


def _detect_build_env() -> tuple[dict[str, str], dict]:
    env = dict(os.environ)
    profile = {
        "source": "inherited-environment",
        "make_llvm": None,
        "path_prefix": None,
        "libclang_path": env.get("LIBCLANG_PATH"),
        "ld_library_path_prefix": None,
    }

    clang15 = LOCAL_LLVM_ROOT / "bin" / "clang-15"
    libclang_dir = LOCAL_LLVM_ROOT / "lib" / "x86_64-linux-gnu"
    if clang15.exists():
        env["PATH"] = f"{LOCAL_LLVM_ROOT / 'bin'}:{env.get('PATH', '')}"
        env.setdefault("LIBCLANG_PATH", str(libclang_dir))
        current_ld = env.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = (
            f"{libclang_dir}:{current_ld}" if current_ld else str(libclang_dir)
        )
        profile.update(
            {
                "source": "auto-detected-/tmp/llvm-15-local",
                "make_llvm": "-15",
                "path_prefix": str(LOCAL_LLVM_ROOT / "bin"),
                "libclang_path": env["LIBCLANG_PATH"],
                "ld_library_path_prefix": str(libclang_dir),
            }
        )
    return env, profile


def _is_configured_build_dir(path: Path) -> bool:
    return (path / ".config").exists()


def _resolve_build_dir(repo_root: Path, module_id: str, build_dir: str | Path | None) -> tuple[Path, str]:
    if build_dir is not None:
        return Path(build_dir), "user-specified"
    candidates = [
        (Path(f"/tmp/{module_id}-build"), "auto-detected-module-build-dir"),
        (Path(f"/tmp/c2saferust-{module_id}-build"), "default-c2saferust-build-dir"),
        (repo_root, "in-tree-build"),
    ]
    for candidate, source in candidates:
        if _is_configured_build_dir(candidate):
            return candidate, source
    return Path(f"/tmp/c2saferust-{module_id}-build"), "default-c2saferust-build-dir"


def _ensure_kernel_image(
    repo_root: Path,
    build_dir: Path,
    *,
    make_llvm: str | None = None,
) -> tuple[Path, dict]:
    image = build_dir / "arch" / "x86" / "boot" / "bzImage"
    env, env_profile = _detect_build_env()
    llvm_arg = make_llvm if make_llvm is not None else env_profile["make_llvm"]

    if image.exists():
        return image, {
            "status": "already_built",
            "image": str(image),
            "env_profile": env_profile,
            "command": None,
            "exit_code": 0,
        }

    command = ["make", f"O={build_dir}"]
    if llvm_arg is not None:
        command.append(f"LLVM={llvm_arg}")
    command.extend([f"-j{os.cpu_count() or 1}", "bzImage"])

    completed = subprocess.run(
        command,
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    result = {
        "status": "built" if completed.returncode == 0 else "failed",
        "image": str(image),
        "env_profile": env_profile,
        "command": " ".join(command),
        "exit_code": completed.returncode,
        "stdout_tail": completed.stdout.splitlines()[-60:],
        "stderr_tail": completed.stderr.splitlines()[-60:],
    }
    if completed.returncode != 0 or not image.exists():
        raise RuntimeError("Failed to build `bzImage` for oracle run.")
    return image, result


def _render_scenario_inputs(module_path: str | Path, module_profile: dict, translation_plan: dict) -> dict:
    scenario_inputs = dict(module_profile["oracle"]["scenario_inputs"])
    rendered = {
        "module_id": translation_plan["module_id"],
        "driver_rust_path": translation_plan["driver_rust_path"],
    }
    rendered.update(scenario_inputs)
    for key, value in list(scenario_inputs.items()):
        if key.endswith("_template") and isinstance(value, str):
            rendered[key.removesuffix("_template")] = value.format(**rendered)
    return rendered


def _resolve_runner_id(module_profile: dict, scenario: dict) -> str:
    oracle_profile = module_profile.get("oracle", {})
    runner_id = (
        oracle_profile.get("runner_id")
        or oracle_profile.get("runner")
        or scenario.get("runner_id")
        or scenario.get("runner")
    )
    if not runner_id:
        raise ValueError("Module/scenario profile did not resolve an oracle runner id.")
    return runner_id


def _extract_run_log_path(run_record: dict) -> Path | None:
    runner_log = run_record.get("runner", {}).get("log_path")
    if runner_log:
        return Path(runner_log)
    qemu_log_ref = run_record.get("qemu", {}).get("log_path")
    return Path(qemu_log_ref) if qemu_log_ref else None


def _write_init_script(root: Path, scenario: dict, scenario_inputs: dict) -> None:
    format_context = dict(scenario_inputs)
    format_context["log_prefix"] = scenario["log_prefix"]
    init_text = "\n".join(line.format(**format_context) for line in scenario["script_lines"]) + "\n"
    (root / "init").write_text(init_text)
    (root / "init").chmod(0o755)


def _build_initramfs(scenario: dict, scenario_inputs: dict) -> tuple[Path, dict]:
    busybox = shutil.which("busybox")
    cpio = shutil.which("cpio")
    if busybox is None or cpio is None:
        raise RuntimeError("`busybox` and `cpio` are required for the QEMU oracle.")

    root = Path(tempfile.mkdtemp(prefix="c2saferust-smoke-rootfs-"))
    for relative in ["bin", "sbin", "proc", "sys", "dev", "etc", "tmp", "run"]:
        (root / relative).mkdir(parents=True, exist_ok=True)

    shutil.copy2(busybox, root / "bin" / "busybox")
    for app in [
        "sh",
        "mount",
        "mkdir",
        "mknod",
        "cat",
        "echo",
        "uname",
        "ip",
        "poweroff",
        "reboot",
        "sleep",
        "dmesg",
        "ls",
        "grep",
        "sed",
        "awk",
        "ifconfig",
        "tail",
    ]:
        (root / "bin" / app).symlink_to("/bin/busybox")
    (root / "sbin" / "modprobe").symlink_to("/bin/busybox")
    (root / "sbin" / "insmod").symlink_to("/bin/busybox")

    _write_init_script(root, scenario, scenario_inputs)

    initramfs_path = Path(tempfile.mkstemp(prefix="c2saferust-smoke-", suffix=".cpio")[1])
    completed = subprocess.run(
        f"cd {root} && find . -print0 | cpio --null -o --format=newc > {initramfs_path}",
        shell=True,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError("Failed to build the oracle initramfs.")

    return initramfs_path, {
        "rootfs_dir": str(root),
        "initramfs_path": str(initramfs_path),
        "cpio_stdout_tail": completed.stdout.splitlines()[-20:],
        "cpio_stderr_tail": completed.stderr.splitlines()[-20:],
    }


def _run_qemu(kernel_image: Path, initramfs: Path, qemu_log: Path, *, timeout_sec: int) -> dict:
    qemu = shutil.which("qemu-system-x86_64")
    if qemu is None:
        raise RuntimeError("`qemu-system-x86_64` is required for the oracle.")

    command = [
        qemu,
        "-nodefaults",
        "-no-reboot",
        "-nographic",
        "-machine",
        "q35,accel=tcg",
        "-m",
        "1024",
        "-smp",
        "2",
        "-kernel",
        str(kernel_image),
        "-initrd",
        str(initramfs),
        "-append",
        "console=ttyS0 rdinit=/init nokaslr panic=-1",
        "-serial",
        "stdio",
    ]

    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout_sec,
    )
    qemu_log.write_text(completed.stdout + ("\n[stderr]\n" + completed.stderr if completed.stderr else ""))
    return {
        "command": command,
        "exit_code": completed.returncode,
        "stdout_tail": completed.stdout.splitlines()[-120:],
        "stderr_tail": completed.stderr.splitlines()[-80:],
    }


def _extract_scenario_summary(qemu_log_text: str, scenario: dict) -> dict:
    prefix = scenario["log_prefix"]

    def find_rc(name: str) -> int | None:
        match = re.search(rf"\[{re.escape(prefix)}\] {re.escape(name)}=(-?\d+)", qemu_log_text)
        return int(match.group(1)) if match else None

    summary = {
        "result": "PASS" if scenario["result_success_marker"] in qemu_log_text else "FAIL",
    }
    for field in scenario["summary_fields"]:
        summary[field] = find_rc(field)
    return summary


def _write_safety_verdict(
    repo_root: Path,
    module_id: str,
    verdict: dict,
    *,
    artifact_root: str | Path | None = None,
) -> Path:
    verdict_path = _artifact_output(
        repo_root,
        module_id,
        "safety-verdict.json",
        artifact_root=artifact_root,
    )
    ledger_path = _artifact_output(
        repo_root,
        module_id,
        "safety-violation-ledger.json",
        artifact_root=artifact_root,
    )
    verdict["violation_ledger_artifact"] = str(ledger_path)
    if "violation_ledger" not in verdict:
        verdict["violation_ledger"] = safety.build_safety_violation_ledger(
            {
                "module_id": verdict.get("module_id", module_id),
                "driver_rust_path": verdict.get("driver_rust_path"),
                "violations": verdict.get("violations", []),
            },
            verdict_artifact=verdict_path,
        )
    safety.write_verdict(verdict_path, verdict)
    safety.write_violation_ledger(ledger_path, verdict["violation_ledger"])
    return verdict_path


def _write_command_semantics_verdict(
    repo_root: Path,
    module_id: str,
    verdict: dict,
    *,
    artifact_root: str | Path | None = None,
) -> Path:
    verdict_path = _artifact_output(
        repo_root,
        module_id,
        "command-semantics-verdict.json",
        artifact_root=artifact_root,
    )
    semantics.write_verdict(verdict_path, verdict)
    return verdict_path


def _blocked_oracle_payload(
    *,
    module_id: str,
    module_c_path: str,
    runner_id: str,
    scenario_id: str,
    scenario_inputs: dict,
    runner: dict,
    steps: list[dict],
    blocker_kind: str,
    blockers: list[str],
    evidence_tier: str,
    safety_artifact: str | None = None,
    command_semantics_artifact: str | None = None,
) -> dict:
    payload = {
        "schema_version": 1,
        "artifact_type": "toolchain-run-record",
        "module_id": module_id,
        "module_c_path": module_c_path,
        "scenario": f"{runner_id}-{scenario_id}",
        "oracle_runner": runner_id,
        "runner_id": runner_id,
        "scenario_id": scenario_id,
        "pass": False,
        "ready_for_oracle": False,
        "blocked": True,
        "evidence_tier": evidence_tier,
        "blocker_kind": blocker_kind,
        "blockers": blockers,
        "scenario_inputs": scenario_inputs,
        "runner": runner,
        "smoke_summary": {
            "result": "BLOCKED",
        },
        "steps": steps,
    }
    if safety_artifact is not None:
        payload["safety_verdict_artifact"] = safety_artifact
    if command_semantics_artifact is not None:
        payload["command_semantics_verdict_artifact"] = command_semantics_artifact
    if "link_kind" in scenario_inputs:
        payload["link_kind"] = scenario_inputs["link_kind"]
    if "device_name" in scenario_inputs:
        payload["device_name"] = scenario_inputs["device_name"]
    if "device_node" in scenario_inputs:
        payload["device_node"] = scenario_inputs["device_node"]
    return payload


def _combine_evidence_tiers(*tiers: str | None) -> str:
    normalized = [tier for tier in tiers if tier in EVIDENCE_TIER_ORDER]
    if not normalized:
        return "env_blocked"
    return min(normalized, key=lambda tier: EVIDENCE_TIER_ORDER[tier])


def run_oracle(
    module_path: str | Path | None = None,
    *,
    repo_root: str | Path | None = None,
    artifact_root: str | Path | None = None,
    profile_id: str | None = None,
    source_tree: str | Path | None = None,
    output: str | Path | None = None,
    qemu_log_output: str | Path | None = None,
    build_dir: str | Path | None = None,
    make_llvm: str | None = None,
    timeout_sec: int = 120,
    runner_context: dict | None = None,
    _skip_safety_gate: bool = False,
    _skip_command_semantics_gate: bool = False,
) -> dict:
    context = intake.resolve_module_context(
        module_path,
        repo_root=repo_root,
        profile_id=profile_id,
        source_tree=source_tree,
    )
    repo = context["repo_root"]
    module_profile = context["profile"]
    translation_plan = intake.build_translation_plan(
        module_path,
        repo_root=repo,
        artifact_root=artifact_root,
        profile_id=module_profile["profile_id"],
        source_tree=source_tree,
    )
    module_id = translation_plan["module_id"]
    scenario = profiles.load_scenario_profile(module_profile["oracle"]["scenario_id"])
    runner_id = _resolve_runner_id(module_profile, scenario)
    scenario_inputs = _render_scenario_inputs(module_path, module_profile, translation_plan)
    safety_verdict = None
    safety_verdict_path = None
    command_semantics_verdict = None
    command_semantics_verdict_path = None
    if not _skip_safety_gate:
        safety_verdict = safety.verify_module_safety(
            module_path,
            repo_root=repo,
            artifact_root=artifact_root,
            profile_id=module_profile["profile_id"],
            source_tree=source_tree,
        )
        safety_verdict_path = _write_safety_verdict(
            repo,
            module_id,
            safety_verdict,
            artifact_root=artifact_root,
        )
    if safety_verdict is not None and not safety_verdict["pass"]:
        payload = _blocked_oracle_payload(
            module_id=module_id,
            module_c_path=translation_plan["module_c_path"],
            runner_id=runner_id,
            scenario_id=scenario["scenario_id"],
            scenario_inputs=scenario_inputs,
            runner={
                "id": runner_id,
                "status": "blocked-before-runner",
            },
            blocker_kind="safety-verification-failed",
            blockers=[
                "verify-safety did not pass; oracle execution is gated until all soundness obligations are discharged."
            ],
            evidence_tier="env_blocked",
            safety_artifact=str(safety_verdict_path),
            steps=[
                {
                    "step_id": "verify-safety",
                    "status": "fail",
                    "details": [
                        f"violations={len(safety_verdict['violations'])}",
                        f"artifact={safety_verdict_path}",
                    ],
                },
                {
                    "step_id": "run-oracle-backend",
                    "status": "blocked",
                    "details": [
                        "blocked-by-safety-gate",
                    ],
                },
            ],
        )
        output_path = _artifact_output(
            repo,
            module_id,
            "toolchain-run-record.json",
            output,
            artifact_root=artifact_root,
        )
        intake.write_json(output_path, payload)
        payload["output_path"] = str(output_path)
        return payload
    if (
        not _skip_command_semantics_gate
        and module_profile.get("command_semantics", {}).get("commands")
    ):
        command_semantics_verdict = semantics.verify_module_command_semantics(
            module_path,
            repo_root=repo,
            artifact_root=artifact_root,
            profile_id=module_profile["profile_id"],
            source_tree=source_tree,
        )
        command_semantics_verdict_path = _write_command_semantics_verdict(
            repo,
            module_id,
            command_semantics_verdict,
            artifact_root=artifact_root,
        )
    if command_semantics_verdict is not None and not command_semantics_verdict["pass"]:
        payload = _blocked_oracle_payload(
            module_id=module_id,
            module_c_path=translation_plan["module_c_path"],
            runner_id=runner_id,
            scenario_id=scenario["scenario_id"],
            scenario_inputs=scenario_inputs,
            runner={
                "id": runner_id,
                "status": "blocked-before-runner",
            },
            blocker_kind="command-semantics-verification-failed",
            blockers=[
                "verify-command-semantics did not pass; oracle execution is gated until command completion semantics are aligned with the artifact."
            ],
            evidence_tier="env_blocked",
            safety_artifact=str(safety_verdict_path) if safety_verdict_path is not None else None,
            command_semantics_artifact=(
                str(command_semantics_verdict_path)
                if command_semantics_verdict_path is not None
                else None
            ),
            steps=[
                *(
                    [
                        {
                            "step_id": "verify-safety",
                            "status": "pass",
                            "details": [
                                f"violations={len(safety_verdict['violations'])}",
                                f"artifact={safety_verdict_path}",
                            ],
                        }
                    ]
                    if safety_verdict is not None and safety_verdict_path is not None
                    else []
                ),
                {
                    "step_id": "verify-command-semantics",
                    "status": "fail",
                    "details": [
                        f"violations={len(command_semantics_verdict['violations'])}",
                        f"artifact={command_semantics_verdict_path}",
                    ],
                },
                {
                    "step_id": "run-oracle-backend",
                    "status": "blocked",
                    "details": [
                        "blocked-by-command-semantics-gate",
                    ],
                },
            ],
        )
        output_path = _artifact_output(
            repo,
            module_id,
            "toolchain-run-record.json",
            output,
            artifact_root=artifact_root,
        )
        intake.write_json(output_path, payload)
        payload["output_path"] = str(output_path)
        return payload

    try:
        runner_payload = oracle_runners.run_runner(
            runner_id,
            repo_root=repo,
            module_path=module_path,
            module_profile=module_profile,
            scenario=scenario,
            translation_plan=translation_plan,
            scenario_inputs=scenario_inputs,
            qemu_log_output=qemu_log_output,
            artifact_root=artifact_root,
            build_dir=build_dir,
            make_llvm=make_llvm,
            timeout_sec=timeout_sec,
            runner_context=runner_context or {},
        )
    except ValueError as exc:
        payload = _blocked_oracle_payload(
            module_id=module_id,
            module_c_path=translation_plan["module_c_path"],
            runner_id=runner_id,
            scenario_id=scenario["scenario_id"],
            scenario_inputs=scenario_inputs,
            runner={
                "id": runner_id,
                "status": "runner-unavailable",
            },
            blocker_kind="runner-unavailable",
            blockers=[str(exc)],
            evidence_tier="env_blocked",
            safety_artifact=str(safety_verdict_path) if safety_verdict_path is not None else None,
            command_semantics_artifact=(
                str(command_semantics_verdict_path)
                if command_semantics_verdict_path is not None
                else None
            ),
            steps=[
                *(
                    [
                        {
                            "step_id": "verify-safety",
                            "status": "pass",
                            "details": [
                                f"violations={len(safety_verdict['violations'])}",
                                f"artifact={safety_verdict_path}",
                            ],
                        }
                    ]
                    if safety_verdict is not None and safety_verdict_path is not None
                    else []
                ),
                *(
                    [
                        {
                            "step_id": "verify-command-semantics",
                            "status": "pass",
                            "details": [
                                f"violations={len(command_semantics_verdict['violations'])}",
                                f"artifact={command_semantics_verdict_path}",
                            ],
                        }
                    ]
                    if command_semantics_verdict is not None and command_semantics_verdict_path is not None
                    else []
                ),
                {
                    "step_id": "run-oracle-backend",
                    "status": "blocked",
                    "details": [
                        "runner-unavailable",
                    ],
                },
            ],
        )
        output_path = _artifact_output(
            repo,
            module_id,
            "toolchain-run-record.json",
            output,
            artifact_root=artifact_root,
        )
        intake.write_json(output_path, payload)
        payload["output_path"] = str(output_path)
        return payload

    payload = {
        "schema_version": 1,
        "artifact_type": "toolchain-run-record",
        "module_id": module_id,
        "module_c_path": translation_plan["module_c_path"],
        "scenario": f"{runner_id}-{scenario['scenario_id']}",
        "oracle_runner": runner_id,
        "runner_id": runner_id,
        "scenario_id": scenario["scenario_id"],
        "pass": runner_payload["pass"],
        "ready_for_oracle": runner_payload["ready_for_oracle"],
        "evidence_tier": runner_payload.get("evidence_tier", "env_ready"),
        "scenario_inputs": scenario_inputs,
        "runner": runner_payload["runner"],
        "smoke_summary": runner_payload["smoke_summary"],
        "steps": runner_payload["steps"],
    }
    if safety_verdict_path is not None and safety_verdict is not None:
        payload["safety_verdict_artifact"] = str(safety_verdict_path)
        payload["safety_summary"] = safety_verdict["summary"]
    if command_semantics_verdict_path is not None and command_semantics_verdict is not None:
        payload["command_semantics_verdict_artifact"] = str(command_semantics_verdict_path)
        payload["command_semantics_summary"] = command_semantics_verdict["summary"]
    if "link_kind" in scenario_inputs:
        payload["link_kind"] = scenario_inputs["link_kind"]
    if "device_name" in scenario_inputs:
        payload["device_name"] = scenario_inputs["device_name"]
    if "device_node" in scenario_inputs:
        payload["device_node"] = scenario_inputs["device_node"]
    if runner_context:
        payload["runner_context"] = runner_context
    for key, value in runner_payload.items():
        if key in {"runner", "pass", "ready_for_oracle", "smoke_summary", "steps"}:
            continue
        payload[key] = value

    output_path = _artifact_output(
        repo,
        module_id,
        "toolchain-run-record.json",
        output,
        artifact_root=artifact_root,
    )
    intake.write_json(output_path, payload)
    payload["output_path"] = str(output_path)
    return payload


def run_differential_oracle(
    module_path: str | Path | None = None,
    *,
    repo_root: str | Path | None = None,
    artifact_root: str | Path | None = None,
    profile_id: str | None = None,
    source_tree: str | Path | None = None,
    baseline_kernel_tree: str | Path,
    baseline_module_path: str | Path | None = None,
    baseline_profile_id: str | None = None,
    baseline_source_tree: str | Path | None = None,
    output: str | Path | None = None,
    build_dir: str | Path | None = None,
    baseline_build_dir: str | Path | None = None,
    make_llvm: str | None = None,
    timeout_sec: int = 120,
) -> dict:
    context = intake.resolve_module_context(
        module_path,
        repo_root=repo_root,
        profile_id=profile_id,
        source_tree=source_tree,
    )
    repo = context["repo_root"]
    module_profile = context["profile"]
    module_id = context["module_id"]
    scenario = profiles.load_scenario_profile(module_profile["oracle"]["scenario_id"])
    differential_profile = module_profile.get("oracle", {}).get("differential", {})

    candidate_output = _artifact_output(
        repo,
        module_id,
        "oracle-candidate-run-record.json",
        artifact_root=artifact_root,
    )
    baseline_output = _artifact_output(
        repo,
        module_id,
        "oracle-baseline-run-record.json",
        artifact_root=artifact_root,
    )
    candidate = run_oracle(
        module_path,
        repo_root=repo,
        artifact_root=artifact_root,
        profile_id=profile_id,
        source_tree=source_tree,
        output=candidate_output,
        build_dir=build_dir,
        make_llvm=make_llvm,
        timeout_sec=timeout_sec,
        runner_context=differential_profile.get("candidate_runner_context"),
    )
    baseline = run_oracle(
        baseline_module_path or module_path,
        repo_root=baseline_kernel_tree,
        profile_id=baseline_profile_id or profile_id,
        source_tree=baseline_source_tree,
        output=baseline_output,
        build_dir=baseline_build_dir,
        make_llvm=make_llvm,
        timeout_sec=timeout_sec,
        runner_context=differential_profile.get("baseline_runner_context"),
        _skip_safety_gate=True,
        _skip_command_semantics_gate=True,
    )

    comparison_fields = scenario.get("comparison_fields", scenario.get("summary_fields", []))
    candidate_summary = candidate.get("smoke_summary", {})
    baseline_summary = baseline.get("smoke_summary", {})
    mismatches = [
        {
            "field": field,
            "candidate": candidate_summary.get(field),
            "baseline": baseline_summary.get(field),
        }
        for field in comparison_fields
        if candidate_summary.get(field) != baseline_summary.get(field)
    ]
    blocked = candidate.get("blocked", False) or baseline.get("blocked", False)
    blockers = list(candidate.get("blockers", [])) + list(baseline.get("blockers", []))
    candidate_evidence_tier = candidate.get("evidence_tier", "env_blocked")
    baseline_evidence_tier = baseline.get("evidence_tier", "env_blocked")
    overall_evidence_tier = (
        "differential_pass"
        if (
            not blocked
            and candidate["pass"]
            and baseline["pass"]
            and not mismatches
        )
        else _combine_evidence_tiers(candidate_evidence_tier, baseline_evidence_tier)
    )
    payload = {
        "schema_version": 1,
        "artifact_type": "differential-oracle-run-record",
        "module_id": module_id,
        "module_c_path": context["module_c_path"],
        "scenario_id": scenario["scenario_id"],
        "comparison_fields": comparison_fields,
        "required_operations": differential_profile.get("required_operations", []),
        "blocked": blocked,
        "blockers": blockers,
        "evidence_tier": overall_evidence_tier,
        "pass": (
            not blocked
            and candidate["pass"]
            and baseline["pass"]
            and not mismatches
        ),
        "ready_for_equivalence_claim": (
            not blocked
            and candidate["pass"]
            and baseline["pass"]
            and not mismatches
        ),
        "candidate": {
            "repo_root": str(repo),
            "runner_id": candidate["runner_id"],
            "run_record": str(candidate_output),
            "pass": candidate["pass"],
            "blocked": candidate.get("blocked", False),
            "evidence_tier": candidate_evidence_tier,
            "runner_context": differential_profile.get("candidate_runner_context"),
        },
        "baseline": {
            "repo_root": str(Path(baseline_kernel_tree).resolve()),
            "runner_id": baseline["runner_id"],
            "run_record": str(baseline_output),
            "pass": baseline["pass"],
            "blocked": baseline.get("blocked", False),
            "evidence_tier": baseline_evidence_tier,
            "runner_context": differential_profile.get("baseline_runner_context"),
        },
        "comparison": {
            "method": differential_profile.get("comparison_mode", "summary-field-equality"),
            "mismatches": mismatches,
        },
        "steps": [
            {
                "step_id": "run-candidate-oracle",
                "status": "blocked" if candidate.get("blocked", False) else ("pass" if candidate["pass"] else "fail"),
                "details": [str(candidate_output)],
            },
            {
                "step_id": "run-baseline-oracle",
                "status": "blocked" if baseline.get("blocked", False) else ("pass" if baseline["pass"] else "fail"),
                "details": [str(baseline_output)],
            },
            {
                "step_id": "compare-run-records",
                "status": "blocked" if blocked else ("pass" if not mismatches else "fail"),
                "details": [
                    f"fields={','.join(comparison_fields)}",
                    f"mismatches={len(mismatches)}",
                ],
            },
        ],
    }
    output_path = _artifact_output(
        repo,
        module_id,
        "differential-oracle-run-record.json",
        output,
        artifact_root=artifact_root,
    )
    intake.write_json(output_path, payload)
    payload["output_path"] = str(output_path)
    return payload


def run_qemu_link_smoke(
    module_path: str | Path | None = None,
    *,
    repo_root: str | Path | None = None,
    artifact_root: str | Path | None = None,
    profile_id: str | None = None,
    source_tree: str | Path | None = None,
    output: str | Path | None = None,
    qemu_log_output: str | Path | None = None,
    build_dir: str | Path | None = None,
    make_llvm: str | None = None,
    timeout_sec: int = 120,
) -> dict:
    return run_oracle(
        module_path,
        repo_root=repo_root,
        artifact_root=artifact_root,
        profile_id=profile_id,
        source_tree=source_tree,
        output=output,
        qemu_log_output=qemu_log_output,
        build_dir=build_dir,
        make_llvm=make_llvm,
        timeout_sec=timeout_sec,
    )


def _rule_matches(rule: dict, run_record: dict, qemu_log_text: str) -> bool:
    when = rule.get("when", {})
    summary = run_record.get("smoke_summary", {})

    if "result" in when and summary.get("result") != when["result"]:
        return False
    if "summary_nonzero_any" in when:
        if not any(summary.get(field) not in (None, 0) for field in when["summary_nonzero_any"]):
            return False
    log_markers = when.get("log_contains_any") or when.get("qemu_log_contains_any")
    if log_markers is not None:
        if not any(marker in qemu_log_text for marker in log_markers):
            return False
    return True


def apply_oracle_feedback(
    module_path: str | Path | None = None,
    *,
    repo_root: str | Path | None = None,
    artifact_root: str | Path | None = None,
    profile_id: str | None = None,
    source_tree: str | Path | None = None,
    run_record: str | Path,
    qemu_log: str | Path | None = None,
    output: str | Path | None = None,
) -> dict:
    context = intake.resolve_module_context(
        module_path,
        repo_root=repo_root,
        profile_id=profile_id,
        source_tree=source_tree,
    )
    repo = context["repo_root"]
    module_profile = context["profile"]
    run_record_path = Path(run_record)
    run_record_payload = json.loads(run_record_path.read_text())
    if qemu_log is not None:
        qemu_log_path = Path(qemu_log)
    else:
        qemu_log_path = _extract_run_log_path(run_record_payload)
    qemu_log_text = qemu_log_path.read_text() if qemu_log_path is not None and qemu_log_path.exists() else ""

    triggered_rules = []
    actions = []
    for rule in module_profile["oracle"].get("feedback_rules", []):
        if _rule_matches(rule, run_record_payload, qemu_log_text):
            triggered_rules.append(rule["id"])
            actions.extend(rule["actions"])

    default_output_path = intake._path_from_repo(
        repo,
        intake._artifact_path_for(
            module_profile["module_id"],
            "oracle-feedback.json",
            repo_root=repo,
            artifact_root=artifact_root,
        ),
    )
    payload = {
        "schema_version": 1,
        "artifact_type": "oracle-feedback",
        "module_id": module_profile["module_id"],
        "module_c_path": context["module_c_path"],
        "profile_id": module_profile["profile_id"],
        "profile_family": module_profile["family_id"],
        "profile_sources": module_profile["profile_sources"],
        "run_record_path": str(run_record_path),
        "qemu_log_path": str(qemu_log_path) if qemu_log_path else None,
        "scenario_id": run_record_payload.get("scenario_id"),
        "triggered_rules": triggered_rules,
        "actions": actions,
    }
    intake.write_json(default_output_path, payload)
    if output is not None and Path(output) != default_output_path:
        intake.write_json(output, payload)

    refreshed = intake.write_planning_artifacts(
        module_path,
        repo_root=repo,
        artifact_root=artifact_root,
        profile_id=module_profile["profile_id"],
        source_tree=source_tree,
    )
    payload["refreshed_artifacts"] = refreshed["generated_artifacts"]
    return payload
