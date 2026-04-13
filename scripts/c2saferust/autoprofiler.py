#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

from __future__ import annotations

from pathlib import Path

import c_ast
import profiles


VTABLE_SUFFIXES = ("_ops", "_driver", "_protocol", "_config")


def _is_ascii_identifier(value: str) -> bool:
    if not value:
        return False
    if not (value[0].isalpha() or value[0] == "_"):
        return False
    return all(char.isalnum() or char == "_" for char in value[1:])


def _is_uppercase_constant(value: str) -> bool:
    return bool(value) and all(char.isupper() or char.isdigit() or char == "_" for char in value)


def _source_root(repo_root: Path, source_tree: str | Path | None) -> Path:
    return Path(source_tree).resolve() if source_tree is not None else repo_root


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


def _artifact_dir(repo_root: Path, output_dir: str | Path | None, module_id: str) -> str:
    if output_dir is None:
        return f"Documentation/rust/c2saferust/{module_id}"
    output_path = Path(output_dir).resolve()
    try:
        return str(output_path.relative_to(repo_root.resolve()))
    except ValueError:
        return str(output_path)


def _pascal_case(value: str) -> str:
    pieces: list[str] = []
    current: list[str] = []
    for char in value:
        if char.isalnum():
            current.append(char)
            continue
        if current:
            pieces.append("".join(current))
            current = []
    if current:
        pieces.append("".join(current))
    return "".join(piece[:1].upper() + piece[1:] for piece in pieces) or "AutoModule"


def _identifier_target(value: str) -> str | None:
    stripped = value.strip()
    for prefix in ("(void *)", "(const void *)"):
        if stripped.startswith(prefix):
            stripped = stripped[len(prefix) :].strip()
    if stripped.startswith("&"):
        stripped = stripped[1:].strip()
    if _is_ascii_identifier(stripped):
        return stripped
    return None


def _is_callback_target(value: str, static_names: set[str]) -> bool:
    stripped = value.strip()
    if not stripped:
        return False
    if stripped.startswith('"'):
        return False
    if stripped in {"NULL", "THIS_MODULE"}:
        return False
    if _is_uppercase_constant(stripped):
        return False
    target = _identifier_target(stripped)
    if target is None:
        return False
    if target in static_names:
        return False
    if _is_uppercase_constant(target):
        return False
    return True


def _linked_initializer_target(value: str, static_names: set[str]) -> str | None:
    target = _identifier_target(value)
    if target is None or target not in static_names:
        return None
    return target


def _is_init_macro(name: str) -> bool:
    return (
        name in {"module_init", "module_exit"}
        or name.endswith("initcall")
        or name.startswith("initcall_")
        or "_initcall_" in name
    )


def _is_shortcut_driver_macro(name: str) -> bool:
    if not (name.startswith("module_") or name.startswith("builtin_")):
        return False
    return name.endswith("_driver") or name.endswith("_driver_probe")


def _build_initializer_graph(initializers: list[dict]) -> tuple[dict[str, list[str]], dict[str, dict]]:
    name_to_initializer = {entry["name"]: entry for entry in initializers}
    static_names = set(name_to_initializer)
    graph: dict[str, list[str]] = {}

    for entry in initializers:
        linked: list[str] = []
        for field in entry.get("fields", []):
            target = _linked_initializer_target(field.get("value", ""), static_names)
            if target is None or target in linked:
                continue
            linked.append(target)
        graph[entry["name"]] = linked

    return graph, name_to_initializer


def _entry_seed_tables(
    tu: c_ast.CTranslationUnit,
    entry_functions: list[str],
    shortcut_driver_macros: list[dict],
    static_names: set[str],
) -> set[str]:
    seeds: set[str] = set()

    for macro in shortcut_driver_macros:
        for argument in macro.get("arguments", []):
            target = _identifier_target(argument)
            if target is not None and target in static_names:
                seeds.add(target)

    for function_name in entry_functions:
        for call in tu.list_call_sites(function_name):
            for argument in call.get("arguments", []):
                target = _identifier_target(argument)
                if target is not None and target in static_names:
                    seeds.add(target)

    return seeds


