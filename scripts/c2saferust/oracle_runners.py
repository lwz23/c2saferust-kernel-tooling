#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import intake


REPO_ROOT = Path(__file__).resolve().parents[2]
LOCAL_LLVM_ROOT = Path("/tmp/llvm-15-local/usr")
RUNNERS: dict[str, object] = {}


def register_runner(runner_id: str, handler: object) -> None:
    RUNNERS[runner_id] = handler


def available_runner_ids() -> list[str]:
    return sorted(RUNNERS)


def run_runner(runner_id: str, **kwargs) -> dict:
    if runner_id not in RUNNERS:
        raise ValueError(
            f"Unknown oracle runner {runner_id!r}; available runners: {', '.join(available_runner_ids()) or '<none>'}."
        )
    return RUNNERS[runner_id](**kwargs)


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


def _ensure_build_target(
    repo_root: Path,
    build_dir: Path,
    target: str,
    *,
    make_llvm: str | None = None,
) -> tuple[Path, dict]:
    target_path = build_dir / target
    env, env_profile = _detect_build_env()
    llvm_arg = make_llvm if make_llvm is not None else env_profile["make_llvm"]

    if target_path.exists():
        return target_path, {
            "status": "already_built",
            "target": target,
            "target_path": str(target_path),
            "env_profile": env_profile,
            "command": None,
            "exit_code": 0,
        }

    command = ["make", f"O={build_dir}"]
    if llvm_arg is not None:
        command.append(f"LLVM={llvm_arg}")
    command.extend([f"-j{os.cpu_count() or 1}", target])

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
        "target": target,
        "target_path": str(target_path),
        "env_profile": env_profile,
        "command": " ".join(command),
        "exit_code": completed.returncode,
        "stdout_tail": completed.stdout.splitlines()[-60:],
        "stderr_tail": completed.stderr.splitlines()[-60:],
    }
    if completed.returncode != 0 or not target_path.exists():
        raise RuntimeError(f"Failed to build oracle target `{target}`.")
    return target_path, result


def _write_init_script(root: Path, scenario: dict, scenario_inputs: dict) -> None:
    format_context = dict(scenario_inputs)
    format_context["log_prefix"] = scenario["log_prefix"]
    init_text = "\n".join(line.format(**format_context) for line in scenario["script_lines"]) + "\n"
    (root / "init").write_text(init_text)
    (root / "init").chmod(0o755)


def _build_initramfs(
    scenario: dict,
    scenario_inputs: dict,
    *,
    extra_files: list[tuple[Path, str]] | None = None,
) -> tuple[Path, dict]:
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
    (root / "sbin" / "rmmod").symlink_to("/bin/busybox")

    for source, destination in extra_files or []:
        target = root / destination
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

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


def _run_qemu_scenario(
    *,
    repo_root: Path,
    module_path: str | Path,
    module_profile: dict,
    scenario: dict,
    translation_plan: dict,
    scenario_inputs: dict,
    qemu_log_output: str | Path | None = None,
    artifact_root: str | Path | None = None,
    build_dir: str | Path | None = None,
    make_llvm: str | None = None,
    timeout_sec: int = 120,
) -> dict:
    del module_path, module_profile

    module_id = translation_plan["module_id"]
    resolved_build_dir, build_dir_source = _resolve_build_dir(repo_root, module_id, build_dir)
    kernel_image, build_result = _ensure_kernel_image(
        repo_root,
        resolved_build_dir,
        make_llvm=make_llvm,
    )
    initramfs, initramfs_result = _build_initramfs(scenario, scenario_inputs)

    qemu_log_path = _artifact_output(
        repo_root,
        module_id,
        "qemu-smoke.log",
        qemu_log_output,
        artifact_root=artifact_root,
    )
    qemu_log_path.parent.mkdir(parents=True, exist_ok=True)
    qemu_result = _run_qemu(kernel_image, initramfs, qemu_log_path, timeout_sec=timeout_sec)
    qemu_text = qemu_log_path.read_text()
    smoke_summary = _extract_scenario_summary(qemu_text, scenario)

    return {
        "runner": {
            "id": "qemu-scenario",
            "log_path": str(qemu_log_path),
            "command": qemu_result["command"],
            "exit_code": qemu_result["exit_code"],
            "stdout_tail": qemu_result["stdout_tail"],
            "stderr_tail": qemu_result["stderr_tail"],
        },
        "pass": smoke_summary["result"] == "PASS",
        "ready_for_oracle": smoke_summary["result"] == "PASS",
        "build": {
            "build_dir": str(resolved_build_dir),
            "build_dir_source": build_dir_source,
            "kernel_image": str(kernel_image),
            "kernel_build": build_result,
        },
        "initramfs": initramfs_result,
        "qemu": {
            "log_path": str(qemu_log_path),
            **qemu_result,
        },
        "smoke_summary": smoke_summary,
        "steps": [
            {
                "step_id": "build-kernel-image",
                "status": "pass",
                "details": [str(kernel_image)],
            },
            {
                "step_id": "build-initramfs",
                "status": "pass",
                "details": [str(initramfs)],
            },
            {
                "step_id": "run-qemu-link-smoke",
                "status": "pass" if smoke_summary["result"] == "PASS" else "fail",
                "details": [f"RESULT={smoke_summary['result']}"],
            },
        ],
    }


