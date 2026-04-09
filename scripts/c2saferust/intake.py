#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

from __future__ import annotations

import json
import re
from pathlib import Path

import profiles


REPO_ROOT = Path(__file__).resolve().parents[2]
INCLUDE_RE = re.compile(r'^\s*#include\s+[<"]([^>"]+)[>"]', re.MULTILINE)
MAKEFILE_ENTRY_RE = re.compile(r'obj-\$\(CONFIG_([A-Z0-9_]+)\)\s*\+=\s*([A-Za-z0-9_.-]+)')
INITIALIZER_FIELD_RE = re.compile(r"^\s*\.(\w+)\s*=\s*([^,]+),", re.MULTILINE)
VALIDATE_CHECK_RE = re.compile(r"if\s*\(\s*tb\[(?P<field>[A-Z0-9_]+)\]\s*\)\s*return\s+(?P<retval>[-A-Z0-9_]+);")

KEYWORD_CALLS = {"if", "return", "sizeof", "while", "for", "switch"}


def _repo_root(repo_root: str | Path | None) -> Path:
    return Path(repo_root).resolve() if repo_root else REPO_ROOT


def _path_from_root(root: Path, path_like: str | Path) -> Path:
    path = Path(path_like)
    return path if path.is_absolute() else (root / path)


def _path_from_repo(repo_root: Path, path_like: str | Path) -> Path:
    return _path_from_root(repo_root, path_like)


def _path_lookup_key(path_like: str | Path, *, roots: list[Path]) -> str:
    path = Path(path_like)
    if not path.is_absolute():
        return str(path)
    for root in roots:
        try:
            return str(path.resolve().relative_to(root.resolve()))
        except ValueError:
            continue
    return str(path.resolve())


def _rel(repo_root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(repo_root.resolve()))
    except ValueError:
        return str(path.resolve())


def _artifact_metadata(profile: dict) -> dict:
    return {
        "profile_id": profile["profile_id"],
        "profile_family": profile["family_id"],
        "profile_sources": profile["profile_sources"],
    }


def resolve_module_context(
    module_path: str | Path | None = None,
    *,
    repo_root: str | Path | None = None,
    profile_id: str | None = None,
    source_tree: str | Path | None = None,
) -> dict:
    repo = _repo_root(repo_root)
    source_tree_path = Path(source_tree).resolve() if source_tree is not None else None

    lookup_key = None
    if module_path is not None:
        roots = [repo]
        if source_tree_path is not None:
            roots.insert(0, source_tree_path)
        lookup_key = _path_lookup_key(module_path, roots=roots)

    profile = profiles.resolve_module_profile(module_path=lookup_key, profile_id=profile_id)
    source_profile = profile.get("source", {})
    landing_profile = profile.get("landing", {})
    source_tree_role = source_profile.get("tree", "kernel-tree")

    if source_tree_role == "external-tree":
        if source_tree_path is None:
            raise ValueError(
                f"Profile `{profile['profile_id']}` requires `--source-tree` because its source tree is external."
            )
        source_root = source_tree_path
    else:
        source_root = repo

    source_module_relpath = source_profile.get("module_path", profile["module_path"])
    source_module = _path_from_root(source_root, source_module_relpath)
    module_id = profile["module_id"]
    driver_rust_path = landing_profile.get("driver_rust_path", profile["driver_rust_path"])
    module_dir = landing_profile.get("module_dir", str(Path(driver_rust_path).parent))
    kconfig_path = landing_profile.get("kconfig_path", f"{module_dir}/Kconfig")
    makefile_path = landing_profile.get("makefile_path", f"{module_dir}/Makefile")
    artifact_dir = profile.get("artifact_dir", f"Documentation/rust/c2saferust/{module_id}")

    return {
        "repo_root": repo,
        "source_root": source_root,
        "source_tree_role": source_tree_role,
        "profile": profile,
        "profile_id": profile["profile_id"],
        "module_id": module_id,
        "module_path": source_module_relpath,
        "module_c_path": _rel(source_root, source_module),
        "module_c_path_in_repo": _rel(repo, source_module) if source_root == repo else None,
        "module_source_path": source_module,
        "module_dir": module_dir,
        "kconfig_path": kconfig_path,
        "makefile_path": makefile_path,
        "driver_rust_path": driver_rust_path,
        "artifact_dir": artifact_dir,
        "kbuild_mode": profile.get("kbuild", {}).get("mode", "replace-existing-c-object"),
        "source_tree": str(source_root),
    }


def _module_profile(
    repo_root: Path,
    module_path: str | Path | None = None,
    *,
    profile_id: str | None = None,
    source_tree: str | Path | None = None,
) -> dict:
    return resolve_module_context(
        module_path,
        repo_root=repo_root,
        profile_id=profile_id,
        source_tree=source_tree,
    )["profile"]


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def stable_json(payload: dict) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def write_json(path: str | Path, payload: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stable_json(payload))


def _load_text(path: Path) -> str:
    return path.read_text()


def _normalize_whitespace(text: str) -> str:
    return " ".join(text.split())


def _compact_whitespace(text: str) -> str:
    return "".join(text.split())


def _strip_rust_noncode(text: str) -> str:
    result: list[str] = []
    index = 0
    block_comment_depth = 0
    in_line_comment = False
    in_string = False
    in_char = False
    escape = False

    while index < len(text):
        char = text[index]
        nxt = text[index + 1] if index + 1 < len(text) else ""

        if in_line_comment:
            if char == "\n":
                in_line_comment = False
                result.append(char)
            else:
                result.append(" ")
            index += 1
            continue

        if block_comment_depth:
            if char == "/" and nxt == "*":
                block_comment_depth += 1
                result.extend("  ")
                index += 2
            elif char == "*" and nxt == "/":
                block_comment_depth -= 1
                result.extend("  ")
                index += 2
            else:
                result.append("\n" if char == "\n" else " ")
                index += 1
            continue

        if in_string:
            if escape:
                escape = False
                result.append(" ")
            elif char == "\\":
                escape = True
                result.append(" ")
            elif char == '"':
                in_string = False
                result.append(" ")
            else:
                result.append("\n" if char == "\n" else " ")
            index += 1
            continue

        if in_char:
            if escape:
                escape = False
                result.append(" ")
            elif char == "\\":
                escape = True
                result.append(" ")
            elif char == "'":
                in_char = False
                result.append(" ")
            else:
                result.append("\n" if char == "\n" else " ")
            index += 1
            continue

        if char == "/" and nxt == "/":
            in_line_comment = True
            result.extend("  ")
            index += 2
            continue

        if char == "/" and nxt == "*":
            block_comment_depth = 1
            result.extend("  ")
            index += 2
            continue

        if char == '"':
            in_string = True
            result.append(" ")
            index += 1
            continue

        if char == "'":
            in_char = True
            result.append(" ")
            index += 1
            continue

        result.append(char)
        index += 1

    return "".join(result)


def _enclosing_pattern(lines: list[str], index: int, pattern: Pattern[str]) -> str:
    for idx in range(index, -1, -1):
        match = pattern.search(lines[idx])
        if match:
            return lines[idx].strip()
    return ""


def _safety_comment_window(lines: list[str], index: int) -> str:
    comments: list[str] = []
    for idx in range(index - 1, -1, -1):
        line = lines[idx].strip()
        if not line:
            if comments:
                break
            continue
        if "SAFETY" in line and (line.startswith("//") or line.startswith("/*")):
            comments.insert(0, line)
            continue
        if comments:
            break
    return "\n".join(comments)


def _source_window(lines: list[str], index: int, radius: int = 2) -> str:
    start = max(0, index - radius)
    end = min(len(lines), index + radius + 1)
    return "\n".join(lines[start:end])


def _find_rust_unsafe_sites(repo_root: Path, relative_paths: list[str]) -> list[dict]:
    sites: list[dict] = []
    fn_pattern = re.compile(r"\bfn\s+[\w_]+")
    impl_pattern = re.compile(r"\bimpl(?:<[^>]+>)?(?:\s|$)")
    for relative_path in relative_paths:
        path = _path_from_repo(repo_root, relative_path)
        if not path.exists():
            continue
        raw_text = _load_text(path)
        raw_lines = raw_text.splitlines()
        stripped = _strip_rust_noncode(raw_text)
        for line_number, line in enumerate(stripped.splitlines(), start=1):
            if "unsafe" not in line:
                continue
            if re.search(r"\bunsafe\s+impl\b", line):
                kind = "unsafe-impl"
            elif re.search(r"\bunsafe\b[^\n{;]*\bfn\b", line):
                kind = "unsafe-fn"
            elif re.search(r"\bunsafe\s+trait\b", line):
                kind = "unsafe-trait"
            elif re.search(r"\bunsafe\s*\{", line):
                kind = "unsafe-block"
            else:
                kind = "unsafe-token"

            idx = line_number - 1
            site = {
                "file": relative_path,
                "line": line_number,
                "unsafe_kind": kind,
                "source_excerpt": line.strip(),
                "safety_comment_window": _safety_comment_window(raw_lines, idx),
                "source_window": _source_window(raw_lines, idx),
                "enclosing_item": _enclosing_pattern(raw_lines, idx, fn_pattern),
                "enclosing_impl_type": _enclosing_pattern(raw_lines, idx, impl_pattern),
            }
            sites.append(site)
    return sites


def _profile_analysis(profile: dict) -> dict:
    return profile.get("analysis", {})


def _get_nested_value(payload: object, dotted_path: str) -> object:
    current = payload
    for segment in dotted_path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(segment)
    return current


def _collect_evidence_context(profile: dict, abstraction_plan: dict) -> dict[str, list]:
    analysis = _profile_analysis(profile)
    configured_paths = analysis.get("evidence_context_paths", {})
    resolved: dict[str, list] = {}
    for context_key, configured_path in configured_paths.items():
        values: list[object] = []
        paths = configured_path if isinstance(configured_path, list) else [configured_path]
        for path in paths:
            value = _get_nested_value(abstraction_plan, path)
            if isinstance(value, list):
                values.extend(value)
            elif value is not None:
                values.append(value)
        resolved[context_key] = values
    return resolved


def _listify(value: object) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _text_contains_any(text: str, needles: list[str]) -> bool:
    return not needles or any(needle in text for needle in needles)


def _text_matches_any(text: str, patterns: list[str]) -> bool:
    return not patterns or any(re.search(pattern, text, re.MULTILINE) for pattern in patterns)


def _match_unsafe_obligation_ids(profile: dict, site: dict) -> list[str]:
    matchers = _profile_analysis(profile).get("unsafe_site_matchers", [])
    matched: list[str] = []
    for matcher in matchers:
        file_suffix = matcher.get("file_suffix")
        if file_suffix and not site["file"].endswith(file_suffix):
            continue
        unsafe_kinds = _listify(matcher.get("unsafe_kind")) + _listify(matcher.get("unsafe_kinds"))
        if unsafe_kinds and site["unsafe_kind"] not in unsafe_kinds:
            continue
        if not _text_contains_any(site["source_excerpt"], matcher.get("excerpt_contains_any", [])):
            continue
        if not _text_matches_any(site["source_excerpt"], matcher.get("excerpt_matches_any", [])):
            continue
        if not _text_contains_any(site["enclosing_item"], matcher.get("enclosing_item_contains_any", [])):
            continue
        if not _text_contains_any(site["enclosing_impl_type"], matcher.get("enclosing_impl_contains_any", [])):
            continue
        if not _text_contains_any(site["safety_comment_window"], matcher.get("safety_comment_contains_any", [])):
            continue
        if not _text_contains_any(site["source_window"], matcher.get("source_window_contains_any", [])):
            continue
        if not _text_matches_any(site["source_window"], matcher.get("source_window_matches_any", [])):
            continue
        matched.extend(matcher.get("obligation_ids", []))
    return _dedupe_preserve_order(matched)


def _extract_includes(source_text: str) -> list[str]:
    seen = []
    for header in INCLUDE_RE.findall(source_text):
        if header not in seen:
            seen.append(header)
    return seen


def _is_public_binding_header(header: str) -> bool:
    return header.startswith(("linux/", "net/", "uapi/", "asm/", "trace/", "rust/"))


def _collect_rust_net_modules(repo_root: Path) -> list[str]:
    net_dir = repo_root / "rust" / "kernel" / "net"
    if not net_dir.exists():
        return []
    return sorted(str(path.relative_to(repo_root)) for path in net_dir.rglob("*.rs"))


def _collect_existing_rust_modules(repo_root: Path, relpaths: list[str]) -> list[str]:
    collected: list[str] = []
    for entry in relpaths:
        relpath = _implemented_module_path(entry)
        if relpath and _file_exists(repo_root, relpath):
            collected.append(relpath)
    return sorted(_dedupe_preserve_order(collected))


def _artifact_root(
    repo_root: str | Path | None = None,
    artifact_root: str | Path | None = None,
) -> Path:
    repo = _repo_root(repo_root)
    if artifact_root is None:
        return repo / "Documentation" / "rust" / "c2saferust"
    root = Path(artifact_root)
    return root if root.is_absolute() else repo / root


def _artifact_dir_for(
    module_id: str,
    *,
    repo_root: str | Path | None = None,
    artifact_root: str | Path | None = None,
) -> str:
    repo = _repo_root(repo_root)
    return _rel(repo, _artifact_root(repo, artifact_root) / module_id)


def _artifact_path_for(
    module_id: str,
    filename: str,
    *,
    repo_root: str | Path | None = None,
    artifact_root: str | Path | None = None,
) -> str:
    repo = _repo_root(repo_root)
    return _rel(repo, _artifact_root(repo, artifact_root) / module_id / filename)


def module_lifecycle_config_requirements(
    module_path: str | Path | None = None,
    *,
    repo_root: str | Path | None = None,
    artifact_root: str | Path | None = None,
    profile_id: str | None = None,
    source_tree: str | Path | None = None,
) -> dict:
    context = resolve_module_context(
        module_path,
        repo_root=repo_root,
        profile_id=profile_id,
        source_tree=source_tree,
    )
    profile = context["profile"]
    build_validation = profile.get("build_validation", {})
    lifecycle_profile = build_validation.get("module_lifecycle_config", {})
    filename = lifecycle_profile.get("artifact_filename", "module-lifecycle.config")
    required_config_lines = list(lifecycle_profile.get("required_config_lines", []))

    return {
        "schema_version": 1,
        "artifact_type": "module-lifecycle-config-requirements",
        "module_id": context["module_id"],
        "module_c_path": context["module_c_path"],
        "driver_rust_path": context["driver_rust_path"],
        "enabled": bool(required_config_lines),
        "artifact_filename": filename,
        "artifact_path": _artifact_path_for(
            context["module_id"],
            filename,
            repo_root=context["repo_root"],
            artifact_root=artifact_root,
        ),
        "required_config_lines": required_config_lines,
        **_artifact_metadata(profile),
    }


def render_module_lifecycle_config(
    module_path: str | Path | None = None,
    *,
    repo_root: str | Path | None = None,
    artifact_root: str | Path | None = None,
    profile_id: str | None = None,
    source_tree: str | Path | None = None,
) -> str:
    requirements = module_lifecycle_config_requirements(
        module_path,
        repo_root=repo_root,
        artifact_root=artifact_root,
        profile_id=profile_id,
        source_tree=source_tree,
    )
    if not requirements["required_config_lines"]:
        return ""
    return "\n".join(requirements["required_config_lines"]) + "\n"


