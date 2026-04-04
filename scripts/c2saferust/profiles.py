#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PROFILE_ROOT = Path(__file__).with_name("profiles")


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def _deep_merge(base: object, overlay: object) -> object:
    if isinstance(base, dict) and isinstance(overlay, dict):
        merged = dict(base)
        for key, value in overlay.items():
            if key in merged:
                merged[key] = _deep_merge(merged[key], value)
            else:
                merged[key] = value
        return merged
    return overlay


def _profile_path(collection: str, key_name: str, key_value: str) -> Path:
    candidates = sorted((PROFILE_ROOT / collection).glob("*.json"))
    for candidate in candidates:
        payload = _load_json(candidate)
        if payload.get(key_name) == key_value:
            return candidate
    raise FileNotFoundError(f"Could not resolve {collection} profile by {key_name}={key_value!r}")


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT.resolve()))
    except ValueError:
        return str(path.resolve())


def _merge_id_list(*collections: object) -> list[str]:
    merged: list[str] = []
    for collection in collections:
        if not isinstance(collection, list):
            continue
        for entry in collection:
            if isinstance(entry, str) and entry not in merged:
                merged.append(entry)
    return merged


def _load_rule_pack(rule_pack_id: str) -> tuple[dict, str]:
    rule_pack_path = _profile_path("rule_packs", "rule_pack_id", rule_pack_id)
    return _load_json(rule_pack_path), _rel(rule_pack_path)


def _apply_rule_packs(base: dict, rule_pack_ids: list[str]) -> tuple[dict, list[str]]:
    merged = dict(base)
    sources: list[str] = []
    for rule_pack_id in rule_pack_ids:
        payload, source = _load_rule_pack(rule_pack_id)
        existing_analysis = merged.get("analysis", {})
        existing_matchers = list(existing_analysis.get("unsafe_site_matchers", []))
        merged = _deep_merge(merged, payload)
        merged_analysis = merged.setdefault("analysis", {})
        payload_analysis = payload.get("analysis", {})
        if "unsafe_site_matchers" in payload_analysis:
            merged_matchers = existing_matchers
            existing_ids = {
                entry.get("id")
                for entry in merged_matchers
                if isinstance(entry, dict)
            }
            for matcher in payload_analysis["unsafe_site_matchers"]:
                matcher_id = matcher.get("id") if isinstance(matcher, dict) else None
                if matcher_id is not None and matcher_id in existing_ids:
                    continue
                merged_matchers.append(matcher)
                if matcher_id is not None:
                    existing_ids.add(matcher_id)
            merged_analysis["unsafe_site_matchers"] = merged_matchers
        sources.append(source)
    return merged, sources


def load_family_profile(family_id: str) -> dict:
    family_path = _profile_path("families", "family_id", family_id)
    family = _load_json(family_path)

    merged = {}
    sources: list[str] = []
    rule_pack_ids: list[str] = []
    template_id = family.get("template_id")
    if template_id:
        template_path = _profile_path("templates", "template_id", template_id)
        template = _load_json(template_path)
        merged = _deep_merge(merged, template)
        sources.append(_rel(template_path))
        rule_pack_ids = _merge_id_list(rule_pack_ids, template.get("rule_pack_ids"))

    merged = _deep_merge(merged, family)
    sources.append(_rel(family_path))
    rule_pack_ids = _merge_id_list(rule_pack_ids, family.get("rule_pack_ids"))
    merged, rule_pack_sources = _apply_rule_packs(merged, rule_pack_ids)
    sources.extend(rule_pack_sources)
    merged["profile_sources"] = sources
    merged["rule_pack_ids"] = rule_pack_ids
    merged["rule_pack_sources"] = rule_pack_sources
    return merged


def load_scenario_profile(scenario_id: str) -> dict:
    scenario_path = _profile_path("scenarios", "scenario_id", scenario_id)
    scenario = _load_json(scenario_path)
    scenario["profile_sources"] = [_rel(scenario_path)]
    return scenario


def load_module_profile(module_path: str | Path) -> dict:
    module_path = str(Path(module_path))
    module_profile_path = _profile_path("modules", "module_path", module_path)
    module_profile = _load_json(module_profile_path)
    family = load_family_profile(module_profile["family_id"])
    merged = _deep_merge(family, module_profile)
    rule_pack_ids = _merge_id_list(family.get("rule_pack_ids"), module_profile.get("rule_pack_ids"))
    merged, rule_pack_sources = _apply_rule_packs(merged, module_profile.get("rule_pack_ids", []))
    merged["profile_sources"] = [*family.get("profile_sources", []), _rel(module_profile_path), *rule_pack_sources]
    merged["rule_pack_ids"] = rule_pack_ids
    merged["rule_pack_sources"] = [*family.get("rule_pack_sources", []), *rule_pack_sources]
    merged.setdefault("profile_id", module_profile.get("module_id", Path(module_path).stem))
    merged.setdefault("module_id", Path(module_path).stem)
    merged.setdefault("artifact_dir", f"Documentation/rust/c2saferust/{merged['module_id']}")
    merged.setdefault(
        "driver_rust_path",
        str(Path(module_path).with_name(f"{Path(module_path).stem}_rust.rs")),
    )
    return merged
