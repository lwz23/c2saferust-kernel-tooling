#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

import intake
import safety
import semantics


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


def _tail_lines(text: str, limit: int = 40) -> list[str]:
    lines = text.splitlines()
    if len(lines) <= limit:
        return lines
    return lines[-limit:]


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


def _make_command(build_dir: Path, target: str, *, llvm_arg: str | None = None) -> list[str]:
    command = ["make", f"O={build_dir}"]
    if llvm_arg is not None:
        command.append(f"LLVM={llvm_arg}")
    command.append(target)
    return command


def _run_command(command: list[str], *, cwd: Path, env: dict[str, str]) -> dict:
    completed = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    return {
        "command": " ".join(shlex.quote(part) for part in command),
        "exit_code": completed.returncode,
        "stdout_tail": _tail_lines(completed.stdout),
        "stderr_tail": _tail_lines(completed.stderr),
        "pass": completed.returncode == 0,
    }


def _write_module_lifecycle_config(
    module_path: str | Path | None = None,
    *,
    repo_root: Path,
    artifact_root: str | Path | None = None,
    profile_id: str | None = None,
    source_tree: str | Path | None = None,
) -> dict:
    requirements = intake.module_lifecycle_config_requirements(
        module_path,
        repo_root=repo_root,
        artifact_root=artifact_root,
        profile_id=profile_id,
        source_tree=source_tree,
    )
    if not requirements["enabled"]:
        return {
            **requirements,
            "path": None,
            "status": "not-configured",
        }

    output_path = intake._path_from_repo(repo_root, requirements["artifact_path"])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        intake.render_module_lifecycle_config(
            module_path,
            repo_root=repo_root,
            artifact_root=artifact_root,
            profile_id=profile_id,
            source_tree=source_tree,
        )
    )
    return {
        **requirements,
        "path": str(output_path),
        "status": "written",
    }


def _expected_config_map(required_config_lines: list[str]) -> dict[str, str]:
    expected: dict[str, str] = {}
    for line in required_config_lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("# ") and stripped.endswith(" is not set"):
            expected[stripped[2:].split(" ", 1)[0]] = "n"
        elif stripped.startswith("CONFIG_") and "=" in stripped:
            key, value = stripped.split("=", 1)
            expected[key] = value
    return expected


def _verify_config_assignments(config_path: Path, required_config_lines: list[str]) -> list[str]:
    if not config_path.exists():
        return list(required_config_lines)
    config_text = config_path.read_text()
    missing: list[str] = []
    for key, value in _expected_config_map(required_config_lines).items():
        if value == "n":
            expected_line = f"# {key} is not set"
        else:
            expected_line = f"{key}={value}"
        if expected_line not in config_text:
            missing.append(expected_line)
    return missing


def _ensure_base_config(
    repo_root: Path,
    build_dir: Path,
    *,
    env: dict[str, str],
    llvm_arg: str | None = None,
) -> dict:
    build_dir.mkdir(parents=True, exist_ok=True)
    config_path = build_dir / ".config"
    if config_path.exists():
        return {
            "status": "already-configured",
            "pass": True,
            "config_path": str(config_path),
            "command": None,
            "exit_code": 0,
            "stdout_tail": [],
            "stderr_tail": [],
        }

    result = _run_command(_make_command(build_dir, "defconfig", llvm_arg=llvm_arg), cwd=repo_root, env=env)
    result.update(
        {
            "status": "configured" if result["pass"] and config_path.exists() else "failed",
            "config_path": str(config_path),
        }
    )
    if not config_path.exists():
        result["pass"] = False
    return result