def _default_command_semantics_verification(completion_mode: str) -> dict:
    if completion_mode == "status-gated":
        return {
            "must_contain": ["run_command_locked"],
            "must_not_contain": ["issue_command_locked"],
        }
    if completion_mode in {"readback-gated", "issue-only"}:
        return {
            "must_contain": ["issue_command_locked"],
            "must_not_contain": ["run_command_locked"],
        }
    raise ValueError(f"Unknown command completion mode {completion_mode!r}")


def _build_command_semantics_entries(profile: dict) -> list[dict]:
    semantics_profile = profile.get("command_semantics", {})
    entries = []
    for command in semantics_profile.get("commands", []):
        defaults = _default_command_semantics_verification(command["completion_mode"])
        verification = dict(command.get("verification", {}))
        entries.append(
            {
                "id": command["id"],
                "helper": command["helper"],
                "command": command["command"],
                "completion_mode": command["completion_mode"],
                "success_witness": command["success_witness"],
                "failure_witness": command["failure_witness"],
                "release_best_effort": command.get("release_best_effort", False),
                "c_evidence": command.get("c_evidence", {}),
                "verification": {
                    "must_contain": _dedupe_preserve_order(
                        [*defaults["must_contain"], *verification.get("must_contain", [])]
                    ),
                    "must_not_contain": _dedupe_preserve_order(
                        [*defaults["must_not_contain"], *verification.get("must_not_contain", [])]
                    ),
                },
            }
        )
    return entries


def _planning_artifact_builders() -> dict[str, object]:
    return {
        "kbuild-plan.json": build_kbuild_plan,
        "binding-gap-audit.json": build_binding_gap_audit,
        "external-header-plan.json": build_external_header_plan,
        "helper-audit.json": build_helper_audit,
        "kbuild-patch-plan.json": build_kbuild_patch_plan,
        "bindings-patch-plan.json": build_bindings_patch_plan,
        "helpers-patch-plan.json": build_helpers_patch_plan,
        "abstraction-plan.json": build_abstraction_plan,
        "unsafe-obligations.json": build_unsafe_obligations,
        "translation-plan.json": build_translation_plan,
        "command-semantics.json": build_command_semantics,
        "safety-policy.json": build_safety_policy,
        "soundness-discharge.json": build_soundness_discharge,
        "agent-workflow-plan.json": build_agent_workflow_plan,
    }


def write_planning_artifacts(
    module_path: str | Path | None = None,
    *,
    repo_root: str | Path | None = None,
    output_dir: str | Path | None = None,
    artifact_root: str | Path | None = None,
    profile_id: str | None = None,
    source_tree: str | Path | None = None,
) -> dict:
    context = resolve_module_context(
        module_path,
        repo_root=repo_root,
        profile_id=profile_id,
        source_tree=source_tree,
    )
    repo = context["repo_root"]
    profile = context["profile"]
    destination = (
        Path(output_dir)
        if output_dir is not None
        else _artifact_root(repo, artifact_root) / profile["module_id"]
    )
    destination.mkdir(parents=True, exist_ok=True)

    generated = []
    for filename, builder in _planning_artifact_builders().items():
        payload = builder(
            module_path,
            repo_root=repo,
            artifact_root=artifact_root,
            profile_id=profile["profile_id"],
            source_tree=source_tree,
        )
        target = destination / filename
        write_json(target, payload)
        generated.append(_rel(repo, target))

    lifecycle_requirements = module_lifecycle_config_requirements(
        module_path,
        repo_root=repo,
        artifact_root=artifact_root,
        profile_id=profile["profile_id"],
        source_tree=source_tree,
    )
    if lifecycle_requirements["enabled"]:
        config_target = destination / lifecycle_requirements["artifact_filename"]
        config_target.write_text(
            render_module_lifecycle_config(
                module_path,
                repo_root=repo,
                artifact_root=artifact_root,
                profile_id=profile["profile_id"],
                source_tree=source_tree,
            )
        )
        generated.append(_rel(repo, config_target))

    return {
        "schema_version": 1,
        "artifact_type": "bootstrap-manifest",
        "module_id": profile["module_id"],
        "profile_id": profile["profile_id"],
        "profile_family": profile["family_id"],
        "generated_artifacts": sorted(_dedupe_preserve_order(generated)),
    }


def _line_number_of_exact_line(text: str, exact_line: str | None) -> int | None:
    if exact_line is None:
        return None
    for line_number, line in enumerate(text.splitlines(), start=1):
        if line == exact_line:
            return line_number
    return None


def _find_kconfig_block_end_line(text: str, symbol: str) -> int | None:
    lines = text.splitlines()
    start_line = _line_number_of_exact_line(text, f"config {symbol}")
    if start_line is None:
        return None

    end_line = start_line
    for line_number in range(start_line + 1, len(lines) + 1):
        line = lines[line_number - 1]
        if line and not line.startswith((" ", "\t")) and re.match(
            r"(config|menuconfig|source|if|endif|comment)\b", line
        ):
            break
        end_line = line_number
    return end_line


def _last_line_number(text: str) -> int | None:
    lines = text.splitlines()
    return len(lines) if lines else None


def _find_makefile_line_for_object(text: str, object_name: str) -> tuple[str | None, int | None]:
    for match in MAKEFILE_ENTRY_RE.finditer(text):
        if match.group(2) == object_name:
            line = match.group(0)
            return line, _line_number_of_exact_line(text, line)
    return None, None


def _extract_brace_body(text: str, open_brace_index: int) -> str:
    depth = 0
    for index in range(open_brace_index, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace_index + 1 : index]
    raise ValueError("Unbalanced braces while extracting C block")


def _extract_function_body(source_text: str, function_name: str) -> str | None:
    match = re.search(
        rf"\b{re.escape(function_name)}\s*\([^;]*?\)\s*\{{",
        source_text,
        re.MULTILINE | re.DOTALL,
    )
    if not match:
        return None
    return _extract_brace_body(source_text, match.end() - 1)


def _find_initializer_name(source_text: str, struct_name: str) -> str | None:
    match = re.search(
        rf"static\s+(?:const\s+)?struct\s+{re.escape(struct_name)}\s+([A-Za-z0-9_]+)[^=]*=\s*\{{",
        source_text,
        re.MULTILINE,
    )
    return match.group(1) if match else None


def _extract_initializer_body(source_text: str, initializer_name: str) -> str | None:
    match = re.search(
        rf"\b{re.escape(initializer_name)}\b[^=]*=\s*\{{",
        source_text,
        re.MULTILINE,
    )
    if not match:
        return None
    return _extract_brace_body(source_text, match.end() - 1)


def _extract_initializer_fields(initializer_body: str | None) -> list[dict]:
    if not initializer_body:
        return []
    return [
        {
            "field": match.group(1),
            "value": match.group(2).strip(),
        }
        for match in INITIALIZER_FIELD_RE.finditer(initializer_body)
    ]


def _initializer_field_map(initializer_body: str | None, *, prefer_first: bool = False) -> dict[str, str]:
    field_map: dict[str, str] = {}
    for entry in _extract_initializer_fields(initializer_body):
        if prefer_first and entry["field"] in field_map:
            continue
        field_map[entry["field"]] = entry["value"]
    return field_map


def _extract_pointer_field_assignments(body: str | None, prefix: str) -> list[dict]:
    if not body:
        return []
    return [
        {
            "field": match.group(1),
            "operator": match.group(2),
            "value": match.group(3).strip(),
        }
        for match in re.finditer(
            rf"{re.escape(prefix)}([A-Za-z0-9_]+)\s*(\|=|=)\s*([^;]+);",
            body,
        )
    ]


def _private_state_source_fields(profile: dict) -> list[str]:
    fields = profile.get("translation", {}).get("private_state", {}).get("fields", [])
    return [
        entry["source_c_field"]
        for entry in fields
        if isinstance(entry, dict) and isinstance(entry.get("source_c_field"), str)
    ]


def _extract_private_member_assignments(body: str | None, source_fields: list[str]) -> list[dict]:
    if not body:
        return []

    assignments: list[dict] = []
    for source_field in source_fields:
        for match in re.finditer(
            rf"\b([A-Za-z_][A-Za-z0-9_]*)->{re.escape(source_field)}\.([A-Za-z0-9_]+)\s*(\|=|=)\s*([^;]+);",
            body,
        ):
            assignments.append(
                {
                    "private_binding": match.group(1),
                    "source_field": source_field,
                    "field": match.group(2),
                    "operator": match.group(3),
                    "value": match.group(4).strip(),
                }
            )
    return assignments


def _extract_call_sites(body: str | None) -> list[str]:
    if not body:
        return []
    seen: list[str] = []
    for match in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", body):
        candidate = match.group(1)
        if candidate in KEYWORD_CALLS or candidate == "this_cpu_ptr":
            continue
        if candidate not in seen:
            seen.append(candidate)
    return seen


def _extract_validate_checks(body: str | None) -> list[dict]:
    if not body:
        return []
    return [
        {
            "field": match.group("field"),
            "return": match.group("retval"),
        }
        for match in VALIDATE_CHECK_RE.finditer(body)
    ]


def _extract_private_struct_fields(source_text: str, struct_name: str) -> list[dict]:
    match = re.search(
        rf"struct\s+{re.escape(struct_name)}\s*\{{(?P<body>.*?)\}};",
        source_text,
        re.MULTILINE | re.DOTALL,
    )
    if not match:
        return []

    fields = []
    for line in match.group("body").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("/*"):
            continue
        field_match = re.match(r"(.+?)\s+([A-Za-z_][A-Za-z0-9_]*)\s*;$", stripped)
        if field_match:
            fields.append(
                {
                    "type": field_match.group(1).strip(),
                    "name": field_match.group(2),
                }
            )
    return fields


def _extract_named_structs(source_text: str, struct_names: list[str]) -> list[dict]:
    structs: list[dict] = []
    seen: set[str] = set()
    for struct_name in struct_names:
        if struct_name in seen:
            continue
        seen.add(struct_name)
        fields = _extract_private_struct_fields(source_text, struct_name)
        if not fields:
            continue
        structs.append(
            {
                "name": struct_name,
                "fields": fields,
            }
        )
    return structs


def _extract_pointer_array_entries(source_text: str, array_name: str) -> list[str]:
    match = re.search(
        rf"\b{re.escape(array_name)}\b\s*\[\]\s*=\s*\{{(?P<body>.*?)\}};",
        source_text,
        re.MULTILINE | re.DOTALL,
    )
    if not match:
        return []

    entries: list[str] = []
    for entry in re.findall(r"&([A-Za-z_][A-Za-z0-9_]*)", match.group("body")):
        if entry not in entries:
            entries.append(entry)
    return entries


def _extract_prefixed_attribute_names(source_text: str, array_name: str, symbol_prefix: str) -> list[str]:
    names: list[str] = []
    for entry in _extract_pointer_array_entries(source_text, array_name):
        if not entry.startswith(symbol_prefix):
            continue
        attribute_name = entry.removeprefix(symbol_prefix)
        if attribute_name and attribute_name not in names:
            names.append(attribute_name)
    return names


def _extract_macro_invocation_names(source_text: str, macro_name: str) -> list[str]:
    names: list[str] = []
    for entry in re.findall(rf"\b{re.escape(macro_name)}\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*,", source_text):
        if entry not in names:
            names.append(entry)
    return names


def _extract_configfs_attribute_callback_maps(source_text: str) -> tuple[dict[str, str], dict[str, str], list[str], list[str]]:
    show_map: dict[str, str] = {}
    store_map: dict[str, str] = {}

    for match in re.finditer(r"\b(memb_group|nullb_device)_([A-Za-z0-9_]+)_(show|store)\s*\(", source_text):
        prefix, attribute_name, callback_kind = match.groups()
        callback_name = f"{prefix}_{attribute_name}_{callback_kind}"
        if callback_kind == "show":
            show_map.setdefault(attribute_name, callback_name)
        else:
            store_map.setdefault(attribute_name, callback_name)

    group_attributes = _extract_prefixed_attribute_names(
        source_text,
        "nullb_group_attrs",
        "memb_group_attr_",
    )
    for attribute_name in group_attributes:
        show_map.setdefault(attribute_name, f"memb_group_{attribute_name}_show")

    device_attributes = _extract_prefixed_attribute_names(
        source_text,
        "nullb_device_attrs",
        "nullb_device_attr_",
    )
    for attribute_name in device_attributes:
        show_map.setdefault(attribute_name, f"nullb_device_{attribute_name}_show")
        store_map.setdefault(attribute_name, f"nullb_device_{attribute_name}_store")

    for attribute_name in _extract_macro_invocation_names(source_text, "NULLB_DEVICE_ATTR"):
        show_map.setdefault(attribute_name, f"nullb_device_{attribute_name}_show")
        store_map.setdefault(attribute_name, f"nullb_device_{attribute_name}_store")
        if attribute_name not in device_attributes:
            device_attributes.append(attribute_name)

    return show_map, store_map, group_attributes, device_attributes


def _helper_symbols_used(source_text: str, helper_wrapper_candidates: dict[str, dict]) -> list[str]:
    return [
        symbol
        for symbol in helper_wrapper_candidates
        if re.search(rf"\b{re.escape(symbol)}\s*\(", source_text)
    ]


def _binding_header_plan(
    header: str,
    binding_header_decisions: dict[str, dict],
    default_decision: dict | None = None,
) -> dict:
    if header in binding_header_decisions:
        return binding_header_decisions[header]
    if default_decision is not None:
        return default_decision
    return {
        "action": "add_to_bindings",
        "reason": "Direct source include is missing from `bindings_helper.h`; no explicit override rule exists.",
        "required_symbols": [],
        "mvp_blocking": False,
    }


def _external_header_plan(
    header: str,
    header_decisions: dict[str, dict],
    default_decision: dict | None = None,
) -> dict:
    if header in header_decisions:
        return header_decisions[header]
    if default_decision is not None:
        return default_decision
    return {
        "action": "manual_review",
        "reason": "Private source header needs an explicit landing or omission decision before driver codegen.",
        "required_symbols": [],
        "mvp_blocking": True,
    }


def _find_external_header_sources(source_root: Path, header: str, *, limit: int = 8) -> list[Path]:
    basename = Path(header).name
    candidates: list[Path] = []

    def _append(path: Path) -> None:
        resolved = path.resolve()
        if path.exists() and resolved not in {item.resolve() for item in candidates}:
            candidates.append(path)

    _append(_path_from_root(source_root, header))
    _append(source_root / basename)

    if len(candidates) < limit and source_root.exists():
        for match in sorted(source_root.rglob(basename)):
            _append(match)
            if len(candidates) >= limit:
                break

    return candidates


