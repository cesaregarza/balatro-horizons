"""Render the public action ledger as a readable retrospective decision report."""

import html
import json
from collections import Counter
from itertools import groupby
from pathlib import Path

DESCRIPTORS = json.loads(
    (Path(__file__).resolve().parents[1] / "web/src/actionDescriptors.json").read_text()
)


def cell(value):
    text = html.escape(str(value), quote=True).replace("\\", "&#92;")
    for char in "|[]`*_":
        text = text.replace(char, f"&#{ord(char)};")
    return " ".join(text.split())


def compact_names(names):
    return (
        "; ".join(
            name if count == 1 else f"{name} ×{count}" for name, count in Counter(names).items()
        )
        or "—"
    )


def describe(row):
    kind = row["type"]
    item = row.get("item", "unknown object")
    descriptor = DESCRIPTORS.get(kind, {"verb": kind, "subject": ""})
    if kind == "reorder":
        return f"Reorder {row['area']}: " + " → ".join(row["ordered_objects"])
    if kind == "play_hand":
        prefix = descriptor["verb"] + " " + (" / ".join(row.get("hand_types", [])) or "hand")
        return prefix + ": " + ", ".join(row.get("cards", []))
    if kind == "discard":
        return descriptor["verb"] + ": " + ", ".join(row.get("cards", []))
    subject = item if descriptor.get("subject") == "item" else descriptor.get("subject", "")
    text = " ".join(part for part in (descriptor["verb"], subject) if part)
    text += descriptor.get("suffix", "")
    if row.get("mode") == "buy_and_use":
        text = text.replace(descriptor["verb"], descriptor.get("buy_and_use", descriptor["verb"]), 1)
    if row.get("targets"):
        text += "; targets: " + ", ".join(row["targets"])
    return text


def consequence(row):
    parts = []
    if row.get("money_change") not in (None, "0", "0.0"):
        parts.append(f"Cash ${row['money_before']} → ${row['money_after']}")
    elif row["type"] in ("buy", "sell", "choose_pack", "reroll_shop", "cash_out", "leave_shop"):
        parts.append(f"Cash ${row['money_after']} (unchanged)")
    if row["type"] == "play_hand":
        parts.extend(
            [
                f"+{row['score']} chips; {row['total_chips']}/{row['target_before']} total",
                f"{row['hands_after']} hands left",
            ]
        )
    elif row["type"] == "discard":
        parts.append(f"{row['discards_after']} discards left")
    elif row["type"] == "select_blind":
        parts.append(f"Target {row['target_after']}")
    elif row["type"] == "skip_blind" and row.get("effects"):
        parts.append("Offered skip reward: " + "; ".join(row["effects"]))
    if row.get("jokers_added"):
        parts.append("Jokers added: " + compact_names(row["jokers_added"]))
    if row.get("jokers_removed"):
        parts.append("Jokers removed: " + compact_names(row["jokers_removed"]))
    return "; ".join(parts) or "Committed"


def table(lines, headings, rows):
    lines.extend(
        [
            "| " + " | ".join(headings) + " |",
            "| " + " | ".join("---" for _ in headings) + " |",
        ]
    )
    lines.extend("| " + " | ".join(cell(value) for value in row) + " |" for row in rows)
    lines.append("")


def render_decisions(result):
    manifest, summary = result["manifest"], result["summary"] or {}
    actions = result["actions"]
    groups = [(ante, list(rows)) for ante, rows in groupby(actions, key=lambda row: row["ante"])]
    lines = [
        f"# Decision summary — {cell(manifest.get('agent', 'agent'))}",
        "",
        f"Episode: `{cell(manifest['episode_id'])}`. "
        f"{len(actions)} committed actions; outcome: {cell(summary.get('outcome', 'ongoing'))}. "
        f"Harness-accounted cost: ${summary.get('cost_usd', 0):.4f}.",
        "",
        f"Evidence kind: {cell(manifest.get('evidence_kind', 'unspecified'))}.",
        "",
        "Decisions are grouped chronologically by ante. D numbers are the journal's "
        "zero-based pre-action decision IDs. Model notes are recorded claims, not expert "
        "judgments; the result column records the native transition. Missing notes are left "
        "explicit. Raw provider output and opaque content are omitted. Ante labels use "
        "the pre-action observation; a boss cash-out can already carry the next ante number. "
        "Hand scores retain the target from before play, even if the cleared round resets it.",
        "",
    ]
    if not actions:
        lines.extend(["No committed game actions were recorded.", ""])
    else:
        table(
            lines,
            ["Ante", "Decisions", "Skipped blinds", "Purchases", "Cash, start → end"],
            [
                [
                    ante,
                    f"D{rows[0]['decision']}–D{rows[-1]['decision']}",
                    compact_names(row["item"] for row in rows if row["type"] == "skip_blind"),
                    compact_names(row["item"] for row in rows if row["type"] == "buy"),
                    f"${rows[0]['money_before']} → ${rows[-1]['money_after']}",
                ]
                for ante, rows in groups
            ],
        )
    if result.get("rounds"):
        lines.extend(["**Recorded blind results**", ""])
        table(
            lines,
            ["Ante", "Blind", "Score / target", "Hands", "Discards", "Cleared"],
            [
                [
                    row["ante"],
                    row["blind"],
                    f"{row.get('total_chips', '—')} / {row['target']}",
                    row["hands_played"],
                    row["discards_used"],
                    "Yes" if row["cleared"] else "No",
                ]
                for row in result["rounds"]
            ],
        )
    for ante, rows in groups:
        lines.extend(
            [f"**Ante {cell(ante)} — D{rows[0]['decision']}–D{rows[-1]['decision']}**", ""]
        )
        table(
            lines,
            ["Decision", "Action / selection", "Recorded result", "Model note"],
            [
                [
                    f"D{row['decision']}",
                    describe(row),
                    consequence(row),
                    row.get("note") or "No note recorded.",
                ]
                for row in rows
            ],
        )
        build = " → ".join(rows[-1].get("jokers_after", [])) or "No Jokers"
        lines.extend([f"Visible Joker order after this block: {cell(build)}.", ""])
    if result.get("uncommitted_actions"):
        lines.extend(
            [
                "**Requests without a committed transition**",
                "",
                "These requests are excluded from the committed-action count. No settled result "
                "is inferred for them.",
                "",
            ]
        )
        table(
            lines,
            ["Decision", "Phase", "Requested action", "Recorded status", "Model note"],
            [
                [
                    f"D{row['decision']}",
                    row["phase"],
                    describe(row),
                    row.get("rejection_code") or row["status"],
                    row.get("note") or "No note recorded.",
                ]
                for row in result["uncommitted_actions"]
            ],
        )
    helpers = result.get("helper_calls", [])
    if helpers:
        names = [
            f"{op['kind']}: {op.get('name') or op.get('section') or op.get('key') or ''}"
            for op in helpers
        ]
        lines.extend([f"Additional model lookups: {cell(compact_names(names))}.", ""])
    return "\n".join(lines)
