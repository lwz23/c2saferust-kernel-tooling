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


REPO_ROOT = Path(__file__).resolve().parents[2]
LOCAL_LLVM_ROOT = Path("/tmp/llvm-15-local/usr")


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

    def local_llvm_is_runnable() -> bool:
        if not clang15.exists():
            return False
        try:
            completed = subprocess.run(
                [str(clang15), "--version"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except Exception:
            return False
        return completed.returncode == 0

    if local_llvm_is_runnable():
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
    context = {
        "module_id": translation_plan["module_id"],
        "driver_rust_path": translation_plan["driver_rust_path"],
        "driver_object_path": translation_plan["driver_rust_path"].removesuffix(".rs") + ".o",
    }
    context.update(scenario_inputs)

    rendered = dict(context)
    for key, value in scenario_inputs.items():
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


def run_oracle(
    module_path: str | Path,
    *,
    repo_root: str | Path | None = None,
    artifact_root: str | Path | None = None,
    output: str | Path | None = None,
    qemu_log_output: str | Path | None = None,
    build_dir: str | Path | None = None,
    make_llvm: str | None = None,
    timeout_sec: int = 120,
) -> dict:
    repo = _repo_root(repo_root)
    module = intake._path_from_repo(repo, module_path)
    module_profile = profiles.load_module_profile(intake._rel(repo, module))
    scenario = profiles.load_scenario_profile(module_profile["oracle"]["scenario_id"])
    translation_plan = intake.build_translation_plan(
        module_path,
        repo_root=repo,
        artifact_root=artifact_root,
    )
    module_id = translation_plan["module_id"]
    runner_id = _resolve_runner_id(module_profile, scenario)
    scenario_inputs = _render_scenario_inputs(module_path, module_profile, translation_plan)
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
    )

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
        "scenario_inputs": scenario_inputs,
        "runner": runner_payload["runner"],
        "smoke_summary": runner_payload["smoke_summary"],
        "steps": runner_payload["steps"],
    }
    for optional_field in ("link_kind", "device_name", "module_name", "module_file_relpath"):
        if optional_field in scenario_inputs:
            payload[optional_field] = scenario_inputs[optional_field]
    for key in ("build", "initramfs", "qemu"):
        if key in runner_payload:
            payload[key] = runner_payload[key]

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


def run_qemu_link_smoke(
    module_path: str | Path,
    *,
    repo_root: str | Path | None = None,
    artifact_root: str | Path | None = None,
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
    module_path: str | Path,
    *,
    repo_root: str | Path | None = None,
    artifact_root: str | Path | None = None,
    run_record: str | Path,
    qemu_log: str | Path | None = None,
    output: str | Path | None = None,
) -> dict:
    repo = _repo_root(repo_root)
    module = intake._path_from_repo(repo, module_path)
    module_profile = profiles.load_module_profile(intake._rel(repo, module))
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
        "module_c_path": intake._rel(repo, module),
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
    )
    payload["refreshed_artifacts"] = refreshed["generated_artifacts"]
    return payload