def _build_binding_patch_units(bindings_helper_text: str, headers: list[dict], target_path: str) -> list[dict]:
    existing_headers = _extract_includes(bindings_helper_text)
    searchable_headers = [
        header
        for header in existing_headers
        if not header.startswith("../../") and not header.startswith("trace/")
    ]
    grouped: dict[tuple[str | None, str | None], list[dict]] = {}

    for header_entry in headers:
        header = header_entry["header"]
        smaller = [candidate for candidate in searchable_headers if candidate < header]
        larger = [candidate for candidate in searchable_headers if candidate > header]
        insert_after = max(smaller) if smaller else None
        insert_before = min(larger) if larger else None
        grouped.setdefault((insert_after, insert_before), []).append(header_entry)

    patch_units = []
    for (insert_after, insert_before), block_entries in sorted(
        grouped.items(), key=lambda item: [(entry["header"]) for entry in item[1]]
    ):
        insert_lines = [f"#include <{entry['header']}>" for entry in sorted(block_entries, key=lambda item: item["header"])]
        anchor_line = (
            _line_number_of_exact_line(bindings_helper_text, f"#include <{insert_after}>")
            if insert_after
            else _line_number_of_exact_line(bindings_helper_text, f"#include <{insert_before}>")
        )
        patch_units.append(
            {
                "target_path": target_path,
                "operation": "insert_after" if insert_after else "insert_before",
                "anchor": {
                    "header": insert_after or insert_before,
                    "line": anchor_line,
                },
                "insert_lines": insert_lines,
                "headers": [
                    {
                        "header": entry["header"],
                        "reason": entry["reason"],
                        "required_symbols": entry["required_symbols"],
                        "mvp_blocking": entry["mvp_blocking"],
                    }
                    for entry in sorted(block_entries, key=lambda item: item["header"])
                ],
            }
        )
    return patch_units


def _helper_wrapper_specs(symbols: list[str], helper_wrapper_candidates: dict[str, dict]) -> list[dict]:
    return [
        {
            "symbol": symbol,
            **helper_wrapper_candidates[symbol],
        }
        for symbol in sorted(symbols)
    ]


def _build_helper_file_content(wrapper_specs: list[dict]) -> list[str]:
    include_headers = sorted({header for spec in wrapper_specs for header in spec["required_headers"]})
    content = ["// SPDX-License-Identifier: GPL-2.0", ""]
    content.extend(f"#include <{header}>" for header in include_headers)
    content.append("")
    for index, spec in enumerate(wrapper_specs):
        content.append(f"__rust_helper {spec['signature']}")
        content.append("{")
        content.extend(spec["body"])
        content.append("}")
        if index != len(wrapper_specs) - 1:
            content.append("")
    return content


def _helper_include_anchor(helpers_aggregate_text: str, helper_file_name: str) -> tuple[str | None, str | None]:
    include_names = re.findall(r'^#include "([^"]+)"', helpers_aggregate_text, re.MULTILINE)
    previous_candidates = [name for name in include_names if name < helper_file_name]
    next_candidates = [name for name in include_names if name > helper_file_name]
    return (max(previous_candidates) if previous_candidates else None, min(next_candidates) if next_candidates else None)


def _helper_layout(repo: Path) -> dict[str, object]:
    helper_dir = repo / "rust" / "helpers"
    aggregate_path = helper_dir / "helpers.c"
    if aggregate_path.exists():
        helper_files = sorted(path.name for path in helper_dir.glob("*.c"))
        helper_text = "\n".join((helper_dir / name).read_text() for name in helper_files)
        return {
            "helper_dir": helper_dir,
            "aggregate_path": aggregate_path,
            "helper_files": helper_files,
            "helper_text": helper_text,
            "uses_helper_shards": True,
        }

    legacy_aggregate = repo / "rust" / "helpers.c"
    helper_text = legacy_aggregate.read_text() if legacy_aggregate.exists() else ""
    return {
        "helper_dir": repo / "rust",
        "aggregate_path": legacy_aggregate,
        "helper_files": [legacy_aggregate.name] if legacy_aggregate.exists() else [],
        "helper_text": helper_text,
        "uses_helper_shards": False,
    }


def _file_exists(repo_root: Path, relpath: str) -> bool:
    return (repo_root / relpath).exists()


def _read_if_exists(path: Path) -> str:
    return path.read_text() if path.exists() else ""


def _implemented_module_maps(profile: dict) -> tuple[dict[str, object], dict[str, object]]:
    module_map = profile.get("implemented_rust_modules", profile.get("implemented_net_modules", {}))
    return module_map.get("mvp", {}), module_map.get("post_mvp", {})


def _implemented_module_path(descriptor: object) -> str | None:
    if isinstance(descriptor, str):
        return descriptor
    if isinstance(descriptor, dict):
        path = descriptor.get("path")
        return path if isinstance(path, str) else None
    return None


def _module_surface_is_present(repo_root: Path, descriptor: object) -> bool:
    path = _implemented_module_path(descriptor)
    if path is None or not _file_exists(repo_root, path):
        return False
    if not isinstance(descriptor, dict):
        return True

    text = _load_text(_path_from_repo(repo_root, path))
    normalized_text = _normalize_whitespace(text)
    compact_text = _compact_whitespace(text)
    for pattern in descriptor.get("must_contain", []):
        if (
            pattern not in text
            and _normalize_whitespace(pattern) not in normalized_text
            and _compact_whitespace(pattern) not in compact_text
        ):
            return False
    return True


def _implemented_areas(
    repo_root: Path,
    mvp_modules: dict[str, object],
    post_mvp_modules: dict[str, object],
) -> set[str]:
    implemented = set()
    for area, descriptor in {**mvp_modules, **post_mvp_modules}.items():
        if _module_surface_is_present(repo_root, descriptor):
            implemented.add(area)
    return implemented


def _net_scope_assessment(
    profile: dict,
    modules: list[str],
    implemented_areas: set[str],
    mvp_net_modules: dict[str, str],
    module_id: str,
) -> str:
    assessment = _profile_analysis(profile).get("net_scope_assessment", {})
    messages = assessment.get("messages", {})
    baseline_modules = assessment.get("baseline_modules", [])
    implemented_mvp = [area for area in mvp_net_modules if area in implemented_areas]
    implemented_area_list = ", ".join(f"`{area}`" for area in mvp_net_modules)
    implemented_or_default = ", ".join(f"`{area}`" for area in implemented_mvp) or "`phy`"
    if set(mvp_net_modules).issubset(implemented_areas):
        return messages.get(
            "all_implemented",
            "The tree now contains the full smoke-path link-type abstractions ({implemented_areas}); deferred callbacks remain outside the first loop.",
        ).format(
            implemented_areas=implemented_area_list,
            implemented_areas_or_default=implemented_or_default,
            module_id=module_id,
        )
    if modules == baseline_modules:
        return messages.get(
            "baseline_only",
            "The tree currently exposes only `net::phy`; {module_id} needs fresh abstractions for link-type devices.",
        ).format(
            implemented_areas=implemented_area_list,
            implemented_areas_or_default=implemented_or_default,
            module_id=module_id,
        )
    return messages.get(
        "partial",
        "The tree exposes partial Rust net support; the current module still needs the remaining MVP link-type abstractions after {implemented_areas_or_default}.",
    ).format(
        implemented_areas=implemented_area_list,
        implemented_areas_or_default=implemented_or_default,
        module_id=module_id,
    )


def _translation_callback_specs(translation_profile: dict) -> list[dict]:
    specs: list[dict] = []

    def add_spec(source: str, field: str) -> None:
        spec = {"source": source, "field": field}
        if spec not in specs:
            specs.append(spec)

    for collection_name in ("required_callback_fields", "deferred_callback_fields"):
        for spec in translation_profile.get(collection_name, []):
            if isinstance(spec, dict) and "source" in spec and "field" in spec:
                add_spec(spec["source"], spec["field"])

    for callback_key in translation_profile.get("callback_roles", {}):
        if "." not in callback_key:
            continue
        source, field = callback_key.split(".", 1)
        add_spec(source, field)

    return specs


def _extract_callback_table(source_text: str, struct_name: str) -> tuple[str | None, str | None, list[dict], dict[str, str]]:
    initializer_name = _find_initializer_name(source_text, struct_name)
    initializer_body = _extract_initializer_body(source_text, initializer_name) if initializer_name else None
    fields = _extract_initializer_fields(initializer_body)
    return initializer_name, initializer_body, fields, _initializer_field_map(initializer_body)


def _build_link_type_source_inventory(profile: dict, source_text: str, module_id: str) -> tuple[dict, dict[str, dict[str, str]]]:
    translation_profile = profile["translation"]
    rtnl_initializer_name, _rtnl_body, rtnl_fields, rtnl_field_map = _extract_callback_table(
        source_text, "rtnl_link_ops"
    )
    netdev_initializer_name, _netdev_body, netdev_fields, netdev_field_map = _extract_callback_table(
        source_text, "net_device_ops"
    )
    ethtool_initializer_name, _ethtool_body, ethtool_fields, ethtool_field_map = _extract_callback_table(
        source_text, "ethtool_ops"
    )

    setup_body = _extract_function_body(source_text, rtnl_field_map.get("setup", "")) if rtnl_field_map.get("setup") else None
    validate_body = (
        _extract_function_body(source_text, rtnl_field_map.get("validate", ""))
        if rtnl_field_map.get("validate")
        else None
    )
    open_body = (
        _extract_function_body(source_text, netdev_field_map.get("ndo_open", ""))
        if netdev_field_map.get("ndo_open")
        else None
    )
    stop_body = (
        _extract_function_body(source_text, netdev_field_map.get("ndo_stop", ""))
        if netdev_field_map.get("ndo_stop")
        else None
    )
    xmit_body = (
        _extract_function_body(source_text, netdev_field_map.get("ndo_start_xmit", ""))
        if netdev_field_map.get("ndo_start_xmit")
        else None
    )
    stats_body = (
        _extract_function_body(source_text, netdev_field_map.get("ndo_get_stats64", ""))
        if netdev_field_map.get("ndo_get_stats64")
        else None
    )

    field_maps = {
        "rtnl_link_ops": rtnl_field_map,
        "net_device_ops": netdev_field_map,
        "ethtool_ops": ethtool_field_map,
    }

    callback_calls: dict[str, list[str]] = {}
    for spec in _translation_callback_specs(translation_profile):
        symbol = field_maps.get(spec["source"], {}).get(spec["field"])
        body = _extract_function_body(source_text, symbol) if symbol else None
        callback_calls[spec["field"]] = _extract_call_sites(body)

    inventory = {
        "private_struct_fields": _extract_private_struct_fields(source_text, module_id),
        "callback_tables": {
            "rtnl_link_ops": {
                "name": rtnl_initializer_name,
                "fields": rtnl_fields,
            },
            "net_device_ops": {
                "name": netdev_initializer_name,
                "fields": netdev_fields,
            },
            "ethtool_ops": {
                "name": ethtool_initializer_name,
                "fields": ethtool_fields,
            },
        },
        "rtnl_link_ops": {
            "name": rtnl_initializer_name,
            "fields": rtnl_fields,
        },
        "net_device_ops": {
            "name": netdev_initializer_name,
            "fields": netdev_fields,
        },
        "ethtool_ops": {
            "name": ethtool_initializer_name,
            "fields": ethtool_fields,
        },
        "setup_field_writes": _extract_pointer_field_assignments(setup_body, "dev->"),
        "validate_checks": _extract_validate_checks(validate_body),
        "open_calls": _extract_call_sites(open_body),
        "open_tap_assignments": _extract_private_member_assignments(
            open_body,
            _private_state_source_fields(profile),
        ),
        "stop_calls": _extract_call_sites(stop_body),
        "xmit_calls": _extract_call_sites(xmit_body),
        "stats_calls": _extract_call_sites(stats_body),
        "callback_calls": callback_calls,
    }
    return inventory, field_maps


def _build_pci_miscdevice_source_inventory(profile: dict, source_text: str, module_id: str) -> tuple[dict, dict[str, dict[str, str]]]:
    translation_profile = profile["translation"]
    pci_initializer_name, _pci_body, pci_fields, pci_field_map = _extract_callback_table(source_text, "pci_driver")
    fops_initializer_name, _fops_body, fops_fields, fops_field_map = _extract_callback_table(source_text, "file_operations")

    field_maps = {
        "pci_driver": pci_field_map,
        "file_operations": fops_field_map,
    }

    callback_calls: dict[str, list[str]] = {}
    for spec in _translation_callback_specs(translation_profile):
        symbol = field_maps.get(spec["source"], {}).get(spec["field"])
        body = _extract_function_body(source_text, symbol) if symbol else None
        callback_calls[spec["field"]] = _extract_call_sites(body)

    inventory = {
        "private_struct_fields": _extract_private_struct_fields(source_text, module_id),
        "callback_tables": {
            "pci_driver": {
                "name": pci_initializer_name,
                "fields": pci_fields,
            },
            "file_operations": {
                "name": fops_initializer_name,
                "fields": fops_fields,
            },
        },
        "pci_driver": {
            "name": pci_initializer_name,
            "fields": pci_fields,
        },
        "file_operations": {
            "name": fops_initializer_name,
            "fields": fops_fields,
        },
        "setup_field_writes": [],
        "validate_checks": [],
        "open_calls": [],
        "open_tap_assignments": [],
        "stop_calls": [],
        "xmit_calls": [],
        "stats_calls": [],
        "callback_calls": callback_calls,
    }
    return inventory, field_maps


def _build_phy_driver_source_inventory(profile: dict, source_text: str, module_id: str) -> tuple[dict, dict[str, dict[str, str]]]:
    translation_profile = profile["translation"]
    phy_initializer_name = _find_initializer_name(source_text, "phy_driver")
    phy_initializer_body = _extract_initializer_body(source_text, phy_initializer_name) if phy_initializer_name else None
    phy_fields = _extract_initializer_fields(phy_initializer_body)
    phy_field_map = _initializer_field_map(phy_initializer_body, prefer_first=True)

    field_maps = {
        "phy_driver": phy_field_map,
    }

    callback_calls: dict[str, list[str]] = {}
    for spec in _translation_callback_specs(translation_profile):
        symbol = field_maps.get(spec["source"], {}).get(spec["field"])
        body = _extract_function_body(source_text, symbol) if symbol else None
        callback_calls[spec["field"]] = _extract_call_sites(body)

    inventory = {
        "private_struct_fields": _extract_private_struct_fields(source_text, module_id),
        "callback_tables": {
            "phy_driver": {
                "name": phy_initializer_name,
                "fields": phy_fields,
                "field_map": phy_field_map,
            },
        },
        "phy_driver": {
            "name": phy_initializer_name,
            "fields": phy_fields,
            "field_map": phy_field_map,
        },
        "setup_field_writes": [],
        "validate_checks": [],
        "open_calls": [],
        "open_tap_assignments": [],
        "stop_calls": [],
        "xmit_calls": [],
        "stats_calls": [],
        "callback_calls": callback_calls,
    }
    return inventory, field_maps