def _prepare_module_lifecycle_build(
    module_path: str | Path | None = None,
    *,
    repo_root: str | Path | None = None,
    artifact_root: str | Path | None = None,
    profile_id: str | None = None,
    source_tree: str | Path | None = None,
    build_dir: str | Path | None = None,
    make_llvm: str | None = None,
) -> dict:
    repo = _repo_root(repo_root)
    translation_plan = intake.build_translation_plan(
        module_path,
        repo_root=repo,
        artifact_root=artifact_root,
        profile_id=profile_id,
        source_tree=source_tree,
    )
    module_id = translation_plan["module_id"]
    build_dir_path, build_dir_source = _resolve_build_dir(repo, module_id, build_dir)
    env, env_profile = _detect_build_env()
    llvm_arg = make_llvm if make_llvm is not None else env_profile["make_llvm"]

    base_config = _ensure_base_config(repo, build_dir_path, env=env, llvm_arg=llvm_arg)
    if not base_config["pass"]:
        return {
            "pass": False,
            "status": "failed",
            "reason": "Failed to create a baseline `.config` before lifecycle fragment merge.",
            "build_dir": str(build_dir_path),
            "build_dir_source": build_dir_source,
            "env_profile": env_profile,
            "base_config": base_config,
        }

    fragment = _write_module_lifecycle_config(
        module_path,
        repo_root=repo,
        artifact_root=artifact_root,
        profile_id=profile_id,
        source_tree=source_tree,
    )
    if not fragment["enabled"]:
        return {
            "pass": True,
            "status": "not-configured",
            "build_dir": str(build_dir_path),
            "build_dir_source": build_dir_source,
            "env_profile": env_profile,
            "base_config": base_config,
            "fragment": fragment,
            "merge_config": None,
            "olddefconfig": None,
            "config_verification": {"missing": []},
        }

    merge_script = repo / "scripts" / "kconfig" / "merge_config.sh"
    if not merge_script.exists():
        return {
            "pass": False,
            "status": "failed",
            "reason": "Missing `scripts/kconfig/merge_config.sh` in the kernel tree.",
            "build_dir": str(build_dir_path),
            "build_dir_source": build_dir_source,
            "env_profile": env_profile,
            "base_config": base_config,
            "fragment": fragment,
        }

    merge_result = _run_command(
        [
            os.environ.get("CONFIG_SHELL", "/bin/sh"),
            str(merge_script),
            "-m",
            "-O",
            str(build_dir_path),
            str(build_dir_path / ".config"),
            str(fragment["path"]),
        ],
        cwd=repo,
        env=env,
    )
    olddefconfig_result = _run_command(
        _make_command(build_dir_path, "olddefconfig", llvm_arg=llvm_arg),
        cwd=repo,
        env=env,
    )
    missing_config_lines = _verify_config_assignments(
        build_dir_path / ".config",
        fragment["required_config_lines"],
    )
    passed = merge_result["pass"] and olddefconfig_result["pass"] and not missing_config_lines
    return {
        "pass": passed,
        "status": "configured" if passed else "failed",
        "reason": None if passed else "Module lifecycle fragment did not converge in the target build dir.",
        "build_dir": str(build_dir_path),
        "build_dir_source": build_dir_source,
        "env_profile": env_profile,
        "base_config": base_config,
        "fragment": fragment,
        "merge_config": merge_result,
        "olddefconfig": olddefconfig_result,
        "config_verification": {
            "missing": missing_config_lines,
        },
    }


def _run_rust_toolchain_gate(
    module_path: str | Path | None = None,
    *,
    repo_root: str | Path | None = None,
    profile_id: str | None = None,
    source_tree: str | Path | None = None,
    build_dir: str | Path | None = None,
    make_llvm: str | None = None,
) -> dict:
    repo = _repo_root(repo_root)
    translation_plan = intake.build_translation_plan(
        module_path,
        repo_root=repo,
        profile_id=profile_id,
        source_tree=source_tree,
    )
    module_id = translation_plan["module_id"]
    build_dir_path, build_dir_source = _resolve_build_dir(repo, module_id, build_dir)
    env, env_profile = _detect_build_env()
    llvm_arg = make_llvm if make_llvm is not None else env_profile["make_llvm"]
    base_config = _ensure_base_config(repo, build_dir_path, env=env, llvm_arg=llvm_arg)
    if not base_config["pass"]:
        return {
            "id": "rust-toolchain-available",
            "pass": False,
            "status": "failed",
            "reason": "Failed to create a baseline `.config` before `make rustavailable`.",
            "build_dir": str(build_dir_path),
            "build_dir_source": build_dir_source,
            "build_dir_configured": _is_configured_build_dir(build_dir_path),
            "env_profile": env_profile,
            "base_config": base_config,
        }

    command_result = _run_command(
        _make_command(build_dir_path, "rustavailable", llvm_arg=llvm_arg),
        cwd=repo,
        env=env,
    )
    command_result.update(
        {
            "id": "rust-toolchain-available",
            "status": "passed" if command_result["pass"] else "failed",
            "build_dir": str(build_dir_path),
            "build_dir_source": build_dir_source,
            "build_dir_configured": _is_configured_build_dir(build_dir_path),
            "env_profile": env_profile,
            "base_config": base_config,
        }
    )
    return command_result


