#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

import intake
import safety


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


def _run_compile_gate(
    module_path: str | Path,
    *,
    repo_root: str | Path | None = None,
    build_dir: str | Path | None = None,
    make_llvm: str | None = None,
) -> dict:
    repo = _repo_root(repo_root)
    translation_plan = intake.build_translation_plan(module_path, repo_root=repo)
    module_id = translation_plan["module_id"]
    target = translation_plan["driver_rust_path"].removesuffix(".rs") + ".o"
    build_dir_path, build_dir_source = _resolve_build_dir(repo, module_id, build_dir)

    env, env_profile = _detect_build_env()
    llvm_arg = make_llvm if make_llvm is not None else env_profile["make_llvm"]

    command = ["make", f"O={build_dir_path}"]
    if llvm_arg is not None:
        command.append(f"LLVM={llvm_arg}")
    command.extend([f"-j{os.cpu_count() or 1}", target])

    completed = subprocess.run(
        command,
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    return {
        "id": "compile-driver-object",
        "pass": completed.returncode == 0,
        "status": "passed" if completed.returncode == 0 else "failed",
        "target": target,
        "build_dir": str(build_dir_path),
        "build_dir_source": build_dir_source,
        "build_dir_configured": _is_configured_build_dir(build_dir_path),
        "command": " ".join(shlex.quote(part) for part in command),
        "env_profile": env_profile,
        "exit_code": completed.returncode,
        "stdout_tail": _tail_lines(completed.stdout),
        "stderr_tail": _tail_lines(completed.stderr),
    }


def gate_agent_candidate(
    module_path: str | Path,
    *,
    repo_root: str | Path | None = None,
    artifact_root: str | Path | None = None,
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

    compile_gate: dict
    if not preflight_pass:
        compile_gate = {
            "id": "compile-driver-object",
            "pass": False,
            "status": "blocked",
            "reason": "Translation preflight is not ready.",
        }
    elif not safety_verdict["pass"]:
        compile_gate = {
            "id": "compile-driver-object",
            "pass": False,
            "status": "blocked",
            "reason": "Safety gate failed; compile gate is not allowed to run.",
        }
    elif skip_compile:
        compile_gate = {
            "id": "compile-driver-object",
            "pass": False,
            "status": "skipped",
            "reason": "Compile gate was skipped by request.",
        }
    else:
        compile_gate = _run_compile_gate(
            module_path,
            repo_root=repo,
            build_dir=build_dir,
            make_llvm=make_llvm,
        )
        if not compile_gate["pass"]:
            blocking_reasons.append("Compile gate failed.")

    gates.append(compile_gate)

    ready_for_acceptance = preflight_pass and safety_verdict["pass"]
    ready_for_smoke = ready_for_acceptance and compile_gate["status"] == "passed"

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