def _build_block_null_source_inventory(
    profile: dict,
    source_text: str,
    module_id: str,
) -> tuple[dict, dict[str, dict[str, str]]]:
    translation_profile = profile["translation"]
    blk_mq_initializer_name, blk_mq_body, blk_mq_fields, blk_mq_field_map = _extract_callback_table(
        source_text, "blk_mq_ops"
    )
    configfs_group_initializer_name, configfs_group_body, configfs_group_fields, configfs_group_field_map = (
        _extract_callback_table(source_text, "configfs_group_operations")
    )
    configfs_item_initializer_name, configfs_item_body, configfs_item_fields, configfs_item_field_map = (
        _extract_callback_table(source_text, "configfs_item_operations")
    )
    private_struct_names = _profile_analysis(profile).get(
        "private_struct_names",
        ["nullb_device", "nullb", "nullb_queue", "nullb_cmd"],
    )
    private_structs = _extract_named_structs(source_text, private_struct_names)
    configfs_show_map, configfs_store_map, group_attributes, device_attributes = (
        _extract_configfs_attribute_callback_maps(source_text)
    )

    field_maps = {
        "blk_mq_ops": blk_mq_field_map,
        "configfs_group_operations": configfs_group_field_map,
        "configfs_item_operations": configfs_item_field_map,
        "configfs_show": configfs_show_map,
        "configfs_store": configfs_store_map,
    }

    callback_calls: dict[str, list[str]] = {}
    for spec in _translation_callback_specs(translation_profile):
        symbol = field_maps.get(spec["source"], {}).get(spec["field"])
        body = _extract_function_body(source_text, symbol) if symbol else None
        callback_calls[spec["field"]] = _extract_call_sites(body)

    make_group_body = (
        _extract_function_body(source_text, configfs_group_field_map.get("make_group", ""))
        if configfs_group_field_map.get("make_group")
        else None
    )
    drop_item_body = (
        _extract_function_body(source_text, configfs_group_field_map.get("drop_item", ""))
        if configfs_group_field_map.get("drop_item")
        else None
    )
    queue_rq_body = (
        _extract_function_body(source_text, blk_mq_field_map.get("queue_rq", ""))
        if blk_mq_field_map.get("queue_rq")
        else None
    )
    complete_body = (
        _extract_function_body(source_text, blk_mq_field_map.get("complete", ""))
        if blk_mq_field_map.get("complete")
        else None
    )
    power_store_body = _extract_function_body(source_text, configfs_store_map.get("power", ""))
    power_show_body = _extract_function_body(source_text, configfs_show_map.get("power", ""))

    inventory = {
        "private_struct_fields": private_structs[0]["fields"] if private_structs else [],
        "private_structs": private_structs,
        "callback_tables": {
            "blk_mq_ops": {
                "name": blk_mq_initializer_name,
                "fields": blk_mq_fields,
                "field_map": blk_mq_field_map,
            },
            "configfs_group_operations": {
                "name": configfs_group_initializer_name,
                "fields": configfs_group_fields,
                "field_map": configfs_group_field_map,
            },
            "configfs_item_operations": {
                "name": configfs_item_initializer_name,
                "fields": configfs_item_fields,
                "field_map": configfs_item_field_map,
            },
            "configfs_show": {
                "name": "configfs_show",
                "fields": [{"field": key, "value": value} for key, value in configfs_show_map.items()],
                "field_map": configfs_show_map,
            },
            "configfs_store": {
                "name": "configfs_store",
                "fields": [{"field": key, "value": value} for key, value in configfs_store_map.items()],
                "field_map": configfs_store_map,
            },
        },
        "blk_mq_ops": {
            "name": blk_mq_initializer_name,
            "fields": blk_mq_fields,
            "field_map": blk_mq_field_map,
        },
        "configfs_group_operations": {
            "name": configfs_group_initializer_name,
            "fields": configfs_group_fields,
            "field_map": configfs_group_field_map,
        },
        "configfs_item_operations": {
            "name": configfs_item_initializer_name,
            "fields": configfs_item_fields,
            "field_map": configfs_item_field_map,
        },
        "configfs_show": {
            "name": "configfs_show",
            "fields": [{"field": key, "value": value} for key, value in configfs_show_map.items()],
            "field_map": configfs_show_map,
        },
        "configfs_store": {
            "name": "configfs_store",
            "fields": [{"field": key, "value": value} for key, value in configfs_store_map.items()],
            "field_map": configfs_store_map,
        },
        "setup_field_writes": [],
        "validate_checks": [],
        "open_calls": [],
        "open_tap_assignments": [],
        "stop_calls": [],
        "xmit_calls": [],
        "stats_calls": [],
        "callback_calls": callback_calls,
        "configfs": {
            "group_attributes": group_attributes,
            "device_attributes": device_attributes,
            "make_group_calls": _extract_call_sites(make_group_body),
            "drop_item_calls": _extract_call_sites(drop_item_body),
            "power_show_calls": _extract_call_sites(power_show_body),
            "power_store_calls": _extract_call_sites(power_store_body),
        },
        "blk_mq": {
            "queue_rq_calls": _extract_call_sites(queue_rq_body),
            "complete_calls": _extract_call_sites(complete_body),
        },
    }
    return inventory, field_maps


def _build_source_inventory(profile: dict, source_text: str, module_id: str) -> tuple[dict, dict[str, dict[str, str]]]:
    source_model = _profile_analysis(profile).get("source_model", "link_type_rtnl")
    if source_model == "pci_miscdevice":
        return _build_pci_miscdevice_source_inventory(profile, source_text, module_id)
    if source_model == "phy_driver":
        return _build_phy_driver_source_inventory(profile, source_text, module_id)
    if source_model == "block_null":
        return _build_block_null_source_inventory(profile, source_text, module_id)
    return _build_link_type_source_inventory(profile, source_text, module_id)


def _driver_build_targets(context: dict, kbuild_plan: dict) -> tuple[str, str]:
    module_dir = Path(context["module_dir"])
    object_name = Path(kbuild_plan["suggested_rust_object"]).name
    object_path = module_dir / object_name
    return str(object_path), str(object_path.with_suffix(".ko"))


def build_kbuild_plan(
    module_path: str | Path | None = None,
    *,
    repo_root: str | Path | None = None,
    artifact_root: str | Path | None = None,
    profile_id: str | None = None,
    source_tree: str | Path | None = None,
) -> dict:
    context = resolve_module_context(
        module_path,
        repo_root=repo_root,
        profile_id=profile_id,
        source_tree=source_tree,
    )
    repo = context["repo_root"]
    profile = context["profile"]
    module = context["module_source_path"]
    module_dir = _path_from_repo(repo, context["module_dir"])
    makefile = _path_from_repo(repo, context["makefile_path"])
    kconfig = _path_from_repo(repo, context["kconfig_path"])

    module_id = context["module_id"]
    kbuild_profile = profile.get("kbuild", {})
    mode = kbuild_profile.get("mode", "replace-existing-c-object")
    module_object = module.with_suffix(".o").name
    makefile_text = _load_text(makefile)
    kconfig_text = _load_text(kconfig)

    source_config_symbol = kbuild_profile.get("source_config_symbol", module_id.upper().replace("-", "_"))
    current_makefile_line = None
    for match in MAKEFILE_ENTRY_RE.finditer(makefile_text):
        if match.group(2) == module_object:
            source_config_symbol = match.group(1)
            current_makefile_line = match.group(0)
            break

    if mode == "add-new-driver":
        driver_config_symbol = kbuild_profile.get("driver_config_symbol", source_config_symbol)
        driver_object = kbuild_profile.get("driver_object", Path(context["driver_rust_path"]).with_suffix(".o").name)
        current_makefile_line = next(
            (match.group(0) for match in MAKEFILE_ENTRY_RE.finditer(makefile_text) if match.group(2) == driver_object),
            current_makefile_line,
        )
        current_kconfig_present = re.search(rf"^config {driver_config_symbol}$", kconfig_text, re.MULTILINE) is not None
        current_driver_entry_present = current_makefile_line is not None and current_kconfig_present
        rust_symbol = driver_config_symbol
        suggested_kconfig = [
            f"config {driver_config_symbol}",
            f'\ttristate "{kbuild_profile.get("driver_prompt", f"Rust driver for {module_id}")}"',
            f"\tdepends on {kbuild_profile.get('driver_depends_on', 'RUST')}",
            "\thelp",
            *[f"\t  {line}" for line in kbuild_profile.get("driver_help", [f"Build the Rust {module_id} driver."])],
        ]
        suggested_makefile = [f"obj-$(CONFIG_{driver_config_symbol}) += {driver_object}"]
        next_action = (
            "Landing Kbuild entries already exist; keep downstream patch/translation artifacts aligned."
            if current_driver_entry_present
            else f"Add the new Rust-only Kbuild entries for `{module_id}` before generating driver patches."
        )
        current_state = {
            "current_makefile_line": current_makefile_line,
            "current_kconfig_present": current_kconfig_present,
            "current_rust_switch_present": current_driver_entry_present,
            "current_driver_entry_present": current_driver_entry_present,
        }
        suggested_object = driver_object
        source_config_symbol = driver_config_symbol
    else:
        rust_symbol = kbuild_profile.get("rust_config_symbol", f"{source_config_symbol}_RUST")
        current_rust_switch_present = (
            f"CONFIG_{rust_symbol}" in makefile_text
            or re.search(rf"^config {rust_symbol}$", kconfig_text, re.MULTILINE) is not None
        )
        rust_prompt = kbuild_profile.get("rust_config_prompt", f"Rust implementation of {module_id}")
        rust_depends_on = kbuild_profile.get("rust_depends_on", f"RUST && {source_config_symbol}")
        rust_help = kbuild_profile.get(
            "rust_help",
            [
                f"Builds the Rust implementation of {module_id} ({module_id}_rust.ko)",
                f"instead of the original C implementation ({module_id}.ko).",
            ],
        )
        suggested_kconfig = [
            f"config {rust_symbol}",
            f'\tbool "{rust_prompt}"',
            f"\tdepends on {rust_depends_on}",
            "\thelp",
            *[f"\t  {line}" for line in rust_help],
        ]
        suggested_makefile = [
            f"ifdef CONFIG_{rust_symbol}",
            f"  obj-$(CONFIG_{source_config_symbol}) += {module_id}_rust.o",
            "else",
            f"  obj-$(CONFIG_{source_config_symbol}) += {module_id}.o",
            "endif",
        ]
        next_action = (
            "Kbuild switch already exists; keep downstream patch/translation artifacts aligned."
            if current_rust_switch_present
            else f"Apply the Kbuild switch before generating {module_id} Rust driver patches."
        )
        current_state = {
            "current_makefile_line": current_makefile_line,
            "current_kconfig_present": re.search(
                rf"^config {source_config_symbol}$", kconfig_text, re.MULTILINE
            )
            is not None,
            "current_rust_switch_present": current_rust_switch_present,
        }
        suggested_object = f"{module_id}_rust.o"

    reference_pattern = kbuild_profile.get(
        "reference_pattern",
        {
            "kconfig_path": "drivers/net/phy/Kconfig",
            "makefile_path": "drivers/net/phy/Makefile",
            "rust_driver_path": "drivers/net/phy/ax88796b_rust.rs",
        },
    )

    return {
        "schema_version": 1,
        "artifact_type": "kbuild-plan",
        "module_id": module_id,
        "module_c_path": context["module_c_path"],
        "module_dir": _rel(repo, module_dir),
        "kconfig_path": _rel(repo, kconfig),
        "makefile_path": _rel(repo, makefile),
        "kbuild_mode": mode,
        "source_config_symbol": source_config_symbol,
        "suggested_rust_config_symbol": rust_symbol,
        "suggested_rust_object": suggested_object,
        "current_state": current_state,
        "reference_pattern": reference_pattern,
        "suggested_kconfig_snippet": suggested_kconfig,
        "suggested_makefile_snippet": suggested_makefile,
        "next_action": next_action,
        **_artifact_metadata(profile),
    }


def build_binding_gap_audit(
    module_path: str | Path | None = None,
    *,
    repo_root: str | Path | None = None,
    artifact_root: str | Path | None = None,
    profile_id: str | None = None,
    source_tree: str | Path | None = None,
) -> dict:
    context = resolve_module_context(
        module_path,
        repo_root=repo_root,
        profile_id=profile_id,
        source_tree=source_tree,
    )
    repo = context["repo_root"]
    profile = context["profile"]
    direct_ffi_candidates = profile["direct_ffi_candidates"]
    helper_wrapper_candidates = profile["helper_wrapper_candidates"]
    abstraction_requirements = profile["abstraction_requirements"]
    module = context["module_source_path"]
    source_text = _load_text(module)
    bindings_helper = repo / "rust" / "bindings" / "bindings_helper.h"
    bindings_helper_text = _load_text(bindings_helper)

    includes = _extract_includes(source_text)
    public_includes = [header for header in includes if _is_public_binding_header(header)]
    private_source_headers = [header for header in includes if header not in public_includes]
    helper_includes = set(_extract_includes(bindings_helper_text))
    missing_headers = [header for header in public_includes if header not in helper_includes]
    existing_headers = [header for header in public_includes if header in helper_includes]

    direct_ffi_symbols = [
        symbol for symbol in direct_ffi_candidates if re.search(rf"\b{symbol}\s*\(", source_text)
    ]
    helper_gap_symbols = _helper_symbols_used(source_text, helper_wrapper_candidates)

    abstraction_gaps = []
    for area, metadata in abstraction_requirements.items():
        if any(pattern in source_text for pattern in metadata["patterns"]):
            abstraction_gaps.append(
                {
                    "area": area,
                    "reason": metadata["reason"],
                }
            )

    mvp_modules, post_mvp_modules = _implemented_module_maps(profile)
    current_rust_modules = _collect_existing_rust_modules(
        repo,
        list({**mvp_modules, **post_mvp_modules}.values()),
    )

    return {
        "schema_version": 1,
        "artifact_type": "binding-gap-audit",
        "module_id": context["module_id"],
        "module_c_path": context["module_c_path"],
        "bindings_helper_path": _rel(repo, bindings_helper),
        "included_headers": includes,
        "private_source_headers": private_source_headers,
        "headers_already_covered": existing_headers,
        "candidate_missing_binding_headers": missing_headers,
        "direct_ffi_symbols": [
            {
                "symbol": symbol,
                "reason": direct_ffi_candidates[symbol],
            }
            for symbol in direct_ffi_symbols
        ],
        "helper_gap_symbols": [
            {
                "symbol": symbol,
                "reason": helper_wrapper_candidates[symbol]["reason"],
            }
            for symbol in helper_gap_symbols
        ],
        "required_rust_abstractions": abstraction_gaps,
        "current_rust_modules": current_rust_modules,
        "current_rust_net_modules": _collect_rust_net_modules(repo),
        "tool_assessment": {
            "requires_new_rust_abstractions": bool(abstraction_gaps),
            "requires_new_rust_net_abstractions": bool(abstraction_gaps) if profile["family_id"] == "net-link-type" else False,
            "stage_2_can_be_driven_statically": True,
        },
        **_artifact_metadata(profile),
    }