def _run_qemu_module_lifecycle(
    *,
    repo_root: Path,
    module_path: str | Path,
    module_profile: dict,
    scenario: dict,
    translation_plan: dict,
    scenario_inputs: dict,
    qemu_log_output: str | Path | None = None,
    artifact_root: str | Path | None = None,
    build_dir: str | Path | None = None,
    make_llvm: str | None = None,
    timeout_sec: int = 120,
) -> dict:
    del module_path, module_profile

    module_id = translation_plan["module_id"]
    module_relpath = scenario_inputs["module_file_relpath"]
    module_file_name = scenario_inputs.get("module_file_name", Path(module_relpath).name)
    resolved_build_dir, build_dir_source = _resolve_build_dir(repo_root, module_id, build_dir)
    kernel_image, build_result = _ensure_kernel_image(
        repo_root,
        resolved_build_dir,
        make_llvm=make_llvm,
    )
    module_ko, module_build = _ensure_build_target(
        repo_root,
        resolved_build_dir,
        module_relpath,
        make_llvm=make_llvm,
    )
    initramfs, initramfs_result = _build_initramfs(
        scenario,
        scenario_inputs,
        extra_files=[(module_ko, f"modules/{module_file_name}")],
    )

    qemu_log_path = _artifact_output(
        repo_root,
        module_id,
        "qemu-smoke.log",
        qemu_log_output,
        artifact_root=artifact_root,
    )
    qemu_log_path.parent.mkdir(parents=True, exist_ok=True)
    qemu_result = _run_qemu(kernel_image, initramfs, qemu_log_path, timeout_sec=timeout_sec)
    qemu_text = qemu_log_path.read_text()
    smoke_summary = _extract_scenario_summary(qemu_text, scenario)

    return {
        "runner": {
            "id": "qemu-module-lifecycle",
            "log_path": str(qemu_log_path),
            "command": qemu_result["command"],
            "exit_code": qemu_result["exit_code"],
            "stdout_tail": qemu_result["stdout_tail"],
            "stderr_tail": qemu_result["stderr_tail"],
        },
        "pass": smoke_summary["result"] == "PASS",
        "ready_for_oracle": smoke_summary["result"] == "PASS",
        "build": {
            "build_dir": str(resolved_build_dir),
            "build_dir_source": build_dir_source,
            "kernel_image": str(kernel_image),
            "kernel_build": build_result,
            "module_build": module_build,
        },
        "initramfs": initramfs_result,
        "qemu": {
            "log_path": str(qemu_log_path),
            **qemu_result,
        },
        "smoke_summary": smoke_summary,
        "steps": [
            {
                "step_id": "build-kernel-image",
                "status": "pass",
                "details": [str(kernel_image)],
            },
            {
                "step_id": "build-module",
                "status": "pass",
                "details": [str(module_ko)],
            },
            {
                "step_id": "build-initramfs",
                "status": "pass",
                "details": [str(initramfs)],
            },
            {
                "step_id": "run-qemu-module-lifecycle",
                "status": "pass" if smoke_summary["result"] == "PASS" else "fail",
                "details": [f"RESULT={smoke_summary['result']}"],
            },
        ],
    }


register_runner("qemu-scenario", _run_qemu_scenario)
register_runner("qemu-module-lifecycle", _run_qemu_module_lifecycle)