def _run_target_gate(
    gate_id: str,
    target: str,
    *,
    repo_root: Path,
    build_dir_path: Path,
    build_dir_source: str,
    env: dict[str, str],
    env_profile: dict,
    llvm_arg: str | None,
) -> dict:
    command = _make_command(build_dir_path, target, llvm_arg=llvm_arg)
    command.insert(-1, f"-j{os.cpu_count() or 1}")
    result = _run_command(command, cwd=repo_root, env=env)
    result.update(
        {
            "id": gate_id,
            "status": "passed" if result["pass"] else "failed",
            "target": target,
            "build_dir": str(build_dir_path),
            "build_dir_source": build_dir_source,
            "build_dir_configured": _is_configured_build_dir(build_dir_path),
            "env_profile": env_profile,
        }
    )
    return result


def _run_compile_gate(
    module_path: str | Path | None = None,
    *,
    repo_root: str | Path | None = None,
    artifact_root: str | Path | None = None,
    profile_id: str | None = None,
    source_tree: str | Path | None = None,
    build_dir: str | Path | None = None,
    make_llvm: str | None = None,
) -> dict:
    repo = _repo_root(repo_root)
    translation_plan = intake.build_translation_plan(
        module_path,
        repo_root=repo,
        profile_id=profile_id,
        source_tree=source_tree,
    )
    module_id = translation_plan["module_id"]
    target = translation_plan["driver_rust_path"].removesuffix(".rs") + ".o"
    preparation = _prepare_module_lifecycle_build(
        module_path,
        repo_root=repo,
        artifact_root=artifact_root,
        profile_id=profile_id,
        source_tree=source_tree,
        build_dir=build_dir,
        make_llvm=make_llvm,
    )
    if not preparation["pass"]:
        return {
            "id": "compile-driver-object",
            "pass": False,
            "status": "blocked",
            "target": target,
            "reason": preparation["reason"],
            "build_dir": preparation["build_dir"],
            "build_dir_source": preparation["build_dir_source"],
            "env_profile": preparation["env_profile"],
            "preparation": preparation,
        }

    llvm_arg = make_llvm if make_llvm is not None else preparation["env_profile"]["make_llvm"]
    env, _ = _detect_build_env()
    return _run_target_gate(
        "compile-driver-object",
        target,
        repo_root=repo,
        build_dir_path=Path(preparation["build_dir"]),
        build_dir_source=preparation["build_dir_source"],
        env=env,
        env_profile=preparation["env_profile"],
        llvm_arg=llvm_arg,
    )