def build_external_header_plan(
    module_path: str | Path | None = None,
    *,
    repo_root: str | Path | None = None,
    artifact_root: str | Path | None = None,
    profile_id: str | None = None,
    source_tree: str | Path | None = None,
) -> dict:
    context = resolve_module_context(
        module_path,
        repo_root=repo_root,
        profile_id=profile_id,
        source_tree=source_tree,
    )
    repo = context["repo_root"]
    source_root = context["source_root"]
    profile = context["profile"]
    header_decisions = profile.get("external_header_decisions", {})
    default_header_decision = profile.get("default_external_header_decision")

    audit = build_binding_gap_audit(
        module_path,
        repo_root=repo,
        artifact_root=artifact_root,
        profile_id=profile["profile_id"],
        source_tree=source_tree,
    )

    header_plans = []
    blocking_headers = []
    for header in audit["private_source_headers"]:
        decision = _external_header_plan(header, header_decisions, default_header_decision)
        source_matches = _find_external_header_sources(source_root, header)
        landing_path = decision.get("landing_path")
        landing_exists = bool(landing_path and _file_exists(repo, landing_path))
        action = decision["action"]

        if action in {"ignore_private_header", "omit_from_upstream"}:
            status = "no-landing-required"
        elif action == "manual_review":
            status = "manual-review"
        elif action == "translate_to_rust_defs":
            status = "rust-translation-pending"
        else:
            status = "already_applied" if landing_exists else "pending"

        entry = {
            "header": header,
            "action": action,
            "status": status,
            "reason": decision["reason"],
            "required_symbols": decision.get("required_symbols", []),
            "mvp_blocking": decision.get("mvp_blocking", False),
            "rust_strategy": decision.get("rust_strategy"),
            "reference_urls": decision.get("reference_urls", []),
            "source_present": bool(source_matches),
            "source_matches": [_rel(source_root, path) for path in source_matches],
            "landing": {
                "path": landing_path,
                "kind": decision.get("landing_kind"),
                "exists": landing_exists,
            }
            if landing_path or decision.get("landing_kind")
            else None,
        }
        header_plans.append(entry)

        if entry["mvp_blocking"] and status in {"pending", "manual-review", "rust-translation-pending"}:
            blocking_headers.append(header)

    if not header_plans:
        overall_status = "not-needed"
    elif blocking_headers:
        overall_status = "pending"
    else:
        overall_status = "ready"

    return {
        "schema_version": 1,
        "artifact_type": "external-header-plan",
        "module_id": context["module_id"],
        "module_c_path": context["module_c_path"],
        "source_tree_role": context["source_tree_role"],
        "source_tree": context["source_tree"],
        "inputs": {
            "binding_gap_audit": _artifact_path_for(
                context["module_id"],
                "binding-gap-audit.json",
                repo_root=repo,
                artifact_root=artifact_root,
            ),
        },
        "status": overall_status,
        "private_source_headers": audit["private_source_headers"],
        "blocking_headers": blocking_headers,
        "header_plans": sorted(header_plans, key=lambda entry: entry["header"]),
        "tool_assessment": {
            "requires_external_header_landing": bool(header_plans),
            "ready_for_driver_codegen": not blocking_headers,
        },
        **_artifact_metadata(profile),
    }


def build_helper_audit(
    module_path: str | Path | None = None,
    *,
    repo_root: str | Path | None = None,
    artifact_root: str | Path | None = None,
    profile_id: str | None = None,
    source_tree: str | Path | None = None,
) -> dict:
    context = resolve_module_context(
        module_path,
        repo_root=repo_root,
        profile_id=profile_id,
        source_tree=source_tree,
    )
    repo = context["repo_root"]
    profile = context["profile"]
    helper_wrapper_candidates = profile["helper_wrapper_candidates"]
    module = context["module_source_path"]
    source_text = _load_text(module)
    helper_layout = _helper_layout(repo)
    helper_dir = helper_layout["helper_dir"]
    helper_files = helper_layout["helper_files"]
    helper_text = helper_layout["helper_text"]

    helper_gap_symbols = _helper_symbols_used(source_text, helper_wrapper_candidates)
    existing_net_helpers = [
        symbol
        for symbol in helper_gap_symbols
        if re.search(rf"\b{symbol}\b", helper_text) or re.search(rf"\brust_helper_{symbol}\b", helper_text)
    ]
    missing_helper_wrappers = [symbol for symbol in helper_gap_symbols if symbol not in existing_net_helpers]

    return {
        "schema_version": 1,
        "artifact_type": "helper-audit",
        "module_id": context["module_id"],
        "module_c_path": context["module_c_path"],
        "helper_dir": _rel(repo, helper_dir),
        "helper_aggregate_path": _rel(repo, helper_layout["aggregate_path"]),
        "uses_helper_shards": helper_layout["uses_helper_shards"],
        "existing_helper_files": helper_files,
        "required_helper_wrappers": [
            {
                "symbol": symbol,
                "reason": helper_wrapper_candidates[symbol]["reason"],
            }
            for symbol in helper_gap_symbols
        ],
        "helpers_already_present": existing_net_helpers,
        "missing_helper_wrappers": missing_helper_wrappers,
        **_artifact_metadata(profile),
    }


def build_kbuild_patch_plan(
    module_path: str | Path | None = None,
    *,
    repo_root: str | Path | None = None,
    artifact_root: str | Path | None = None,
    profile_id: str | None = None,
    source_tree: str | Path | None = None,
) -> dict:
    context = resolve_module_context(
        module_path,
        repo_root=repo_root,
        profile_id=profile_id,
        source_tree=source_tree,
    )
    repo = context["repo_root"]
    profile = context["profile"]
    kbuild_profile = profile.get("kbuild", {})
    kbuild_plan = build_kbuild_plan(
        module_path,
        repo_root=repo,
        artifact_root=artifact_root,
        profile_id=profile["profile_id"],
        source_tree=source_tree,
    )
    makefile = _path_from_repo(repo, kbuild_plan["makefile_path"])
    kconfig = _path_from_repo(repo, kbuild_plan["kconfig_path"])
    makefile_text = _load_text(makefile)
    kconfig_text = _load_text(kconfig)

    current_makefile_line = kbuild_plan["current_state"]["current_makefile_line"]
    makefile_line = _line_number_of_exact_line(makefile_text, current_makefile_line)
    kbuild_mode = kbuild_plan.get("kbuild_mode", "replace-existing-c-object")
    patch_required = not kbuild_plan["current_state"]["current_rust_switch_present"]
    patch_units = []
    if patch_required:
        if kbuild_mode == "add-new-driver":
            kconfig_anchor_symbol = kbuild_profile.get("kconfig_insert_after_symbol")
            kconfig_anchor_line = (
                _find_kconfig_block_end_line(kconfig_text, kconfig_anchor_symbol)
                if kconfig_anchor_symbol
                else _last_line_number(kconfig_text)
            )
            makefile_anchor_text = kbuild_profile.get("makefile_insert_after")
            makefile_anchor_line = _line_number_of_exact_line(makefile_text, makefile_anchor_text)
            patch_units = [
                {
                    "target_path": kbuild_plan["kconfig_path"],
                    "operation": "insert_after",
                    "anchor": {
                        "symbol": kconfig_anchor_symbol,
                        "line": kconfig_anchor_line,
                    },
                    "insert_lines": kbuild_plan["suggested_kconfig_snippet"],
                    "reason": "Add the Rust-only config entry for the new landing driver.",
                },
                {
                    "target_path": kbuild_plan["makefile_path"],
                    "operation": "insert_after",
                    "anchor": {
                        "line": makefile_anchor_line,
                        "text": makefile_anchor_text,
                    },
                    "insert_lines": kbuild_plan["suggested_makefile_snippet"],
                    "reason": "Add the Rust-only object line for the new landing driver.",
                },
            ]
        else:
            kconfig_insert_after_line = _find_kconfig_block_end_line(
                kconfig_text, kbuild_plan["source_config_symbol"]
            )
            patch_units = [
                {
                    "target_path": kbuild_plan["kconfig_path"],
                    "operation": "insert_after",
                    "anchor": {
                        "symbol": kbuild_plan["source_config_symbol"],
                        "line": kconfig_insert_after_line,
                    },
                    "insert_lines": kbuild_plan["suggested_kconfig_snippet"],
                    "reason": f"Introduce a Rust switch without replacing the existing `{kbuild_plan['source_config_symbol']}` user-facing symbol.",
                },
                {
                    "target_path": kbuild_plan["makefile_path"],
                    "operation": "replace_exact_line",
                    "anchor": {
                        "line": makefile_line,
                        "text": current_makefile_line,
                    },
                    "replacement_lines": kbuild_plan["suggested_makefile_snippet"],
                    "reason": (
                        f"Select `{kbuild_plan['suggested_rust_object']}` only when "
                        f"`CONFIG_{kbuild_plan['suggested_rust_config_symbol']}=y`; otherwise keep the C object."
                    ),
                },
            ]

    return {
        "schema_version": 1,
        "artifact_type": "kbuild-patch-plan",
        "module_id": kbuild_plan["module_id"],
        "module_c_path": kbuild_plan["module_c_path"],
        "kbuild_mode": kbuild_mode,
        "patch_required": patch_required,
        "status": "pending" if patch_required else "already_applied",
        "inputs": {
            "kbuild_plan": _artifact_path_for(
                kbuild_plan["module_id"],
                "kbuild-plan.json",
                repo_root=repo,
                artifact_root=artifact_root,
            ),
        },
        "patch_units": patch_units,
        "validation_checks": [
            {
                "path": kbuild_plan["kconfig_path"],
                "contains": f"config {kbuild_plan['suggested_rust_config_symbol']}",
            },
            *(
                [
                    {
                        "path": kbuild_plan["makefile_path"],
                        "contains": f"obj-$(CONFIG_{kbuild_plan['source_config_symbol']}) += {kbuild_plan['suggested_rust_object']}",
                    }
                ]
                if kbuild_mode == "add-new-driver"
                else [
                    {
                        "path": kbuild_plan["makefile_path"],
                        "contains": f"ifdef CONFIG_{kbuild_plan['suggested_rust_config_symbol']}",
                    },
                    {
                        "path": kbuild_plan["makefile_path"],
                        "contains": f"obj-$(CONFIG_{kbuild_plan['source_config_symbol']}) += {kbuild_plan['suggested_rust_object']}",
                    },
                ]
            ),
        ],
    }


def build_bindings_patch_plan(
    module_path: str | Path | None = None,
    *,
    repo_root: str | Path | None = None,
    artifact_root: str | Path | None = None,
    profile_id: str | None = None,
    source_tree: str | Path | None = None,
) -> dict:
    context = resolve_module_context(
        module_path,
        repo_root=repo_root,
        profile_id=profile_id,
        source_tree=source_tree,
    )
    repo = context["repo_root"]
    profile = context["profile"]
    binding_header_decisions = profile["bindings_header_decisions"]
    default_binding_header_decision = profile.get("default_binding_header_decision")
    audit = build_binding_gap_audit(
        module_path,
        repo_root=repo,
        artifact_root=artifact_root,
        profile_id=profile["profile_id"],
        source_tree=source_tree,
    )
    bindings_helper = _path_from_repo(repo, audit["bindings_helper_path"])
    bindings_helper_text = _load_text(bindings_helper)

    binding_additions = []
    deferred_headers = []
    rust_abstraction_headers = []
    for header in audit["candidate_missing_binding_headers"]:
        decision = _binding_header_plan(header, binding_header_decisions, default_binding_header_decision)
        entry = {
            "header": header,
            "reason": decision["reason"],
            "required_symbols": decision["required_symbols"],
            "mvp_blocking": decision["mvp_blocking"],
        }
        action = decision["action"]
        if action == "add_to_bindings":
            binding_additions.append(entry)
        elif action == "defer_to_abstraction":
            deferred_headers.append(entry)
        else:
            rust_abstraction_headers.append(entry)

    patch_units = _build_binding_patch_units(
        bindings_helper_text,
        binding_additions,
        audit["bindings_helper_path"],
    )
    patch_required = bool(binding_additions)

    return {
        "schema_version": 1,
        "artifact_type": "bindings-patch-plan",
        "module_id": audit["module_id"],
        "module_c_path": audit["module_c_path"],
        "bindings_helper_path": audit["bindings_helper_path"],
        "patch_required": patch_required,
        "status": "pending" if patch_required else "already_applied",
        "inputs": {
            "binding_gap_audit": _artifact_path_for(
                audit["module_id"],
                "binding-gap-audit.json",
                repo_root=repo,
                artifact_root=artifact_root,
            ),
        },
        "style_constraints": profile.get("planning", {}).get(
            "bindings_patch_style_constraints",
            [
                "Keep the top include list in `bindings_helper.h` alphabetically ordered.",
                "Prefer exposing only headers needed by the current abstraction/driver plan; do not mirror every C include blindly.",
            ],
        ),
        "binding_additions": sorted(binding_additions, key=lambda entry: entry["header"]),
        "deferred_headers": sorted(deferred_headers, key=lambda entry: entry["header"]),
        "rust_abstraction_headers": sorted(rust_abstraction_headers, key=lambda entry: entry["header"]),
        "patch_units": patch_units,
        "validation_checks": [
            {
                "path": audit["bindings_helper_path"],
                "contains": f"#include <{entry['header']}>",
            }
            for entry in sorted(binding_additions, key=lambda item: item["header"])
        ],
        **_artifact_metadata(profile),
    }


def build_helpers_patch_plan(
    module_path: str | Path | None = None,
    *,
    repo_root: str | Path | None = None,
    artifact_root: str | Path | None = None,
    profile_id: str | None = None,
    source_tree: str | Path | None = None,
) -> dict:
    context = resolve_module_context(
        module_path,
        repo_root=repo_root,
        profile_id=profile_id,
        source_tree=source_tree,
    )
    repo = context["repo_root"]
    profile = context["profile"]
    helper_wrapper_candidates = profile["helper_wrapper_candidates"]
    helper_audit = build_helper_audit(
        module_path,
        repo_root=repo,
        artifact_root=artifact_root,
        profile_id=profile["profile_id"],
        source_tree=source_tree,
    )
    helpers_aggregate = _path_from_root(repo, helper_audit["helper_aggregate_path"])
    aggregate_text = _load_text(helpers_aggregate) if helpers_aggregate.exists() else ""
    wrapper_specs = _helper_wrapper_specs(
        helper_audit["missing_helper_wrappers"],
        helper_wrapper_candidates,
    )
    helper_file_name = profile.get("planning", {}).get("helper_file_name", "net.c")
    insert_after, insert_before = _helper_include_anchor(aggregate_text, helper_file_name)
    if insert_after:
        anchor_line = _line_number_of_exact_line(aggregate_text, f'#include "{insert_after}"')
    elif insert_before:
        anchor_line = _line_number_of_exact_line(aggregate_text, f'#include "{insert_before}"')
    else:
        anchor_line = _line_number_of_pattern(aggregate_text, r"^#include <")
    patch_units = []
    if wrapper_specs:
        include_target = helper_audit["helper_aggregate_path"]
        patch_units = [
            {
                "target_path": f"{helper_audit['helper_dir']}/{helper_file_name}",
                "operation": "create_file",
                "reason": "Add the missing network-specific helper wrappers as a dedicated helper shard.",
                "content_lines": _build_helper_file_content(wrapper_specs),
            },
            {
                "target_path": include_target,
                "operation": "insert_after" if insert_after else "insert_before",
                "anchor": {
                    "file": insert_after or insert_before or helpers_aggregate.name,
                    "line": anchor_line,
                },
                "insert_lines": [f'#include "{helper_file_name}"'],
                "reason": "Compile the new helper shard through the existing aggregate translation unit while keeping the helper include list ordered when that list exists.",
            },
        ]

    return {
        "schema_version": 1,
        "artifact_type": "helpers-patch-plan",
        "module_id": helper_audit["module_id"],
        "module_c_path": helper_audit["module_c_path"],
        "helper_dir": helper_audit["helper_dir"],
        "patch_required": bool(wrapper_specs),
        "status": "pending" if wrapper_specs else "already_applied",
        "inputs": {
            "helper_audit": _artifact_path_for(
                helper_audit["module_id"],
                "helper-audit.json",
                repo_root=repo,
                artifact_root=artifact_root,
            ),
        },
        "wrapper_specs": wrapper_specs,
        "no_makefile_change_required": True,
        "patch_units": patch_units,
        "validation_checks": [
            {
                "path": f"{helper_audit['helper_dir']}/{helper_file_name}",
                "contains": spec["helper_name"],
            }
            for spec in wrapper_specs
        ]
        + (
            [
                {
                    "path": helper_audit["helper_aggregate_path"],
                    "contains": f'#include "{helper_file_name}"',
                }
            ]
            if wrapper_specs
            else []
        ),
        **_artifact_metadata(profile),
    }


