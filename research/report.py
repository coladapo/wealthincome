"""Markdown reports for event-study results, including the pre-committed bar check.

The bar (phase-b design): n >= 200 events, positive EV after 2x cost with a CI
that excludes 0, and the edge holds in the held-out and bear slices.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

from research.event_study import EventStudyResult

MIN_EVENTS = 200


def _fmt(v, pct=True, nd=3):
    if v is None:
        return "-"
    return f"{v*100:.{nd}f}" if pct else f"{v:.{nd}f}"


def results_table(result: EventStudyResult) -> str:
    lines = ["| slice | h (days) | n | mean net % | win % | EV per $100 | 95% CI low % | 95% CI high % |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for r in result.rows:
        lines.append(
            f"| {r['slice']} | {r['horizon']} | {r['n']} | {_fmt(r['mean'])} | "
            f"{_fmt(r['win_rate'], nd=1)} | {_fmt(r['ev_per_100'], pct=False)} | "
            f"{_fmt(r['ci_low'])} | {_fmt(r['ci_high'])} |")
    return "\n".join(lines)


def bar_check(result: EventStudyResult) -> Dict[int, Dict[str, bool]]:
    """Per horizon: which bar conditions pass."""
    sm = result.params["stress_mult"]
    out = {}
    bear_names = sorted({r["slice"] for r in result.rows if r["slice"].startswith("bear")})
    for h in result.params["horizons"]:
        allr = result.get("all", h) or {}
        st = result.get(f"stress x{sm:g} cost", h) or {}
        ho = result.get(f"holdout stress x{sm:g}", h) or {}
        bears = [result.get(b, h) or {} for b in bear_names]
        c = {
            "n>=200": (allr.get("n") or 0) >= MIN_EVENTS,
            "stress EV>0, CI>0": bool(st.get("n")) and st["mean"] > 0 and st["ci_low"] > 0,
            "holdout (stress) EV>0": bool(ho.get("n")) and ho["mean"] > 0,
            "bear EV>0": bool(bears) and all(b.get("n") and b["mean"] > 0 for b in bears),
        }
        c["CLEARS BAR"] = all(c.values())
        out[h] = c
    return out


def bar_check_table(result: EventStudyResult) -> str:
    chk = bar_check(result)
    keys = list(next(iter(chk.values())).keys())
    lines = ["| h | " + " | ".join(keys) + " |", "|---:|" + "---|" * len(keys)]
    for h, c in chk.items():
        lines.append(f"| {h} | " + " | ".join("yes" if c[k] else "no" for k in keys) + " |")
    return "\n".join(lines)


def _section(title: str, result: EventStudyResult, params: Dict,
             robustness: Optional[EventStudyResult] = None) -> str:
    parts = [f"## {title}", "", f"Params: `{json.dumps(params, sort_keys=True, default=str)}`", "",
             f"Events generated: {result.n_events_in}; usable (bars available for entry): "
             f"{result.n_events_used}. Holdout starts {result.params.get('holdout_start', '-')}. "
             f"Cost {result.params['cost_bps_per_side']} bp/side; CI bootstrap clustered by "
             f"{result.params['cluster']}.", "", results_table(result), "",
             "Bar check:", "", bar_check_table(result), ""]
    if robustness is not None:
        parts += [f"Robustness: same events, CI clustered by {robustness.params['cluster']} "
                  "(stricter for overlapping holds):", "",
                  "| slice | h | CI low % | CI high % |", "|---|---:|---:|---:|"]
        for r in robustness.rows:
            if r["slice"] in ("all",) or r["slice"].startswith("stress") or r["slice"].startswith("holdout stress"):
                parts.append(f"| {r['slice']} | {r['horizon']} | {_fmt(r['ci_low'])} | {_fmt(r['ci_high'])} |")
        parts.append("")
    return "\n".join(parts)


def write_edge_report(name: str, result: EventStudyResult, params: Dict, path: str,
                      variants: Sequence[Tuple[str, Dict, EventStudyResult]] = (),
                      variants_tried: Optional[int] = None,
                      read: Optional[str] = None,
                      robustness: Optional[EventStudyResult] = None,
                      preamble: Optional[str] = None) -> str:
    """Write a markdown report for `result` (+ optional variants) to `path`; return the text."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    parts = [f"# Edge report: {name}", "", f"Generated {now}.", ""]
    if preamble:
        parts += [preamble, ""]
    if read:
        parts += ["## Honest read", "", read, ""]
    if variants_tried is not None:
        parts += [f"Variants tried for this edge so far (distinct parameter sets in variant_runs): "
                  f"**{variants_tried}**. Treat the best variant's numbers as optimistic by that much.", ""]
    parts.append(_section("Default parameters", result, params, robustness))
    for label, vp, vr in variants:
        parts.append(_section(f"Variant: {label}", vr, vp))
    text = "\n".join(parts)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    return text
