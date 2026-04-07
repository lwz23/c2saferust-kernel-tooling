#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

from __future__ import annotations

import argparse
import html
import textwrap
from dataclasses import dataclass
from pathlib import Path


FONT_FAMILY = "Liberation Sans, DejaVu Sans, Noto Sans, sans-serif"
WIDTH = 1900
HEIGHT = 1160
MARGIN_X = 40
MARGIN_TOP = 40
LANE_LABEL_WIDTH = 200
LANE_HEIGHT = 180
LANE_GAP = 12
NODE_WIDTH = 220
NODE_HEIGHT = 80
NOTE_HEIGHT = 56
NOTE_GAP = 8
TITLE_SIZE = 22
BODY_SIZE = 14
SMALL_SIZE = 12


@dataclass(frozen=True)
class Lane:
    name: str
    fill: str
    stroke: str


@dataclass(frozen=True)
class Node:
    node_id: str
    lane: int
    x: int
    y_offset: int
    title: str
    lines: tuple[str, ...]
    fill: str
    stroke: str
    note: tuple[str, ...] = ()

    @property
    def y(self) -> int:
        return MARGIN_TOP + self.lane * (LANE_HEIGHT + LANE_GAP) + self.y_offset


LANES = (
    Lane("Researcher / Operator", "#F8FAFC", "#CBD5E1"),
    Lane("Static Toolchain", "#EFF6FF", "#93C5FD"),
    Lane("Coding Agent", "#FFF7ED", "#FDBA74"),
    Lane("Review Agent", "#FEF2F2", "#FCA5A5"),
    Lane("Dynamic Oracle", "#ECFDF5", "#86EFAC"),
)

PALETTE = {
    "research": ("#E2E8F0", "#64748B"),
    "static": ("#DBEAFE", "#3B82F6"),
    "coding": ("#FED7AA", "#F97316"),
    "review": ("#FECACA", "#DC2626"),
    "oracle": ("#D1FAE5", "#10B981"),
    "note": ("#F8FAFC", "#94A3B8"),
}

NODES = (
    Node(
        "research_intake",
        0,
        260,
        50,
        "Import External C Driver",
        ("Collect source tree, headers,", "and intended landing path."),
        *PALETTE["research"],
    ),
    Node(
        "research_scope",
        0,
        790,
        50,
        "Constrain The Work",
        ("Accept only artifact-backed", "claims and managed-file edits."),
        *PALETTE["research"],
    ),
    Node(
        "research_feedback",
        0,
        1590,
        50,
        "Feed Lessons Back",
        ("Update family/profile logic", "for the next module."),
        *PALETTE["research"],
    ),
    Node(
        "static_bootstrap",
        1,
        260,
        40,
        "bootstrap-module",
        ("Profile the external module", "against kernel-tree landing."),
        *PALETTE["static"],
        note=("translation-plan.json", "abstraction-plan.json"),
    ),
    Node(
        "static_gap",
        1,
        520,
        40,
        "Gap Audits",
        ("Audit bindings, helpers,", "Kbuild, and external headers."),
        *PALETTE["static"],
        note=("binding-gap-audit.json", "helper-audit.json"),
    ),
    Node(
        "static_safety",
        1,
        780,
        40,
        "Static Gates",
        ("verify-safety and", "verify-command-semantics."),
        *PALETTE["static"],
        note=("safety-verdict.json", "command-semantics-verdict.json"),
    ),
    Node(
        "static_agent",
        1,
        1040,
        40,
        "Agent Contract",
        ("Emit workflow scope,", "gates, and allowlists."),
        *PALETTE["static"],
        note=("agent-workflow-plan.json", "managed files + gates"),
    ),
    Node(
        "static_diff",
        1,
        1590,
        40,
        "Differential Verdict",
        ("Compare baseline and", "candidate run-records."),
        *PALETTE["static"],
        note=("differential-oracle-run-record.json", "evidence_tier"),
    ),
    Node(
        "coding_impl",
        2,
        1040,
        48,
        "Implement Candidate",
        ("Write Rust driver and", "needed kernel abstractions."),
        *PALETTE["coding"],
    ),
    Node(
        "coding_revise",
        2,
        1320,
        48,
        "Revise After Findings",
        ("Close soundness, lifecycle,", "and scope regressions."),
        *PALETTE["coding"],
    ),
    Node(
        "review_audit",
        3,
        1040,
        48,
        "Audit For Blockers",
        ("Publication, teardown,", "mmap, and RFC cleanliness."),
        *PALETTE["review"],
    ),
    Node(
        "review_findings",
        3,
        1320,
        48,
        "Return Findings",
        ("Blocking findings and", "residual limitations only."),
        *PALETTE["review"],
    ),
    Node(
        "oracle_build",
        4,
        1040,
        48,
        "Build Runtime Oracle",
        ("Compile guest tester and", "prepare managed guest assets."),
        *PALETTE["oracle"],
        note=("guest tester build", "minimal guest / emulator assets"),
    ),
    Node(
        "oracle_run",
        4,
        1320,
        48,
        "Run Baseline + Candidate",
        ("Use the same goldfish", "device model and tester."),
        *PALETTE["oracle"],
    ),
    Node(
        "oracle_records",
        4,
        1590,
        48,
        "Collect Run Records",
        ("Persist baseline/candidate", "records for field comparison."),
        *PALETTE["oracle"],
        note=("oracle-baseline-run-record.json", "oracle-candidate-run-record.json"),
    ),
)