def _callback_symbols_from_specs(field_maps: dict[str, dict[str, str]], specs: list[dict]) -> list[str]:
    symbols: list[str] = []
    for spec in specs:
        symbol = field_maps.get(spec["source"], {}).get(spec["field"])
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    return symbols


def _depends_on_paths(
    module_id: str,
    metadata: dict,
    *,
    repo_root: str | Path | None = None,
    artifact_root: str | Path | None = None,
) -> list[str]:
    return [
        _artifact_path_for(
            module_id,
            filename,
            repo_root=repo_root,
            artifact_root=artifact_root,
        )
        for filename in metadata.get("depends_on_artifacts", [])
    ]


def _artifact_output_path_for_targets(
    module_id: str,
    target_name: str,
    *,
    repo_root: str | Path | None = None,
    artifact_root: str | Path | None = None,
) -> str:
    return _artifact_path_for(
        module_id,
        f"{target_name}.json",
        repo_root=repo_root,
        artifact_root=artifact_root,
    )


def _load_oracle_feedback(
    repo_root: Path,
    module_id: str,
    *,
    artifact_root: str | Path | None = None,
) -> dict | None:
    feedback_path = _path_from_repo(
        repo_root,
        _artifact_path_for(
            module_id,
            "oracle-feedback.json",
            repo_root=repo_root,
            artifact_root=artifact_root,
        ),
    )
    if not feedback_path.exists():
        return None
    return json.loads(feedback_path.read_text())


def _feedback_notes(feedback: dict | None, artifact_type: str) -> list[str]:
    if not feedback:
        return []
    notes = []
    for action in feedback.get("actions", []):
        if action.get("type") != "add_note":
            continue
        if artifact_type in action.get("targets", []):
            notes.append(action["message"])
    return notes


def _feedback_blockers(feedback: dict | None, artifact_type: str) -> list[str]:
    if not feedback:
        return []
    blockers = []
    for action in feedback.get("actions", []):
        if action.get("type") != "add_blocker":
            continue
        if artifact_type in action.get("targets", []):
            blockers.append(action["message"])
    return blockers


def _feedback_promoted_callbacks(feedback: dict | None) -> set[tuple[str, str]]:
    if not feedback:
        return set()
    return {
        (action["source"], action["field"])
        for action in feedback.get("actions", [])
        if action.get("type") == "promote_callback"
    }


def _feedback_promoted_areas(feedback: dict | None) -> set[str]:
    if not feedback:
        return set()
    return {
        action["area"]
        for action in feedback.get("actions", [])
        if action.get("type") == "promote_area"
    }


def _apply_feedback_to_soundness_rules(
    feedback: dict | None,
    rules: list[dict],
    *,
    artifact_type: str,
) -> list[dict]:
    if not feedback:
        return rules

    adjusted = [dict(rule) for rule in rules]
    adjusted_by_id = {rule["id"]: rule for rule in adjusted}

    for action in feedback.get("actions", []):
        if action.get("type") != "relax_soundness_rule":
            continue
        if artifact_type not in action.get("targets", []):
            continue

        rule = adjusted_by_id.get(action.get("rule_id"))
        if rule is None:
            continue

        remove_must_contain = set(action.get("remove_must_contain", []))
        if remove_must_contain:
            rule["must_contain"] = [
                pattern for pattern in rule["must_contain"] if pattern not in remove_must_contain
            ]

        remove_must_not_contain = set(action.get("remove_must_not_contain", []))
        if remove_must_not_contain:
            rule["must_not_contain"] = [
                pattern
                for pattern in rule["must_not_contain"]
                if pattern not in remove_must_not_contain
            ]

    return adjusted


def _obligation_evidence_context(profile: dict, abstraction_plan: dict) -> dict[str, list]:
    return _collect_evidence_context(profile, abstraction_plan)


def build_abstraction_plan(
    module_path: str | Path | None = None,
    *,
    repo_root: str | Path | None = None,
    artifact_root: str | Path | None = None,
    profile_id: str | None = None,
    source_tree: str | Path | None = None,
) -> dict:
    context = resolve_module_context(
        module_path,
        repo_root=repo_root,
        profile_id=profile_id,
        source_tree=source_tree,
    )
    repo = context["repo_root"]
    profile = context["profile"]
    translation_profile = profile["translation"]
    abstraction_requirements = profile["abstraction_requirements"]
    mvp_modules, post_mvp_modules = _implemented_module_maps(profile)
    feedback = _load_oracle_feedback(repo, profile["module_id"], artifact_root=artifact_root)

    module = context["module_source_path"]
    source_text = _load_text(module)
    binding_audit = build_binding_gap_audit(
        module_path,
        repo_root=repo,
        artifact_root=artifact_root,
        profile_id=profile["profile_id"],
        source_tree=source_tree,
    )
    helper_audit = build_helper_audit(
        module_path,
        repo_root=repo,
        artifact_root=artifact_root,
        profile_id=profile["profile_id"],
        source_tree=source_tree,
    )
    command_semantics = build_command_semantics(
        module_path,
        repo_root=repo,
        artifact_root=artifact_root,
        profile_id=profile["profile_id"],
        source_tree=source_tree,
    )
    external_header_plan = build_external_header_plan(
        module_path,
        repo_root=repo,
        artifact_root=artifact_root,
        profile_id=profile["profile_id"],
        source_tree=source_tree,
    )
    kbuild_patch_plan = build_kbuild_patch_plan(
        module_path,
        repo_root=repo,
        artifact_root=artifact_root,
        profile_id=profile["profile_id"],
        source_tree=source_tree,
    )
    bindings_patch_plan = build_bindings_patch_plan(
        module_path,
        repo_root=repo,
        artifact_root=artifact_root,
        profile_id=profile["profile_id"],
        source_tree=source_tree,
    )
    helpers_patch_plan = build_helpers_patch_plan(
        module_path,
        repo_root=repo,
        artifact_root=artifact_root,
        profile_id=profile["profile_id"],
        source_tree=source_tree,
    )

    implemented_areas = _implemented_areas(repo, mvp_modules, post_mvp_modules)
    missing_mvp_areas = [area for area in abstraction_requirements if area not in implemented_areas]
    phase_4_prereqs = []
    if missing_mvp_areas:
        phase_4_prereqs.append(
            "Implement/export the remaining MVP abstractions: "
            + ", ".join(missing_mvp_areas)
            + "."
        )
    if kbuild_patch_plan["patch_required"]:
        if kbuild_patch_plan.get("kbuild_mode") == "add-new-driver":
            phase_4_prereqs.append("Add the landing Kbuild entries before driver codegen.")
        else:
            phase_4_prereqs.append("Apply the Kbuild switch so the Rust object can replace the C object.")
    if bindings_patch_plan["patch_required"]:
        phase_4_prereqs.append("Expose the remaining bindings headers before driver codegen.")
    if helper_audit["missing_helper_wrappers"]:
        phase_4_prereqs.append(
            "Add the remaining helper wrappers before driver codegen: "
            + ", ".join(helper_audit["missing_helper_wrappers"])
            + "."
        )
    if external_header_plan["blocking_headers"]:
        phase_4_prereqs.append(
            "Resolve external header/UAPI landing before driver codegen: "
            + ", ".join(external_header_plan["blocking_headers"])
            + "."
        )
    phase_4_prereqs.extend(_feedback_blockers(feedback, "abstraction-plan"))
    phase_4_prereqs = _dedupe_preserve_order(phase_4_prereqs)

    module_id = context["module_id"]
    source_inventory, field_maps = _build_source_inventory(profile, source_text, module_id)

    promoted_callbacks = _feedback_promoted_callbacks(feedback)
    required_specs = list(translation_profile["required_callback_fields"])
    deferred_specs = [
        spec
        for spec in translation_profile["deferred_callback_fields"]
        if (spec["source"], spec["field"]) not in promoted_callbacks
    ]
    for source, field in sorted(promoted_callbacks):
        spec = {"source": source, "field": field}
        if spec not in required_specs:
            required_specs.append(spec)

    required_callbacks = _callback_symbols_from_specs(field_maps, required_specs)
    deferred_callbacks = _callback_symbols_from_specs(field_maps, deferred_specs)

    promoted_areas = _feedback_promoted_areas(feedback)
    current_rust_modules = binding_audit.get("current_rust_modules", [])

    payload = {
        "schema_version": 1,
        "artifact_type": "abstraction-plan",
        "module_id": module_id,
        "module_c_path": context["module_c_path"],
        "inputs": {
            "kbuild_patch_plan": _artifact_path_for(
                module_id,
                "kbuild-patch-plan.json",
                repo_root=repo,
                artifact_root=artifact_root,
            ),
            "external_header_plan": _artifact_path_for(
                module_id,
                "external-header-plan.json",
                repo_root=repo,
                artifact_root=artifact_root,
            ),
            "bindings_patch_plan": _artifact_path_for(
                module_id,
                "bindings-patch-plan.json",
                repo_root=repo,
                artifact_root=artifact_root,
            ),
            "helpers_patch_plan": _artifact_path_for(
                module_id,
                "helpers-patch-plan.json",
                repo_root=repo,
                artifact_root=artifact_root,
            ),
            "command_semantics": _artifact_path_for(
                module_id,
                "command-semantics.json",
                repo_root=repo,
                artifact_root=artifact_root,
            ),
        },
        "goal": translation_profile["abstraction_goal"],
        "current_rust_scope": {
            "modules": current_rust_modules,
            "implemented_areas": sorted(implemented_areas),
            "assessment": (
                _net_scope_assessment(
                    profile,
                    binding_audit["current_rust_net_modules"],
                    implemented_areas,
                    mvp_modules,
                    module_id,
                )
                if profile["family_id"] == "net-link-type"
                else (
                    "The tree already exposes the currently implemented abstraction surfaces ({implemented}); the remaining blockers stay explicit in `abstraction_areas`."
                ).format(
                    implemented=", ".join(sorted(implemented_areas)) or "<none>",
                )
            ),
        },
        "current_rust_net_scope": {
            "rust_net_root": _rel(repo, repo / "rust" / "kernel" / "net.rs"),
            "modules": binding_audit["current_rust_net_modules"],
            "implemented_areas": sorted(implemented_areas),
            "assessment": (
                _net_scope_assessment(
                    profile,
                    binding_audit["current_rust_net_modules"],
                    implemented_areas,
                    mvp_modules,
                    module_id,
                )
                if profile["family_id"] == "net-link-type"
                else "Not a net-link-type target."
            ),
        },
        "source_inventory": source_inventory,
        "mvp_scope": {
            "target_command": translation_profile["mvp_scope"]["target_command"],
            "required_callbacks": required_callbacks,
            "deferred_callbacks": deferred_callbacks,
            "note": translation_profile["mvp_scope"]["note"],
        },
        "abstraction_areas": [],
        "phase_4_readiness": {
            "ready_for_minimal_driver_codegen": not phase_4_prereqs,
            "required_before_driver_codegen": phase_4_prereqs,
            "driver_generation_rules": translation_profile.get(
                "driver_generation_rules",
                [
                    "Driver code may borrow style from `drivers/net/phy/ax88796b_rust.rs` / `rust/kernel/net/phy.rs`, but only for module/abstraction layering.",
                    "Driver code must not call raw register/unregister or helper exports directly from the driver surface.",
                    "Any Phase 4 expansion beyond the `mvp_scope.deferred_callbacks` list requires updating this artifact first.",
                ],
            ),
            "helper_gaps": helper_audit["missing_helper_wrappers"],
            "external_header_blockers": external_header_plan["blocking_headers"],
        },
        **_artifact_metadata(profile),
    }

    all_rust_modules = {**mvp_modules, **post_mvp_modules}
    for area, metadata in abstraction_requirements.items():
        priority = metadata.get("priority", "mvp-blocker")
        if area in promoted_areas:
            priority = "mvp-blocker"
        payload["abstraction_areas"].append(
            {
                "area": area,
                "priority": priority,
                "status": "implemented" if area in implemented_areas else "missing",
                "implementation_path": _implemented_module_path(all_rust_modules.get(area)) if area in implemented_areas else None,
                "c_evidence": [item for item in metadata["patterns"] if item in source_text],
                "required_surface": metadata["required_surface"],
                "deferred_surface": metadata.get("deferred_surface", []),
                "depends_on": _depends_on_paths(
                    module_id,
                    metadata,
                    repo_root=repo,
                    artifact_root=artifact_root,
                ),
                "preferred_files": metadata["preferred_files"],
            }
        )

    notes = _feedback_notes(feedback, "abstraction-plan")
    if notes:
        payload["oracle_feedback_notes"] = notes
    return payload