def _candidate_tables(initializers: list[dict], registry: dict) -> list[dict]:
    signature_to_families = registry["signature_to_families"]
    candidates: list[dict] = []
    for entry in initializers:
        type_name = entry.get("type_name")
        name = entry.get("name")
        signature_families = signature_to_families.get(type_name, []) if isinstance(type_name, str) else []
        suffix_match = any(
            isinstance(value, str) and value.endswith(VTABLE_SUFFIXES)
            for value in (type_name, name)
        )
        if not signature_families and not suffix_match:
            continue
        candidates.append(
            {
                "name": name,
                "type_name": type_name,
                "fields": entry.get("fields", []),
                "field_map": entry.get("field_map", {}),
                "signature_families": list(signature_families),
                "suffix_match": suffix_match,
            }
        )
    return candidates


def _family_scores(
    candidates: list[dict],
    entry_connected_names: set[str],
    registry: dict,
) -> dict[str, dict]:
    scores: dict[str, dict] = {}
    for family_id, family in registry["families_by_id"].items():
        signatures = [entry for entry in family.get("signature_structs", []) if isinstance(entry, str)]
        matched_primary = False
        matched_secondary: list[str] = []
        entry_bonus_signatures: list[str] = []

        for candidate in candidates:
            type_name = candidate.get("type_name")
            if type_name not in signatures:
                continue
            if signatures and type_name == signatures[0]:
                matched_primary = True
            elif type_name not in matched_secondary:
                matched_secondary.append(type_name)
            if candidate["name"] in entry_connected_names and type_name not in entry_bonus_signatures:
                entry_bonus_signatures.append(type_name)

        score = 0
        if matched_primary:
            score += 5
        score += 2 * len(matched_secondary)
        score += 2 * len(entry_bonus_signatures)
        scores[family_id] = {
            "score": score,
            "matched_primary": signatures[0] if matched_primary and signatures else None,
            "matched_secondary": matched_secondary,
            "entry_bonus_signatures": entry_bonus_signatures,
        }
    return scores


def _select_family(candidates: list[dict], entry_connected_names: set[str], registry: dict) -> tuple[str, str, dict]:
    scores = _family_scores(candidates, entry_connected_names, registry)
    ranked = sorted(
        ((family_id, metadata["score"]) for family_id, metadata in scores.items()),
        key=lambda item: (-item[1], item[0]),
    )
    if not ranked:
        return "generic-unknown", "generic-unknown", scores

    best_family, best_score = ranked[0]
    tied = [family_id for family_id, score in ranked if score == best_score]
    if best_score < 5 or len(tied) > 1:
        return "generic-unknown", "generic-unknown", scores
    return best_family, "known-family", scores


def _selected_tables(
    candidates: list[dict],
    selected_family_id: str,
    selection_mode: str,
    entry_connected_names: set[str],
    registry: dict,
) -> list[dict]:
    if selection_mode == "known-family":
        family = registry["families_by_id"][selected_family_id]
        signatures = set(family.get("signature_structs", []))
        return [candidate for candidate in candidates if candidate.get("type_name") in signatures]

    entry_connected = [candidate for candidate in candidates if candidate["name"] in entry_connected_names]
    return entry_connected or candidates


def _required_callback_fields(
    tables: list[dict],
    *,
    selection_mode: str,
) -> tuple[list[dict], dict[str, list[dict]], dict[str, list[str]]]:
    required: list[dict] = []
    linked_table_refs: dict[str, list[dict]] = {}
    callback_targets: dict[str, list[str]] = {}
    static_names = {table["name"] for table in tables}

    for table in tables:
        source_name = table["type_name"] if selection_mode == "known-family" else table["name"]
        if not source_name:
            continue
        linked_table_refs[table["name"]] = []
        callback_targets[table["name"]] = []
        for field in table.get("fields", []):
            value = field.get("value", "")
            target = _linked_initializer_target(value, static_names)
            if target is not None:
                linked_table_refs[table["name"]].append(
                    {
                        "field": field["field"],
                        "target": target,
                    }
                )
                continue
            if not _is_callback_target(value, static_names):
                continue
            callback_targets[table["name"]].append(field["field"])
            spec = {"source": source_name, "field": field["field"]}
            if spec not in required:
                required.append(spec)

    return required, linked_table_refs, callback_targets


