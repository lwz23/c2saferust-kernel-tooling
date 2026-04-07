#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_PATH = Path(__file__).with_name("profiles") / "target_candidates.json"


def _load_seed() -> dict:
    return json.loads(DATA_PATH.read_text())


def _weighted_total(scores: dict[str, int], weights: dict[str, int]) -> int:
    return sum(int(scores.get(key, 0)) * int(weight) for key, weight in weights.items())


def _sorted_candidates(candidates: list[dict]) -> list[dict]:
    return sorted(
        candidates,
        key=lambda entry: (
            entry["total_score"],
            entry["scores"].get("community_match", 0),
            entry["scores"].get("test_feasibility", 0),
            entry["scores"].get("source_availability", 0),
        ),
        reverse=True,
    )


def build_candidate_target_scoreboard() -> dict:
    seed = _load_seed()
    weights = seed["weights"]
    current_primary = seed["current_primary"]

    candidates = []
    for candidate in seed["candidates"]:
        total_score = _weighted_total(candidate["scores"], weights)
        candidates.append(
            {
                **candidate,
                "total_score": total_score,
            }
        )
    candidates = _sorted_candidates(candidates)
    by_id = {candidate["candidate_id"]: candidate for candidate in candidates}
    primary = by_id[current_primary]
    best = candidates[0]

    switch_allowed = (
        best["candidate_id"] != current_primary
        and best["scores"]["community_match"] > primary["scores"]["community_match"]
        and best["scores"]["test_feasibility"] > primary["scores"]["test_feasibility"]
        and best["total_score"] >= primary["total_score"] + 1
    )

    if switch_allowed:
        recommendation = {
            "action": "pivot-primary",
            "candidate_id": best["candidate_id"],
            "reason": (
                f"{best['candidate_id']} exceeds {current_primary} on both community_match and "
                f"test_feasibility and clears the total-score threshold."
            ),
        }
    else:
        recommendation = {
            "action": "keep-primary",
            "candidate_id": current_primary,
            "reason": (
                f"No candidate beats {current_primary} on both community_match and "
                "test_feasibility while also clearing the switch threshold."
            ),
        }

    return {
        "schema_version": 1,
        "artifact_type": "candidate-target-scoreboard",
        "current_primary": current_primary,
        "weights": weights,
        "recommendation": recommendation,
        "candidates": candidates,
    }