def build_translation_plan(
    module_path: str | Path | None = None,
    *,
    repo_root: str | Path | None = None,
    artifact_root: str | Path | None = None,
    profile_id: str | None = None,
    source_tree: str | Path | None = None,
) -> dict:
    context = resolve_module_context(
        module_path,
        repo_root=repo_root,
        profile_id=profile_id,
        source_tree=source_tree,
    )
    repo = context["repo_root"]
    profile = context["profile"]
    translation_profile = profile["translation"]
    feedback = _load_oracle_feedback(repo, profile["module_id"], artifact_root=artifact_root)
    module_id = context["module_id"]

    abstraction_plan = build_abstraction_plan(
        module_path,
        repo_root=repo,
        artifact_root=artifact_root,
        profile_id=profile["profile_id"],
        source_tree=source_tree,
    )
    unsafe_plan = build_unsafe_obligations(
        module_path,
        repo_root=repo,
        artifact_root=artifact_root,
        profile_id=profile["profile_id"],
        source_tree=source_tree,
    )
    command_semantics = build_command_semantics(
        module_path,
        repo_root=repo,
        artifact_root=artifact_root,
        profile_id=profile["profile_id"],
        source_tree=source_tree,
    )
    external_header_plan = build_external_header_plan(
        module_path,
        repo_root=repo,
        artifact_root=artifact_root,
        profile_id=profile["profile_id"],
        source_tree=source_tree,
    )
    kbuild_patch_plan = build_kbuild_patch_plan(
        module_path,
        repo_root=repo,
        artifact_root=artifact_root,
        profile_id=profile["profile_id"],
        source_tree=source_tree,
    )
    bindings_patch_plan = build_bindings_patch_plan(
        module_path,
        repo_root=repo,
        artifact_root=artifact_root,
        profile_id=profile["profile_id"],
        source_tree=source_tree,
    )
    helpers_patch_plan = build_helpers_patch_plan(
        module_path,
        repo_root=repo,
        artifact_root=artifact_root,
        profile_id=profile["profile_id"],
        source_tree=source_tree,
    )
    inventory = abstraction_plan["source_inventory"]

    readiness_blockers = list(abstraction_plan["phase_4_readiness"]["required_before_driver_codegen"])
    if helpers_patch_plan["patch_required"]:
        readiness_blockers.append("Apply the helper wrapper patch before generating driver code.")
    readiness_blockers.extend(_feedback_blockers(feedback, "translation-plan"))
    readiness_blockers = _dedupe_preserve_order(readiness_blockers)

    setup_translations = []
    setup_translation_map = translation_profile.get("setup_translation_map", {})
    for write in inventory["setup_field_writes"]:
        mapped = setup_translation_map.get(write["field"])
        entry = {
            "c_field": write["field"],
            "c_operator": write["operator"],
            "c_value": write["value"],
        }
        if mapped:
            entry.update(mapped)
        else:
            entry.update(
                {
                    "status": "manual-review",
                    "rust_surface": None,
                    "note": "No translation rule is defined yet; update the profile before generating this field write.",
                }
            )
        setup_translations.append(entry)

    validate_translation = []
    for check in inventory["validate_checks"]:
        matched_rule = next(
            (
                rule
                for rule in translation_profile.get("validate_translation_rules", [])
                if rule["field"] == check["field"] and rule["return"] == check["return"]
            ),
            None,
        )
        if matched_rule:
            validate_translation.append(
                {
                    "status": matched_rule["status"],
                    "c_rule": f"tb[{check['field']}] => {check['return']}",
                    "rust_surface": matched_rule["rust_surface"],
                    "note": matched_rule["note"],
                }
            )
        else:
            validate_translation.append(
                {
                    "status": "manual-review",
                    "c_rule": f"tb[{check['field']}] => {check['return']}",
                    "rust_surface": None,
                    "note": "Unhandled validate rule; update the profile before code generation.",
                }
            )

    callback_fields = {
        table_name: table.get("field_map", {entry["field"]: entry["value"] for entry in table["fields"]})
        for table_name, table in inventory.get("callback_tables", {}).items()
    }
    promoted_callbacks = _feedback_promoted_callbacks(feedback)
    callback_mapping = []
    for callback_key, role in translation_profile["callback_roles"].items():
        source, field = callback_key.split(".", 1)
        c_symbol = callback_fields.get(source, {}).get(field)
        if not c_symbol:
            continue
        entry = {
            "status": role["status"],
            "c_symbol": c_symbol,
        }
        if "rust_surface" in role:
            entry["rust_surface"] = role["rust_surface"]
        if "behavior_contract" in role:
            entry["behavior_contract"] = role["behavior_contract"]
        if "body_plan" in role:
            entry["body_plan"] = role["body_plan"]
        if "c_evidence" in role:
            entry["c_evidence"] = role["c_evidence"]
        if (source, field) in promoted_callbacks:
            entry["status"] = "required"
        callback_mapping.append(entry)

    deferred_default = profile["translation"]["deferred_callback_default"]
    for spec in translation_profile["deferred_callback_fields"]:
        if (spec["source"], spec["field"]) in promoted_callbacks:
            continue
        if f"{spec['source']}.{spec['field']}" in translation_profile["callback_roles"]:
            continue
        c_symbol = callback_fields.get(spec["source"], {}).get(spec["field"])
        if not c_symbol:
            continue
        callback_mapping.append(
            {
                "status": deferred_default["status"],
                "c_symbol": c_symbol,
                **(
                    {"rust_surface": deferred_default["rust_surface"]}
                    if "rust_surface" in deferred_default
                    else {}
                ),
                **(
                    {"behavior_contract": deferred_default["behavior_contract"]}
                    if "behavior_contract" in deferred_default
                    else {}
                ),
                **({"body_plan": deferred_default["body_plan"]} if "body_plan" in deferred_default else {}),
            }
        )

    payload = {
        "schema_version": 1,
        "artifact_type": "translation-plan",
        "module_id": module_id,
        "module_c_path": context["module_c_path"],
        "driver_rust_path": context["driver_rust_path"],
        "inputs": {
            "kbuild_patch_plan": _artifact_path_for(
                module_id,
                "kbuild-patch-plan.json",
                repo_root=repo,
                artifact_root=artifact_root,
            ),
            "external_header_plan": _artifact_path_for(
                module_id,
                "external-header-plan.json",
                repo_root=repo,
                artifact_root=artifact_root,
            ),
            "bindings_patch_plan": _artifact_path_for(
                module_id,
                "bindings-patch-plan.json",
                repo_root=repo,
                artifact_root=artifact_root,
            ),
            "helpers_patch_plan": _artifact_path_for(
                module_id,
                "helpers-patch-plan.json",
                repo_root=repo,
                artifact_root=artifact_root,
            ),
            "abstraction_plan": _artifact_path_for(
                module_id,
                "abstraction-plan.json",
                repo_root=repo,
                artifact_root=artifact_root,
            ),
            "unsafe_obligations": _artifact_path_for(
                module_id,
                "unsafe-obligations.json",
                repo_root=repo,
                artifact_root=artifact_root,
            ),
            "command_semantics": _artifact_path_for(
                module_id,
                "command-semantics.json",
                repo_root=repo,
                artifact_root=artifact_root,
            ),
        },
        "goal": translation_profile["goal"],
        "readiness": {
            "ready_for_minimal_driver_codegen": not readiness_blockers,
            "blockers": readiness_blockers,
            "upstream_artifact_status": {
                "kbuild_patch_plan": kbuild_patch_plan["status"],
                "external_header_plan": external_header_plan["status"],
                "bindings_patch_plan": bindings_patch_plan["status"],
                "helpers_patch_plan": helpers_patch_plan["status"],
                "abstraction_plan": (
                    "ready" if abstraction_plan["phase_4_readiness"]["ready_for_minimal_driver_codegen"] else "blocked"
                ),
                "command_semantics": command_semantics["status"],
            },
        },
        "module_shell": translation_profile["module_shell"],
        "private_state": translation_profile["private_state"],
        "callback_mapping": callback_mapping,
        "setup_translation": setup_translations,
        "validate_translation": validate_translation,
        "deferred_callbacks": abstraction_plan["mvp_scope"]["deferred_callbacks"],
        "allowed_driver_surfaces": [
            *translation_profile["allowed_driver_surfaces"],
            *translation_profile.get("additional_allowed_driver_surfaces", []),
        ],
        "forbidden_driver_calls": [
            *translation_profile["forbidden_driver_calls"],
            *translation_profile.get("additional_forbidden_driver_calls", []),
        ],
        "unsafe_policy": {
            "driver_unsafe_expected": False,
            "note": "The first Rust loop should keep raw pointer work and bindgen coupling inside the abstractions named above; driver code must remain free of `unsafe` and direct `bindings::*` usage.",
            "linked_obligations": [entry["id"] for entry in unsafe_plan["obligations"]],
        },
        "driver_policy": translation_profile["driver_policy"],
        **_artifact_metadata(profile),
    }
    for key in [
        "callback_inference",
        "bit_strategy",
        "driver_layout",
        "error_handling_policy",
        "evidence_paths",
        "implementation_order",
        "driver_shape",
        "state_mapping",
        "abstraction_gap_handling",
        "runtime_expectation",
    ]:
        if key in translation_profile:
            payload[key] = translation_profile[key]
    notes = _feedback_notes(feedback, "translation-plan")
    if notes:
        payload["oracle_feedback_notes"] = notes
    return payload


def build_command_semantics(
    module_path: str | Path | None = None,
    *,
    repo_root: str | Path | None = None,
    artifact_root: str | Path | None = None,
    profile_id: str | None = None,
    source_tree: str | Path | None = None,
) -> dict:
    context = resolve_module_context(
        module_path,
        repo_root=repo_root,
        profile_id=profile_id,
        source_tree=source_tree,
    )
    repo = context["repo_root"]
    profile = context["profile"]
    module_id = context["module_id"]
    entries = _build_command_semantics_entries(profile)

    return {
        "schema_version": 1,
        "artifact_type": "command-semantics",
        "module_id": module_id,
        "module_c_path": context["module_c_path"],
        "driver_rust_path": context["driver_rust_path"],
        "status": "configured" if entries else "not-configured",
        "verification_mode": "helper-body-pattern",
        "inputs": {
            "translation_plan": _artifact_path_for(
                module_id,
                "translation-plan.json",
                repo_root=repo,
                artifact_root=artifact_root,
            ),
        },
        "summary": {
            "total_commands": len(entries),
            "completion_modes": {
                mode: sum(1 for entry in entries if entry["completion_mode"] == mode)
                for mode in ["status-gated", "readback-gated", "issue-only"]
            },
        },
        "commands": entries,
        **_artifact_metadata(profile),
    }


def build_unsafe_obligations(
    module_path: str | Path | None = None,
    *,
    repo_root: str | Path | None = None,
    artifact_root: str | Path | None = None,
    profile_id: str | None = None,
    source_tree: str | Path | None = None,
) -> dict:
    context = resolve_module_context(
        module_path,
        repo_root=repo_root,
        profile_id=profile_id,
        source_tree=source_tree,
    )
    repo = context["repo_root"]
    profile = context["profile"]
    feedback = _load_oracle_feedback(repo, profile["module_id"], artifact_root=artifact_root)
    abstraction_plan = build_abstraction_plan(
        module_path,
        repo_root=repo,
        artifact_root=artifact_root,
        profile_id=profile["profile_id"],
        source_tree=source_tree,
    )
    module_id = abstraction_plan["module_id"]
    evidence_context = _obligation_evidence_context(profile, abstraction_plan)

    obligations = []
    for obligation_id, template in profile["unsafe"]["unsafe_obligation_templates"].items():
        evidence = list(template.get("evidence_literals", []))
        context_key = template.get("evidence_context_key")
        if context_key:
            evidence.extend(evidence_context.get(context_key, []))
        obligations.append(
            {
                "id": obligation_id,
                "phase": template["phase"],
                "area": template["area"],
                "category": template["category"],
                "evidence": evidence,
                "obligation": template["obligation"],
                "preferred_discharge": template["preferred_discharge"],
                "allowed_unsafe_location": template["allowed_unsafe_location"],
            }
        )

    payload = {
        "schema_version": 1,
        "artifact_type": "unsafe-obligations",
        "module_id": module_id,
        "module_c_path": abstraction_plan["module_c_path"],
        "inputs": {
            "abstraction_plan": _artifact_path_for(
                module_id,
                "abstraction-plan.json",
                repo_root=repo,
                artifact_root=artifact_root,
            ),
            "command_semantics": _artifact_path_for(
                module_id,
                "command-semantics.json",
                repo_root=repo,
                artifact_root=artifact_root,
            ),
        },
        "obligations": obligations,
        "driver_side_rules": [
            rule.replace("{driver_rust_path}", context["driver_rust_path"])
            for rule in profile.get("unsafe", {}).get(
                "driver_side_rules",
                [
                    "Do not use `unsafe` in driver code, including `unsafe impl`, `unsafe fn`, or `unsafe {}` blocks.",
                    "Do not reference `bindings::*` directly from driver code; route constants and types through approved abstractions.",
                    "Do not cast `net_device` private storage inside `{driver_rust_path}`.",
                    "Do not add new raw FFI calls in driver code beyond the abstractions named in `abstraction-plan.json`.",
                    "Driver `start_xmit` may only use `skbuff::SkBuff`, `stats::dev_lstats_add`, and `netdevice::TxOutcome`; ownership must stay inside those safe surfaces.",
                    "Keep deferred callbacks disabled until the corresponding post-MVP obligations have an explicit discharge path.",
                ],
            )
        ],
        **_artifact_metadata(profile),
    }
    notes = _feedback_notes(feedback, "unsafe-obligations")
    if notes:
        payload["oracle_feedback_notes"] = notes
    return payload


def build_safety_policy(
    module_path: str | Path | None = None,
    *,
    repo_root: str | Path | None = None,
    artifact_root: str | Path | None = None,
    profile_id: str | None = None,
    source_tree: str | Path | None = None,
) -> dict:
    context = resolve_module_context(
        module_path,
        repo_root=repo_root,
        profile_id=profile_id,
        source_tree=source_tree,
    )
    repo = context["repo_root"]
    profile = context["profile"]
    feedback = _load_oracle_feedback(repo, profile["module_id"], artifact_root=artifact_root)
    module_id = context["module_id"]
    abstraction_plan = build_abstraction_plan(
        module_path,
        repo_root=repo,
        artifact_root=artifact_root,
        profile_id=profile["profile_id"],
        source_tree=source_tree,
    )
    translation_plan = build_translation_plan(
        module_path,
        repo_root=repo,
        artifact_root=artifact_root,
        profile_id=profile["profile_id"],
        source_tree=source_tree,
    )

    allowlist_prefixes = profile.get("safety", {}).get("abstraction_allowlist_prefixes", ["rust/kernel/net/"])
    implemented_areas = set(abstraction_plan["current_rust_scope"]["implemented_areas"])
    allowlisted_files = [
        entry["implementation_path"]
        for entry in abstraction_plan["abstraction_areas"]
        if entry["status"] == "implemented" and entry["implementation_path"]
    ]
    allowlisted_files = [
        path
        for path in allowlisted_files
        if any(path.startswith(prefix) for prefix in allowlist_prefixes)
    ]

    required_soundness_rules = [
        {"id": rule_id, **rule}
        for rule_id, rule in profile["safety"]["soundness_rule_templates"].items()
        if not rule.get("requires_area") or rule["requires_area"] in implemented_areas
    ]
    required_soundness_rules = _apply_feedback_to_soundness_rules(
        feedback,
        required_soundness_rules,
        artifact_type="safety-policy",
    )

    payload = {
        "schema_version": 1,
        "artifact_type": "safety-policy",
        "module_id": module_id,
        "module_c_path": context["module_c_path"],
        "driver_rust_path": translation_plan["driver_rust_path"],
        "inputs": {
            "abstraction_plan": _artifact_path_for(
                module_id,
                "abstraction-plan.json",
                repo_root=repo,
                artifact_root=artifact_root,
            ),
            "external_header_plan": _artifact_path_for(
                module_id,
                "external-header-plan.json",
                repo_root=repo,
                artifact_root=artifact_root,
            ),
            "translation_plan": _artifact_path_for(
                module_id,
                "translation-plan.json",
                repo_root=repo,
                artifact_root=artifact_root,
            ),
            "unsafe_obligations": _artifact_path_for(
                module_id,
                "unsafe-obligations.json",
                repo_root=repo,
                artifact_root=artifact_root,
            ),
            "soundness_discharge": _artifact_path_for(
                module_id,
                "soundness-discharge.json",
                repo_root=repo,
                artifact_root=artifact_root,
            ),
        },
        "driver_policy": {
            "zero_unsafe": profile["safety"]["driver_policy"]["zero_unsafe"],
            "zero_bindings": profile["safety"]["driver_policy"]["zero_bindings"],
            "zero_extern_c": profile["safety"]["driver_policy"]["zero_extern_c"],
            "required_crate_attributes": profile["safety"]["driver_policy"]["required_crate_attributes"],
            "forbidden_tokens": profile["safety"]["driver_policy"]["forbidden_tokens"],
            "forbidden_calls": translation_plan["forbidden_driver_calls"],
            "allowed_surfaces": translation_plan["allowed_driver_surfaces"],
        },
        "abstraction_policy": {
            "allowlisted_files": allowlisted_files,
            "unsafe_trait_impls_are_proof_sites": True,
            "required_soundness_rules": required_soundness_rules,
        },
        **_artifact_metadata(profile),
    }
    notes = _feedback_notes(feedback, "safety-policy")
    if notes:
        payload["oracle_feedback_notes"] = notes
    return payload