def wrap_lines(lines: tuple[str, ...], max_chars: int) -> list[str]:
    wrapped: list[str] = []
    for line in lines:
        wrapped.extend(textwrap.wrap(line, width=max_chars) or [""])
    return wrapped


def add_text(elements: list[str], x: float, y: float, lines: list[str], size: int, weight: str = "400") -> None:
    if not lines:
        return
    escaped = [html.escape(line) for line in lines]
    tspans = []
    for index, line in enumerate(escaped):
        dy = "0" if index == 0 else "1.25em"
        tspans.append(f'<tspan x="{x}" dy="{dy}">{line}</tspan>')
    elements.append(
        f'<text x="{x}" y="{y}" font-family="{FONT_FAMILY}" '
        f'font-size="{size}" font-weight="{weight}" fill="#0F172A">'
        + "".join(tspans)
        + "</text>"
    )


def node_geometry(node: Node) -> tuple[int, int, int, int]:
    return node.x, node.y, NODE_WIDTH, NODE_HEIGHT


def note_geometry(node: Node) -> tuple[int, int, int, int]:
    return node.x, node.y + NODE_HEIGHT + NOTE_GAP, NODE_WIDTH, NOTE_HEIGHT


def add_node(elements: list[str], node: Node) -> None:
    x, y, w, h = node_geometry(node)
    elements.append(
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" ry="14" '
        f'fill="{node.fill}" stroke="{node.stroke}" stroke-width="2"/>'
    )
    add_text(elements, x + 16, y + 24, wrap_lines((node.title,), 26), 15, "700")
    add_text(elements, x + 16, y + 46, wrap_lines(node.lines, 28), BODY_SIZE)
    if node.note:
        nx, ny, nw, nh = note_geometry(node)
        note_fill, note_stroke = PALETTE["note"]
        elements.append(
            f'<rect x="{nx}" y="{ny}" width="{nw}" height="{nh}" rx="12" ry="12" '
            f'fill="{note_fill}" stroke="{note_stroke}" stroke-width="1.5"/>'
        )
        add_text(elements, nx + 14, ny + 20, wrap_lines(node.note, 34), SMALL_SIZE, "600")


def center_right(node: Node) -> tuple[float, float]:
    x, y, w, h = node_geometry(node)
    return x + w, y + h / 2


def center_left(node: Node) -> tuple[float, float]:
    x, y, _, h = node_geometry(node)
    return x, y + h / 2


def top_center(node: Node) -> tuple[float, float]:
    x, y, w, _ = node_geometry(node)
    return x + w / 2, y


def bottom_center(node: Node) -> tuple[float, float]:
    x, y, w, h = node_geometry(node)
    return x + w / 2, y + h


def add_arrow(
    elements: list[str],
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    dashed: bool = False,
    elbow: bool = False,
) -> None:
    dash = ' stroke-dasharray="8 6"' if dashed else ""
    if elbow:
        mid_x = (start[0] + end[0]) / 2
        points = f"{start[0]},{start[1]} {mid_x},{start[1]} {mid_x},{end[1]} {end[0]},{end[1]}"
        elements.append(
            f'<polyline points="{points}" fill="none" stroke="#475569" stroke-width="2.2"{dash} '
            'marker-end="url(#arrowhead)"/>'
        )
    else:
        elements.append(
            f'<line x1="{start[0]}" y1="{start[1]}" x2="{end[0]}" y2="{end[1]}" '
            f'stroke="#475569" stroke-width="2.2"{dash} marker-end="url(#arrowhead)"/>'
        )


