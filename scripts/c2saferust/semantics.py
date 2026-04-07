#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

from __future__ import annotations

import re
from pathlib import Path

import intake


REPO_ROOT = Path(__file__).resolve().parents[2]


def _repo_root(repo_root: str | Path | None) -> Path:
    return Path(repo_root).resolve() if repo_root else REPO_ROOT


def _path_from_repo(repo_root: Path, path_like: str | Path) -> Path:
    path = Path(path_like)
    return path if path.is_absolute() else (repo_root / path)


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


def _contains_pattern(text: str, pattern: str) -> bool:
    normalized_text = intake._normalize_whitespace(text)
    compact_text = intake._compact_whitespace(text)
    return (
        pattern in text
        or intake._normalize_whitespace(pattern) in normalized_text
        or intake._compact_whitespace(pattern) in compact_text
    )


def _extract_function_body(text: str, fn_name: str) -> str | None:
    stripped = intake._strip_rust_noncode(text)
    match = re.search(rf"\bfn\s+{re.escape(fn_name)}\s*\(", stripped)
    if match is None:
        return None

    body_start = stripped.find("{", match.end())
    if body_start < 0:
        return None

    depth = 0
    for index in range(body_start, len(stripped)):
        char = stripped[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return stripped[body_start:index + 1]
    return None


def verify_module_command_semantics(
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
    command_semantics = intake.build_command_semantics(
        module_path,
        repo_root=repo,
        artifact_root=artifact_root,
        profile_id=context["profile_id"],
        source_tree=source_tree,
    )
    commands = command_semantics["commands"]

    if not commands:
        return {
            "schema_version": 1,
            "artifact_type": "command-semantics-verdict",
            "module_id": context["module_id"],
            "module_c_path": context["module_c_path"],
            "driver_rust_path": context["driver_rust_path"],
            "command_semantics_artifact": intake._artifact_path_for(
                context["module_id"],
                "command-semantics.json",
                repo_root=repo,
                artifact_root=artifact_root,
            ),
            "applicable": False,
            "pass": True,
            "summary": {
                "total_commands": 0,
                "verified_commands": 0,
                "total_violations": 0,
            },
            "commands": [],
            "violations": [],
        }

    driver_path = _path_from_repo(repo, context["driver_rust_path"])
    if not driver_path.exists():
        return {
            "schema_version": 1,
            "artifact_type": "command-semantics-verdict",
            "module_id": context["module_id"],
            "module_c_path": context["module_c_path"],
            "driver_rust_path": context["driver_rust_path"],
            "command_semantics_artifact": intake._artifact_path_for(
                context["module_id"],
                "command-semantics.json",
                repo_root=repo,
                artifact_root=artifact_root,
            ),
            "applicable": True,
            "pass": False,
            "summary": {
                "total_commands": len(commands),
                "verified_commands": 0,
                "total_violations": 1,
            },
            "commands": [],
            "violations": [
                {
                    "kind": "missing-driver-file",
                    "helper": None,
                    "command_id": None,
                    "message": f"Driver Rust file `{context['driver_rust_path']}` does not exist.",
                }
            ],
        }

    driver_text = driver_path.read_text()
    command_results = []
    violations = []
    for command in commands:
        body = _extract_function_body(driver_text, command["helper"])
        missing_patterns = []
        unexpected_patterns = []
        if body is None:
            violations.append(
                {
                    "kind": "missing-command-helper",
                    "helper": command["helper"],
                    "command_id": command["id"],
                    "message": f"Could not find Rust helper `{command['helper']}`.",
                }
            )
            command_results.append(
                {
                    "id": command["id"],
                    "helper": command["helper"],
                    "completion_mode": command["completion_mode"],
                    "status": "blocked",
                    "missing_patterns": ["fn " + command["helper"]],
                    "unexpected_patterns": [],
                }
            )
            continue

        for pattern in command["verification"]["must_contain"]:
            if not _contains_pattern(body, pattern):
                missing_patterns.append(pattern)
        for pattern in command["verification"]["must_not_contain"]:
            if _contains_pattern(body, pattern):
                unexpected_patterns.append(pattern)

        status = "pass" if not missing_patterns and not unexpected_patterns else "fail"
        command_results.append(
            {
                "id": command["id"],
                "helper": command["helper"],
                "completion_mode": command["completion_mode"],
                "status": status,
                "missing_patterns": missing_patterns,
                "unexpected_patterns": unexpected_patterns,
            }
        )
        if missing_patterns or unexpected_patterns:
            violations.append(
                {
                    "kind": "command-semantics-mismatch",
                    "helper": command["helper"],
                    "command_id": command["id"],
                    "completion_mode": command["completion_mode"],
                    "message": f"Helper `{command['helper']}` does not satisfy the declared completion semantics.",
                    "missing_patterns": missing_patterns,
                    "unexpected_patterns": unexpected_patterns,
                }
            )

    return {
        "schema_version": 1,
        "artifact_type": "command-semantics-verdict",
        "module_id": context["module_id"],
        "module_c_path": context["module_c_path"],
        "driver_rust_path": context["driver_rust_path"],
        "command_semantics_artifact": intake._artifact_path_for(
            context["module_id"],
            "command-semantics.json",
            repo_root=repo,
            artifact_root=artifact_root,
        ),
        "applicable": True,
        "pass": not violations,
        "summary": {
            "total_commands": len(commands),
            "verified_commands": sum(1 for result in command_results if result["status"] == "pass"),
            "total_violations": len(violations),
        },
        "commands": command_results,
        "violations": violations,
    }


def write_verdict(path: str | Path, payload: dict) -> None:
    intake.write_json(path, payload)