def gate_agent_candidate(
    module_path: str | Path | None = None,
    *,
    repo_root: str | Path | None = None,
    artifact_root: str | Path | None = None,
    profile_id: str | None = None,
    source_tree: str | Path | None = None,
    output: str | Path | None = None,
    safety_output: str | Path | None = None,
    build_dir: str | Path | None = None,
    make_llvm: str | None = None,
    skip_compile: bool = False,
) -> dict:
    repo = _repo_root(repo_root)
    workflow_plan = intake.build_agent_workflow_plan(
        module_path,
        repo_root=repo,
        artifact_root=artifact_root,
        profile_id=profile_id,
        source_tree=source_tree,
    )
    module_id = workflow_plan["module_id"]

    gates = []
    blocking_reasons: list[str] = []

    preflight_pass = workflow_plan["preflight"]["ready_for_agent_codegen"]
    gates.append(
        {
            "id": "translation-readiness",
            "pass": preflight_pass,
            "status": "passed" if preflight_pass else "blocked",
            "blockers": workflow_plan["preflight"]["blockers"],
        }
    )
    if not preflight_pass:
        blocking_reasons.extend(workflow_plan["preflight"]["blockers"])

    safety_verdict = safety.verify_module_safety(
        module_path,
        repo_root=repo,
        artifact_root=artifact_root,
        profile_id=profile_id,
        source_tree=source_tree,
    )
    safety_path = _artifact_output(
        repo,
        module_id,
        "safety-verdict.json",
        safety_output,
        artifact_root=artifact_root,
    )
    safety.write_verdict(safety_path, safety_verdict)
    gates.append(
        {
            "id": "verify-safety",
            "pass": safety_verdict["pass"],
            "status": "passed" if safety_verdict["pass"] else "failed",
            "artifact": str(safety_path),
            "summary": safety_verdict["summary"],
            "violation_count": len(safety_verdict["violations"]),
        }
    )
    if not safety_verdict["pass"]:
        blocking_reasons.append("Safety gate failed.")

    command_semantics_profile = intake.build_command_semantics(
        module_path,
        repo_root=repo,
        artifact_root=artifact_root,
        profile_id=profile_id,
        source_tree=source_tree,
    )
    command_semantics_verdict = None
    command_semantics_path = _artifact_output(
        repo,
        module_id,
        "command-semantics-verdict.json",
        artifact_root=artifact_root,
    )
    if command_semantics_profile["commands"]:
        command_semantics_verdict = semantics.verify_module_command_semantics(
            module_path,
            repo_root=repo,
            artifact_root=artifact_root,
            profile_id=profile_id,
            source_tree=source_tree,
        )
        semantics.write_verdict(command_semantics_path, command_semantics_verdict)
        gates.append(
            {
                "id": "verify-command-semantics",
                "pass": command_semantics_verdict["pass"],
                "status": "passed" if command_semantics_verdict["pass"] else "failed",
                "artifact": str(command_semantics_path),
                "summary": command_semantics_verdict["summary"],
                "violation_count": len(command_semantics_verdict["violations"]),
            }
        )
        if not command_semantics_verdict["pass"]:
            blocking_reasons.append("Command semantics gate failed.")

    rust_toolchain_gate: dict
    compile_gate: dict
    package_gate: dict
    if not preflight_pass:
        rust_toolchain_gate = {
            "id": "rust-toolchain-available",
            "pass": False,
            "status": "blocked",
            "reason": "Translation preflight is not ready.",
        }
        compile_gate = {
            "id": "compile-driver-object",
            "pass": False,
            "status": "blocked",
            "reason": "Translation preflight is not ready.",
        }
        package_gate = {
            "id": "package-module",
            "pass": False,
            "status": "blocked",
            "reason": "Translation preflight is not ready.",
        }
    elif not safety_verdict["pass"]:
        rust_toolchain_gate = {
            "id": "rust-toolchain-available",
            "pass": False,
            "status": "blocked",
            "reason": "Safety gate failed; build gating is not allowed to run.",
        }
        compile_gate = {
            "id": "compile-driver-object",
            "pass": False,
            "status": "blocked",
            "reason": "Safety gate failed; compile gate is not allowed to run.",
        }
        package_gate = {
            "id": "package-module",
            "pass": False,
            "status": "blocked",
            "reason": "Safety gate failed; module packaging is not allowed to run.",
        }
    elif command_semantics_verdict is not None and not command_semantics_verdict["pass"]:
        rust_toolchain_gate = {
            "id": "rust-toolchain-available",
            "pass": False,
            "status": "blocked",
            "reason": "Command semantics gate failed; build gating is not allowed to run.",
        }
        compile_gate = {
            "id": "compile-driver-object",
            "pass": False,
            "status": "blocked",
            "reason": "Command semantics gate failed; compile gate is not allowed to run.",
        }
        package_gate = {
            "id": "package-module",
            "pass": False,
            "status": "blocked",
            "reason": "Command semantics gate failed; module packaging is not allowed to run.",
        }
    elif skip_compile:
        rust_toolchain_gate = {
            "id": "rust-toolchain-available",
            "pass": False,
            "status": "skipped",
            "reason": "Build gates were skipped by request.",
        }
        compile_gate = {
            "id": "compile-driver-object",
            "pass": False,
            "status": "skipped",
            "reason": "Compile gate was skipped by request.",
        }
        package_gate = {
            "id": "package-module",
            "pass": False,
            "status": "skipped",
            "reason": "Module packaging gate was skipped by request.",
        }
    else:
        rust_toolchain_gate = _run_rust_toolchain_gate(
            module_path,
            repo_root=repo,
            profile_id=profile_id,
            source_tree=source_tree,
            build_dir=build_dir,
            make_llvm=make_llvm,
        )
        if not rust_toolchain_gate["pass"]:
            blocking_reasons.append("Rust toolchain gate failed.")
            compile_gate = {
                "id": "compile-driver-object",
                "pass": False,
                "status": "blocked",
                "reason": "Rust toolchain preflight failed; compile gate is not allowed to run.",
            }
            package_gate = {
                "id": "package-module",
                "pass": False,
                "status": "blocked",
                "reason": "Rust toolchain preflight failed; module packaging is not allowed to run.",
            }
        else:
            preparation = _prepare_module_lifecycle_build(
                module_path,
                repo_root=repo,
                artifact_root=artifact_root,
                profile_id=profile_id,
                source_tree=source_tree,
                build_dir=build_dir,
                make_llvm=make_llvm,
            )
            if not preparation["pass"]:
                blocking_reasons.append("Module lifecycle build configuration failed.")
                compile_gate = {
                    "id": "compile-driver-object",
                    "pass": False,
                    "status": "blocked",
                    "reason": preparation["reason"],
                    "preparation": preparation,
                }
                package_gate = {
                    "id": "package-module",
                    "pass": False,
                    "status": "blocked",
                    "reason": "Module lifecycle build configuration failed; module packaging is not allowed to run.",
                    "preparation": preparation,
                }
            else:
                llvm_arg = make_llvm if make_llvm is not None else preparation["env_profile"]["make_llvm"]
                env, _ = _detect_build_env()
                build_dir_path = Path(preparation["build_dir"])
                compile_target = workflow_plan["driver_object_path"]
                compile_gate = _run_target_gate(
                    "compile-driver-object",
                    compile_target,
                    repo_root=repo,
                    build_dir_path=build_dir_path,
                    build_dir_source=preparation["build_dir_source"],
                    env=env,
                    env_profile=preparation["env_profile"],
                    llvm_arg=llvm_arg,
                )
                if not compile_gate["pass"]:
                    blocking_reasons.append("Compile gate failed.")
                    package_gate = {
                        "id": "package-module",
                        "pass": False,
                        "status": "blocked",
                        "reason": "Compile gate failed; module packaging is not allowed to run.",
                    }
                else:
                    package_gate = _run_target_gate(
                        "package-module",
                        workflow_plan["driver_rust_path"].removesuffix(".rs") + ".ko",
                        repo_root=repo,
                        build_dir_path=build_dir_path,
                        build_dir_source=preparation["build_dir_source"],
                        env=env,
                        env_profile=preparation["env_profile"],
                        llvm_arg=llvm_arg,
                    )
                    if not package_gate["pass"]:
                        blocking_reasons.append("Module packaging gate failed.")

    gates.append(rust_toolchain_gate)
    gates.append(compile_gate)
    gates.append(package_gate)

    ready_for_acceptance = preflight_pass and safety_verdict["pass"] and (
        command_semantics_verdict is None or command_semantics_verdict["pass"]
    )
    ready_for_smoke = (
        ready_for_acceptance
        and rust_toolchain_gate["status"] == "passed"
        and compile_gate["status"] == "passed"
        and package_gate["status"] == "passed"
    )

    payload = {
        "schema_version": 1,
        "artifact_type": "agent-gate-report",
        "module_id": module_id,
        "module_c_path": workflow_plan["module_c_path"],
        "workflow_artifact": intake._artifact_path_for(
            module_id,
            "agent-workflow-plan.json",
            repo_root=repo,
            artifact_root=artifact_root,
        ),
        "driver_rust_path": workflow_plan["driver_rust_path"],
        "pass": ready_for_acceptance,
        "ready_for_smoke": ready_for_smoke,
        "gates": gates,
        "blocking_reasons": blocking_reasons,
    }

    output_path = _artifact_output(
        repo,
        module_id,
        "agent-gate-report.json",
        output,
        artifact_root=artifact_root,
    )
    intake.write_json(output_path, payload)
    payload["output_path"] = str(output_path)
    return payload