def render_svg() -> str:
    elements: list[str] = []
    elements.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-labelledby="title desc">'
    )
    elements.append("<title id=\"title\">Goldfish C2SafeRust Workflow</title>")
    elements.append(
        "<desc id=\"desc\">Swimlane diagram showing how static tooling, coding agent, review "
        "agent, and differential oracle collaborate on the goldfish_address_space migration.</desc>"
    )
    elements.append(
        "<defs>"
        '<marker id="arrowhead" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">'
        '<polygon points="0 0, 10 3.5, 0 7" fill="#475569"/>'
        "</marker>"
        "</defs>"
    )
    elements.append(
        f'<rect x="0" y="0" width="{WIDTH}" height="{HEIGHT}" fill="#FFFFFF"/>'
    )
    elements.append(
        f'<text x="{MARGIN_X}" y="30" font-family="{FONT_FAMILY}" font-size="{TITLE_SIZE}" '
        'font-weight="700" fill="#0F172A">Goldfish C2SafeRust Workflow For An External Linux Driver</text>'
    )
    elements.append(
        f'<text x="{MARGIN_X}" y="54" font-family="{FONT_FAMILY}" font-size="{BODY_SIZE}" '
        'fill="#334155">The workflow combines static artifacts, a constrained coding agent, an '
        'independent review agent, and a baseline-vs-candidate differential oracle.</text>'
    )

    lane_x = MARGIN_X
    lane_y = MARGIN_TOP + 30
    lane_width = WIDTH - 2 * MARGIN_X
    for index, lane in enumerate(LANES):
        y = lane_y + index * (LANE_HEIGHT + LANE_GAP)
        elements.append(
            f'<rect x="{lane_x}" y="{y}" width="{lane_width}" height="{LANE_HEIGHT}" rx="18" ry="18" '
            f'fill="{lane.fill}" stroke="{lane.stroke}" stroke-width="1.6"/>'
        )
        elements.append(
            f'<rect x="{lane_x}" y="{y}" width="{LANE_LABEL_WIDTH}" height="{LANE_HEIGHT}" rx="18" ry="18" '
            f'fill="#FFFFFF" stroke="{lane.stroke}" stroke-width="1.2"/>'
        )
        add_text(
            elements,
            lane_x + 22,
            y + 52,
            wrap_lines((lane.name,), 18),
            17,
            "700",
        )

    for node in NODES:
        add_node(elements, node)

    lookup = {node.node_id: node for node in NODES}
    add_arrow(elements, bottom_center(lookup["research_intake"]), top_center(lookup["static_bootstrap"]))
    add_arrow(elements, center_right(lookup["static_bootstrap"]), center_left(lookup["static_gap"]))
    add_arrow(elements, center_right(lookup["static_gap"]), center_left(lookup["static_safety"]))
    add_arrow(elements, center_right(lookup["static_safety"]), center_left(lookup["static_agent"]))
    add_arrow(elements, bottom_center(lookup["research_scope"]), top_center(lookup["static_agent"]), elbow=True)
    add_arrow(elements, bottom_center(lookup["research_scope"]), top_center(lookup["coding_impl"]), elbow=True)
    add_arrow(elements, bottom_center(lookup["static_agent"]), top_center(lookup["coding_impl"]))
    add_arrow(elements, bottom_center(lookup["coding_impl"]), top_center(lookup["review_audit"]))
    add_arrow(elements, center_right(lookup["review_audit"]), center_left(lookup["review_findings"]))
    add_arrow(elements, top_center(lookup["review_findings"]), bottom_center(lookup["coding_revise"]))
    add_arrow(elements, center_left(lookup["coding_revise"]), center_right(lookup["coding_impl"]), dashed=True)
    add_arrow(elements, top_center(lookup["coding_revise"]), bottom_center(lookup["static_safety"]), dashed=True, elbow=True)
    add_arrow(elements, bottom_center(lookup["coding_revise"]), top_center(lookup["oracle_build"]), elbow=True)
    add_arrow(elements, center_right(lookup["oracle_build"]), center_left(lookup["oracle_run"]))
    add_arrow(elements, center_right(lookup["oracle_run"]), center_left(lookup["oracle_records"]))
    add_arrow(elements, top_center(lookup["oracle_records"]), bottom_center(lookup["static_diff"]), elbow=True)
    add_arrow(elements, top_center(lookup["static_diff"]), bottom_center(lookup["research_feedback"]))

    legend_y = HEIGHT - 108
    elements.append(
        f'<text x="{MARGIN_X}" y="{legend_y}" font-family="{FONT_FAMILY}" font-size="16" '
        'font-weight="700" fill="#0F172A">Legend</text>'
    )
    legend_items = (
        ("Static tooling / artifacts", *PALETTE["static"]),
        ("Coding agent", *PALETTE["coding"]),
        ("Review agent", *PALETTE["review"]),
        ("Dynamic oracle", *PALETTE["oracle"]),
        ("Artifact note", *PALETTE["note"]),
    )
    lx = MARGIN_X
    for label, fill, stroke in legend_items:
        elements.append(
            f'<rect x="{lx}" y="{legend_y + 18}" width="28" height="18" rx="6" ry="6" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>'
        )
        elements.append(
            f'<text x="{lx + 38}" y="{legend_y + 32}" font-family="{FONT_FAMILY}" font-size="{SMALL_SIZE}" '
            f'fill="#334155">{html.escape(label)}</text>'
        )
        lx += 300

    elements.append("</svg>")
    return "\n".join(elements) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render the goldfish C2SafeRust retrospective workflow as an SVG diagram."
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Path to the SVG file that should be generated.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    svg = render_svg()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(svg, encoding="utf-8")


if __name__ == "__main__":
    main()