def discover_auto_profile(
    module_path: str | Path,
    *,
    repo_root: str | Path,
    source_tree: str | Path | None = None,
    output_dir: str | Path | None = None,
) -> dict:
    repo = Path(repo_root).resolve()
    source_root = _source_root(repo, source_tree)
    lookup_key = _path_lookup_key(module_path, roots=[source_root, repo])
    source_path = source_root / lookup_key
    source_text = source_path.read_text(errors="ignore")
    tu = c_ast.CTranslationUnit(source_text)
    registry = profiles.load_family_registry()

    macro_invocations = tu.list_macro_invocations()
    entry_macros = [entry for entry in macro_invocations if _is_init_macro(entry["name"])]
    shortcut_driver_macros = [
        entry for entry in macro_invocations if _is_shortcut_driver_macro(entry["name"])
    ]
    entry_functions = [
        entry["arguments"][0]
        for entry in entry_macros
        if entry.get("arguments") and _is_ascii_identifier(entry["arguments"][0])
    ]

    initializers = tu.list_static_struct_initializers()
    graph, name_to_initializer = _build_initializer_graph(initializers)
    static_names = set(name_to_initializer)
    seed_tables = _entry_seed_tables(tu, entry_functions, shortcut_driver_macros, static_names)
    entry_connected_names = set(seed_tables)
    for seed in list(seed_tables):
        entry_connected_names.update(graph.get(seed, []))

    candidates = _candidate_tables(initializers, registry)
    for candidate in candidates:
        candidate["entry_connected"] = candidate["name"] in entry_connected_names
        candidate["linked_tables"] = list(graph.get(candidate["name"], []))

    selected_family_id, selection_mode, family_scores = _select_family(candidates, entry_connected_names, registry)
    tables = _selected_tables(candidates, selected_family_id, selection_mode, entry_connected_names, registry)
    required_callback_fields, linked_table_refs, callback_targets = _required_callback_fields(
        tables,
        selection_mode=selection_mode,
    )

    for candidate in candidates:
        candidate["linked_table_refs"] = linked_table_refs.get(candidate["name"], [])
        candidate["callback_targets"] = callback_targets.get(candidate["name"], [])

    module_name = Path(lookup_key).stem
    driver_type = _pascal_case(module_name) + "Driver"
    module_type = _pascal_case(module_name) + "Module"
    scenario_inputs = {
        "module_name": module_name,
        "module_file_relpath_template": str(Path(lookup_key).with_suffix(".ko")),
        "module_file_name_template": f"{module_name}.ko",
    }
    generated_profile = {
        "schema_version": 1,
        "profile_type": "c2saferust-module-profile",
        "profile_id": f"auto-{module_name}",
        "family_id": selected_family_id,
        "module_path": lookup_key,
        "module_id": module_name,
        "artifact_dir": _artifact_dir(repo, output_dir, module_name),
        "driver_rust_path": str(Path(lookup_key).with_name(f"{module_name}_rust.rs")),
        "translation": {
            "goal": (
                "Auto-discover the primary callback tables and emit a conservative planning bundle "
                f"for `{lookup_key}` without relying on a handwritten module profile."
            ),
            "abstraction_goal": (
                "Capture the callback topology, entrypoint evidence, and immediate manual-review "
                "constraints before any driver-specific Rust translation policy is claimed."
            ),
            "mvp_scope": {
                "target_command": f"modprobe {module_name} && rmmod {module_name}",
                "note": "AutoProfiler output is intentionally conservative and may require a dedicated family profile before code generation.",
            },
            "required_callback_fields": required_callback_fields,
            "deferred_callback_fields": [],
            "callback_roles": {},
            "setup_translation_map": {},
            "validate_translation_rules": [],
            "module_shell": {
                "module_macro_required": True,
                "preferred_module_type": module_type,
                "preferred_driver_type": driver_type,
                "accepted_registration": "manual-review",
                "module_name": module_name,
            },
            "private_state": {
                "type_name": None,
                "layout": None,
                "fields": [],
            },
        },
        "analysis": {
            "selected_callback_tables": [
                {
                    "name": table["name"],
                    "type_name": table.get("type_name"),
                }
                for table in tables
            ],
            "entrypoints": {
                "entry_macros": entry_macros,
                "entry_functions": entry_functions,
                "shortcut_driver_macros": shortcut_driver_macros,
            },
        },
        "oracle": {
            "scenario_id": "module_load_unload",
            "scenario_inputs": scenario_inputs,
        },
    }

    report = {
        "selected_family_id": selected_family_id,
        "selection_mode": selection_mode,
        "entry_macros": entry_macros,
        "entry_functions": entry_functions,
        "shortcut_driver_macros": shortcut_driver_macros,
        "candidate_tables": candidates,
        "family_scores": family_scores,
        "linked_table_refs": linked_table_refs,
        "required_callback_fields": required_callback_fields,
        "profile_sources": registry["family_sources"].get(selected_family_id, []),
    }

    return {
        "generated_profile": generated_profile,
        "report": report,
        "selected_family_id": selected_family_id,
        "selection_mode": selection_mode,
    }
