"""Preregistered, deterministic statement alignment using only concept and evidence."""

from __future__ import annotations

from .phase_state import fingerprint

POLICY = {
    "version": "scoped-statement-matching-v2",
    "candidate": "same canonical concept or casefolded unresolved phrase; span IoU >= 0.5",
    "objective": "maximum cardinality, then maximum summed IoU rounded to millionths",
    "ties": "stable sorted span/concept/full-record fingerprints; lowest assignment column",
    "semantics": "status/context/attribute agreement never increases edge weight",
    "duplicates": "extra predictions remain false positives; duplicate gold is rejected",
}


def concept(row: dict) -> str:
    return row.get("concept") or row.get("original_concept", "").casefold()


def overlap(left: dict, right: dict) -> float:
    intersection = max(
        0,
        min(left["evidence_end"], right["evidence_end"])
        - max(left["evidence_start"], right["evidence_start"]),
    )
    union = max(left["evidence_end"], right["evidence_end"]) - min(
        left["evidence_start"], right["evidence_start"]
    )
    return intersection / union if union > 0 else 0


def _order(rows: list[dict]) -> list[int]:
    return sorted(
        range(len(rows)),
        key=lambda i: (
            concept(rows[i]),
            rows[i]["evidence_start"],
            rows[i]["evidence_end"],
            fingerprint(rows[i]),
        ),
    )


def align(predicted: list[dict], gold: list[dict]) -> dict:
    pi, gi = _order(predicted), _order(gold)
    bonus = (min(len(pi), len(gi)) + 1) * 1000000
    candidates = {
        (p, g): overlap(predicted[p], gold[g])
        for p in pi
        for g in gi
        if concept(predicted[p]) == concept(gold[g]) and overlap(predicted[p], gold[g]) >= 0.5
    }
    matrix = [
        [-(bonus + round(candidates[p, g] * 1000000)) if (p, g) in candidates else 0 for g in gi]
        + [0] * len(pi)
        for p in pi
    ]
    assigned = _assignment(matrix) if pi else []
    pairs = [
        (pi[p], gi[g])
        for p, g in enumerate(assigned)
        if g < len(gi) and (pi[p], gi[g]) in candidates
    ]
    used_p, used_g = {p for p, _ in pairs}, {g for _, g in pairs}
    return {
        "pairs": pairs,
        "unmatched_predicted": [p for p in pi if p not in used_p],
        "unmatched_gold": [g for g in gi if g not in used_g],
        "ambiguous_predicted": [p for p in pi if sum((p, g) in candidates for g in gi) > 1],
        "ambiguous_gold": [g for g in gi if sum((p, g) in candidates for p in pi) > 1],
        "pair_iou": [candidates[p, g] for p, g in pairs],
    }


def _assignment(cost: list[list[int]]) -> list[int]:
    """Rectangular Hungarian assignment; enough dummy columns leave rows unmatched."""
    n, m = len(cost), len(cost[0])
    u, v, p, way = [0] * (n + 1), [0] * (m + 1), [0] * (m + 1), [0] * (m + 1)
    for i in range(1, n + 1):
        p[0], j0 = i, 0
        best, used = [float("inf")] * (m + 1), [False] * (m + 1)
        while True:
            used[j0], i0, delta, j1 = True, p[j0], float("inf"), 0
            for j in range(1, m + 1):
                if not used[j]:
                    cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < best[j]:
                        best[j], way[j] = cur, j0
                    if best[j] < delta:
                        delta, j1 = best[j], j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]], v[j] = u[p[j]] + delta, v[j] - delta
                else:
                    best[j] -= delta
            j0 = j1
            if not p[j0]:
                break
        while j0:
            p[j0], j0 = p[way[j0]], way[j0]
    result = [-1] * n
    for j in range(1, m + 1):
        if p[j]:
            result[p[j] - 1] = j - 1
    return result
