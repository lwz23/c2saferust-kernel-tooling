#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import intake


REPO_ROOT = Path(__file__).resolve().parents[2]
VERIFIER_SOURCE = Path(__file__).with_name("safety_verifier.rs")
VERIFIER_BINARY = Path("/tmp/c2saferust-safety-verifier")


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


def verify_module_safety(
    module_path: str | Path,
    *,
    repo_root: str | Path | None = None,
    artifact_root: str | Path | None = None,
) -> dict:
    repo = _repo_root(repo_root)
    policy = intake.build_safety_policy(module_path, repo_root=repo, artifact_root=artifact_root)
    discharge = intake.build_soundness_discharge(module_path, repo_root=repo, artifact_root=artifact_root)
    module_id = Path(module_path).stem

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

    return {
        "schema_version": 1,
        "artifact_type": "safety-verdict",
        "module_id": module_id,
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
        "summary": {
            "quick_gate_violations": len(quick_violations),
            "rust_verifier_violations": len(rust_verifier_findings),
            "total_violations": len(all_violations),
        },
    }


def write_verdict(path: str | Path, verdict: dict) -> None:
    intake.write_json(path, verdict)