def build_soundness_discharge(
    module_path: str | Path | None = None,
    *,
    repo_root: str | Path | None = None,
    artifact_root: str | Path | None = None,
    profile_id: str | None = None,
    source_tree: str | Path | None = None,
) -> dict:
    context = resolve_module_context(
        module_path,
        repo_root=repo_root,
        profile_id=profile_id,
        source_tree=source_tree,
    )
    repo = context["repo_root"]
    profile = context["profile"]
    module_id = context["module_id"]
    safety_policy = build_safety_policy(
        module_path,
        repo_root=repo,
        artifact_root=artifact_root,
        profile_id=profile["profile_id"],
        source_tree=source_tree,
    )
    command_semantics = build_command_semantics(
        module_path,
        repo_root=repo,
        artifact_root=artifact_root,
        profile_id=profile["profile_id"],
        source_tree=source_tree,
    )
    command_semantics = build_command_semantics(
        module_path,
        repo_root=repo,
        artifact_root=artifact_root,
        profile_id=profile["profile_id"],
        source_tree=source_tree,
    )
    unsafe_plan = build_unsafe_obligations(
        module_path,
        repo_root=repo,
        artifact_root=artifact_root,
        profile_id=profile["profile_id"],
        source_tree=source_tree,
    )
    allowlisted_files = safety_policy["abstraction_policy"]["allowlisted_files"]

    obligation_map = {entry["id"]: entry for entry in unsafe_plan["obligations"]}
    proof_sites = []
    for index, site in enumerate(_find_rust_unsafe_sites(repo, allowlisted_files), start=1):
        linked_obligation_ids = _match_unsafe_obligation_ids(profile, site)
        preferred_discharge = [
            obligation_map[obligation_id]["preferred_discharge"]
            for obligation_id in linked_obligation_ids
            if obligation_id in obligation_map
        ]
        proof_sites.append(
            {
                "id": f"proof-site-{index}",
                "file": site["file"],
                "line": site["line"],
                "unsafe_kind": site["unsafe_kind"],
                "source_excerpt": site["source_excerpt"],
                "enclosing_item": site["enclosing_item"],
                "enclosing_impl_type": site["enclosing_impl_type"],
                "safety_comment_window": site["safety_comment_window"],
                "source_window": site["source_window"],
                "linked_obligation_ids": linked_obligation_ids,
                "safe_api_surface": Path(site["file"]).stem,
                "caller_obligations": [
                    "Keep the raw pointer/reference conversion inside the abstraction boundary.",
                ],
                "kernel_preconditions": [
                    "The kernel callback or registration path upholds the pointed-to object lifetime.",
                ],
                "proof_argument": " ".join(preferred_discharge) if preferred_discharge else "No matching unsafe obligation was found.",
                "evidence_refs": [
                    _artifact_path_for(
                        module_id,
                        "unsafe-obligations.json",
                        repo_root=repo,
                        artifact_root=artifact_root,
                    )
                ],
                "status": "discharged" if linked_obligation_ids else "blocked",
            }
        )

    driver_path = _path_from_repo(repo, safety_policy["driver_rust_path"])
    driver_text = _load_text(driver_path) if driver_path.exists() else ""
    stripped_driver = _strip_rust_noncode(driver_text)

    structural_rules = []
    for rule in safety_policy["abstraction_policy"]["required_soundness_rules"]:
        path = _path_from_repo(repo, rule["file"])
        text = _load_text(path) if path.exists() else ""
        normalized_text = _normalize_whitespace(text)
        compact_text = _compact_whitespace(text)
        missing = [
            pattern
            for pattern in rule["must_contain"]
            if pattern not in text
            and _normalize_whitespace(pattern) not in normalized_text
            and _compact_whitespace(pattern) not in compact_text
        ]
        unexpected = [
            pattern
            for pattern in rule["must_not_contain"]
            if pattern in text
            or _normalize_whitespace(pattern) in normalized_text
            or _compact_whitespace(pattern) in compact_text
        ]
        structural_rules.append(
            {
                "id": rule["id"],
                "file": rule["file"],
                "linked_obligation_ids": rule["linked_obligation_ids"],
                "must_contain": rule["must_contain"],
                "must_not_contain": rule["must_not_contain"],
                "status": "discharged" if not missing and not unexpected else "blocked",
                "missing_patterns": missing,
                "unexpected_patterns": unexpected,
            }
        )

    for crate_attr in safety_policy["driver_policy"]["required_crate_attributes"]:
        structural_rules.append(
            {
                "id": "driver-forbid-unsafe-code-attr",
                "file": safety_policy["driver_rust_path"],
                "linked_obligation_ids": [],
                "must_contain": [crate_attr],
                "must_not_contain": [],
                "status": "discharged" if crate_attr in driver_text else "blocked",
                "missing_patterns": [] if crate_attr in driver_text else [crate_attr],
                "unexpected_patterns": [],
            }
        )
    driver_rules = [
        {
            "id": "driver-zero-unsafe",
            "status": "discharged" if not re.search(r"\bunsafe\b", stripped_driver) else "blocked",
            "must_not_contain": ["unsafe"],
        },
        {
            "id": "driver-zero-bindings",
            "status": "discharged" if "bindings::" not in stripped_driver else "blocked",
            "must_not_contain": ["bindings::"],
        },
        {
            "id": "driver-zero-ffi",
            "status": "discharged" if 'extern "C"' not in stripped_driver else "blocked",
            "must_not_contain": ['extern "C"'],
        },
    ]

    return {
        "schema_version": 1,
        "artifact_type": "soundness-discharge",
        "module_id": module_id,
        "module_c_path": context["module_c_path"],
        "driver_rust_path": safety_policy["driver_rust_path"],
        "inputs": {
            "safety_policy": _artifact_path_for(
                module_id,
                "safety-policy.json",
                repo_root=repo,
                artifact_root=artifact_root,
            ),
            "unsafe_obligations": _artifact_path_for(
                module_id,
                "unsafe-obligations.json",
                repo_root=repo,
                artifact_root=artifact_root,
            ),
            "command_semantics": _artifact_path_for(
                module_id,
                "command-semantics.json",
                repo_root=repo,
                artifact_root=artifact_root,
            ),
        },
        "proof_sites": proof_sites,
        "structural_rules": structural_rules,
        "driver_rules": driver_rules,
    }


def build_agent_workflow_plan(
    module_path: str | Path | None = None,
    *,
    repo_root: str | Path | None = None,
    artifact_root: str | Path | None = None,
    profile_id: str | None = None,
    source_tree: str | Path | None = None,
) -> dict:
    context = resolve_module_context(
        module_path,
        repo_root=repo_root,
        profile_id=profile_id,
        source_tree=source_tree,
    )
    repo = context["repo_root"]
    profile = context["profile"]
    module_id = context["module_id"]

    abstraction_plan = build_abstraction_plan(
        module_path,
        repo_root=repo,
        artifact_root=artifact_root,
        profile_id=profile["profile_id"],
        source_tree=source_tree,
    )
    translation_plan = build_translation_plan(
        module_path,
        repo_root=repo,
        artifact_root=artifact_root,
        profile_id=profile["profile_id"],
        source_tree=source_tree,
    )
    safety_policy = build_safety_policy(
        module_path,
        repo_root=repo,
        artifact_root=artifact_root,
        profile_id=profile["profile_id"],
        source_tree=source_tree,
    )
    command_semantics = build_command_semantics(
        module_path,
        repo_root=repo,
        artifact_root=artifact_root,
        profile_id=profile["profile_id"],
        source_tree=source_tree,
    )
    module_lifecycle_config = module_lifecycle_config_requirements(
        module_path,
        repo_root=repo,
        artifact_root=artifact_root,
        profile_id=profile["profile_id"],
        source_tree=source_tree,
    )
    kbuild_plan = build_kbuild_plan(
        module_path,
        repo_root=repo,
        artifact_root=artifact_root,
        profile_id=profile["profile_id"],
        source_tree=source_tree,
    )

    driver_rust_path = translation_plan["driver_rust_path"]
    managed_abstraction_files = safety_policy["abstraction_policy"]["allowlisted_files"]
    managed_files = [driver_rust_path, *managed_abstraction_files]
    driver_object_path, driver_module_path = _driver_build_targets(context, kbuild_plan)

    readiness_blockers = []
    if not translation_plan["readiness"]["ready_for_minimal_driver_codegen"]:
        readiness_blockers.extend(translation_plan["readiness"]["blockers"])
    readiness_blockers = _dedupe_preserve_order(readiness_blockers)
    gate_selector = f"--profile-id {profile['profile_id']}"
    if source_tree is not None:
        gate_selector += f" --source-tree {source_tree}"

    return {
        "schema_version": 1,
        "artifact_type": "agent-workflow-plan",
        "module_id": module_id,
        "module_c_path": context["module_c_path"],
        "driver_rust_path": driver_rust_path,
        "driver_object_path": driver_object_path,
        "driver_module_path": driver_module_path,
        "inputs": {
            "abstraction_plan": _artifact_path_for(
                module_id,
                "abstraction-plan.json",
                repo_root=repo,
                artifact_root=artifact_root,
            ),
            "translation_plan": _artifact_path_for(
                module_id,
                "translation-plan.json",
                repo_root=repo,
                artifact_root=artifact_root,
            ),
            "safety_policy": _artifact_path_for(
                module_id,
                "safety-policy.json",
                repo_root=repo,
                artifact_root=artifact_root,
            ),
            "soundness_discharge": _artifact_path_for(
                module_id,
                "soundness-discharge.json",
                repo_root=repo,
                artifact_root=artifact_root,
            ),
            "unsafe_obligations": _artifact_path_for(
                module_id,
                "unsafe-obligations.json",
                repo_root=repo,
                artifact_root=artifact_root,
            ),
            **(
                {"module_lifecycle_config": module_lifecycle_config["artifact_path"]}
                if module_lifecycle_config["enabled"]
                else {}
            ),
        },
        "preflight": {
            "ready_for_agent_codegen": not readiness_blockers,
            "blockers": readiness_blockers,
            "required_reads": [
                _artifact_path_for(module_id, "abstraction-plan.json", repo_root=repo, artifact_root=artifact_root),
                _artifact_path_for(
                    module_id,
                    "external-header-plan.json",
                    repo_root=repo,
                    artifact_root=artifact_root,
                ),
                _artifact_path_for(module_id, "translation-plan.json", repo_root=repo, artifact_root=artifact_root),
                _artifact_path_for(module_id, "safety-policy.json", repo_root=repo, artifact_root=artifact_root),
                _artifact_path_for(
                    module_id,
                    "soundness-discharge.json",
                    repo_root=repo,
                    artifact_root=artifact_root,
                ),
                _artifact_path_for(
                    module_id,
                    "unsafe-obligations.json",
                    repo_root=repo,
                    artifact_root=artifact_root,
                ),
                _artifact_path_for(
                    module_id,
                    "command-semantics.json",
                    repo_root=repo,
                    artifact_root=artifact_root,
                ),
                *([module_lifecycle_config["artifact_path"]] if module_lifecycle_config["enabled"] else []),
            ],
        },
        "generation_scope": {
            "managed_files": managed_files,
            "driver_files": [driver_rust_path],
            "abstraction_files": managed_abstraction_files,
            "deferred_callbacks": translation_plan["deferred_callbacks"],
            "forbidden_driver_calls": translation_plan["forbidden_driver_calls"],
            "allowed_driver_surfaces": translation_plan["allowed_driver_surfaces"],
            "required_driver_crate_attributes": safety_policy["driver_policy"]["required_crate_attributes"],
        },
        "agent_contract": {
            "goal": translation_plan["goal"],
            "must_read_before_edit": [
                _artifact_path_for(module_id, "translation-plan.json", repo_root=repo, artifact_root=artifact_root),
                _artifact_path_for(module_id, "safety-policy.json", repo_root=repo, artifact_root=artifact_root),
                _artifact_path_for(
                    module_id,
                    "soundness-discharge.json",
                    repo_root=repo,
                    artifact_root=artifact_root,
                ),
                _artifact_path_for(
                    module_id,
                    "command-semantics.json",
                    repo_root=repo,
                    artifact_root=artifact_root,
                ),
            ],
            "must_not_do": [
                "Do not introduce `unsafe` into driver code.",
                "Do not call `bindings::*` directly from driver code.",
                "Do not implement callbacks listed under `deferred_callbacks` without updating the artifacts first.",
                "Do not edit files outside `generation_scope.managed_files` unless a newer artifact explicitly expands the scope.",
            ],
            "soundness_claim_scope": {
                "mvp_callbacks": abstraction_plan["mvp_scope"]["required_callbacks"],
                "deferred_callbacks": translation_plan["deferred_callbacks"],
                "abstraction_allowlist": managed_abstraction_files,
            },
        },
        "acceptance_gates": [
            {
                "id": "verify-safety",
                "stage": "pre-acceptance",
                "mandatory": True,
                "command": (
                    "python3 scripts/c2saferust/tool_cli.py verify-safety "
                    "--kernel-tree <linux-tree> "
                    f"{gate_selector} "
                    f"--output {_artifact_path_for(module_id, 'safety-verdict.json', repo_root=repo, artifact_root=artifact_root)}"
                ),
                "pass_condition": "`safety-verdict.json.pass == true`",
            },
            *(
                [
                    {
                        "id": "verify-command-semantics",
                        "stage": "pre-acceptance",
                        "mandatory": True,
                        "command": (
                            "python3 scripts/c2saferust/tool_cli.py verify-command-semantics "
                            "--kernel-tree <linux-tree> "
                            f"{gate_selector} "
                            f"--output {_artifact_path_for(module_id, 'command-semantics-verdict.json', repo_root=repo, artifact_root=artifact_root)}"
                        ),
                        "pass_condition": "`command-semantics-verdict.json.pass == true`",
                    }
                ]
                if command_semantics["commands"]
                else []
            ),
            {
                "id": "rust-toolchain-available",
                "stage": "pre-smoke",
                "mandatory": True,
                "command": (
                    "make O=/tmp/c2saferust-"
                    f"{module_id}"
                    "-build <LLVM/ENV> rustavailable"
                ),
                "pass_condition": "`make rustavailable` exits 0 with the same LLVM/libclang environment used for module packaging.",
            },
            {
                "id": "compile-driver-object",
                "stage": "pre-smoke",
                "mandatory": True,
                "command": f"make O=/tmp/c2saferust-{module_id}-build <LLVM/ENV> {driver_object_path}",
                "pass_condition": f"`make` exits 0 for `{driver_object_path}` after safety gate passes.",
            },
            {
                "id": "package-module",
                "stage": "pre-smoke",
                "mandatory": True,
                "command": f"make O=/tmp/c2saferust-{module_id}-build <LLVM/ENV> {driver_module_path}",
                "pass_condition": f"`make` exits 0 for `{driver_module_path}` after the lifecycle fragment is merged into the build dir.",
            },
        ],
        **({"agent_workflow": profile["agent_workflow"]} if profile.get("agent_workflow") else {}),
    }
