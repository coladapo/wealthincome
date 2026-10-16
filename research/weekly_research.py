"""Weekly, bounded research session: a model proposes, the pipeline decides.

Once a week (launchd, Sundays 09:00 PT) ONE prompt goes to the model with the
pipeline's state; ONE structured (JSON) reply comes back with at most three
economically motivated hypotheses, each with a pre-registered, machine-checkable
pass bar, a trial budget (<= 5) and the body of a strategy class. The model has
no tools and no file access, and it never sees a result:

  1. gather_state()     read-only: registry, hypotheses + trials, variant counts,
                        latest walk-forward numbers, shadow records, rejected edges
                        (report headlines from the artifacts folder), sleeve
                        allocation, data available, the bar each sleeve must clear;
  2. build_prompt()     system = role + rules, user = state + the Strategy contract
                        + one existing strategy as the template + the reply schema;
  3. validate_reply()   strict JSON; a proposal that reuses an already-rejected edge
                        without a new, stated reason is rejected, as is anything
                        outside the schema (unknown bar criteria, unusable data, ...);
  4. per accepted proposal (THIS code, not the model): write the module to
     research/strategies/proposed/<yyyy-ww>/<id>.py (ast syntax + safety check; a
     failure is recorded, never repaired by the model), pre-register the hypothesis
     with the model's bar and budget, run ONE budgeted trial through
     research.pipeline.evaluate, and check the bar mechanically. The effective bar
     is the model's criteria merged with the sleeve's FLOOR (SLEEVE_FLOORS, the
     stricter value wins): the model can tighten a sleeve's bar, never loosen it.
     Cleared -> candidate moves to shadow. Nothing here moves a strategy beyond
     shadow: champion needs research.pipeline promote --approved-by chris;
  5. write the report (data/persistent/research-sessions/<date>.md + a copy in the
     artifacts folder), post ONE line to #wealthincome, log the session in
     research_sessions (data/research.db).

No orders, no risk limits, no allocation, no promotion.
"""

from __future__ import annotations

import ast
import glob
import importlib
import importlib.util
import inspect
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from research import experiments
from research import strategy as registry
from research.bars import DEFAULT_DB_PATH, REPO_ROOT, connect

DEFAULT_ARTIFACTS_DIR = "/Users/wivak/.claude/shared-context/2026-10-14-audit-wealthincome-c7e2/artifacts"
PROPOSED_DIR = os.path.join(REPO_ROOT, "research", "strategies", "proposed")
MODEL = "opus"                 # evergreen alias; the RESOLVED id from the envelope is logged
EFFORT = "high"
MODEL_TIMEOUT_S = 1800
EVAL_TIMEOUT_S = 1200          # one walk-forward trial of a drafted strategy
MAX_PROPOSALS = 3
MAX_TRIAL_BUDGET = 5
PROPOSAL_HORIZONS = ("swing", "long", "treasury")   # day sleeve: intraday simulator, not this pipeline
DATA_SOURCES = ("daily_bars", "earnings_events")
MIN_NEW_REASON_CHARS = 40
ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,40}$")
CLASS_RE = re.compile(r"^[A-Z][A-Za-z0-9]{2,60}$")

# ---------------------------------------------------------------------------- the bar
# Machine-checkable bar criteria the model may pre-register. Every criterion is checked
# at 1x AND 2x cost except where noted. kind: "ci" (date|month|both), "min" (value must
# be >= threshold), "floor" (max drawdown must be >= threshold, i.e. shallower), "flag".
CRITERIA: Dict[str, Tuple[str, str]] = {
    "excess_vs_spy_ci": ("ci", "annualised excess return vs SPY buy-and-hold: 95% CI low > 0 "
                               "under date clusters, month clusters, or both"),
    "min_ann_excess_vs_spy": ("min", "annualised mean excess vs SPY >= value (e.g. 0.01 = 1%/yr)"),
    "beat_champion": ("flag", "annualised excess vs the real champion's paper equity > 0 "
                              "(common dates only, ~6 months of live record)"),
    "min_sharpe": ("min", "annualised Sharpe >= value"),
    "sharpe_at_least_spy": ("flag", "Sharpe >= SPY buy-and-hold's over the same dates"),
    "max_drawdown_shallower_than_spy": ("flag", "max drawdown shallower than SPY's over the same dates"),
    "max_drawdown_floor": ("floor", "max drawdown >= value (e.g. -0.20 = no worse than -20%)"),
    "not_worse_than_spy_2022": ("flag", "calendar-2022 return >= SPY's 2022 return (the bear slice)"),
    "min_trades": ("min", "number of simulated trades >= value"),
    "min_ann_return": ("min", "CAGR >= value"),
    "return_ci_above_zero": ("ci", "mean daily return vs cash at 0: 95% CI low > 0 (date/month/both)"),
    "min_dsr": ("min", "deflated Sharpe ratio over all trials of the hypothesis >= value "
                       "(cost-independent; 1x run)"),
}
CI_ORDER = {"date": 1, "month": 2, "both": 3}

# The sleeve's own bar (sleeve-design.md + the tightened bar of phase-b-design.md and the
# G1 sleeve reports). Merged into every proposal's bar; the stricter value wins.
SLEEVE_FLOORS: Dict[str, Dict[str, Any]] = {
    "swing": {"excess_vs_spy_ci": "both", "not_worse_than_spy_2022": True, "min_trades": 200},
    "long": {"sharpe_at_least_spy": True, "max_drawdown_shallower_than_spy": True,
             "not_worse_than_spy_2022": True},
    "treasury": {"return_ci_above_zero": "both", "max_drawdown_floor": -0.01},
}

# Already-rejected edges: report-file stem -> phrases that identify a proposal reusing it.
REJECTED_EDGE_KEYWORDS: Dict[str, List[str]] = {
    "mean-reversion": ["mean reversion", "mean-reversion", "oversold", "rsi"],
    "gap-fill": ["gap fill", "gap-fill", "gap down", "gap-down", "gap up", "gap-up", "opening gap"],
    "pead": ["pead", "post-earnings", "post earnings", "earnings drift", "earnings surprise",
             "earnings beat"],
    "orb-intraday": ["opening range", "opening-range", "orb"],
    "trend-filtered-core": ["200-day", "200 day", "moving average", "trend filter", "trend-filter",
                            "sma"],
}

DATA_AVAILABLE_TEXT = """\
- Daily OHLCV bars (yfinance, split/dividend adjusted) 2019-01-02 onward for the CURRENT S&P 500
  constituents plus SPY/QQQ/IWM and the T-bill ETFs BIL/SGOV. SURVIVORSHIP-BIASED: names that left
  the index are missing, so long edges are flattered. No historical index membership.
- Earnings events per symbol: report_date, timing (bmo/amc/unknown), eps_estimate, eps_actual,
  surprise_pct (Yahoo; the estimate is not a guaranteed point-in-time consensus).
- Minute bars: 2 years for 52 names (SPY, QQQ + 50 liquid names) in a SEPARATE intraday store with
  its own simulator. NOT reachable from this daily pipeline -- do not propose intraday rules.
- Options flow / ticks only from Apr 2026 (no history): unusable for a backtest.
- Nothing else: no fundamentals beyond EPS, no analyst revisions, no short interest, no news, no
  insider filings, no macro series."""


# ---------------------------------------------------------------------------- helpers

def iso_week(d: Optional[date] = None) -> str:
    y, w, _ = (d or date.today()).isocalendar()
    return f"{y}-{w:02d}"


def _ro(db_path: Optional[str]) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path or DEFAULT_DB_PATH}?mode=ro", uri=True, timeout=30)


def _has_table(conn, name) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _f(v, pct=True, nd=1) -> str:
    if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
        return "-"
    return f"{v * 100:.{nd}f}%" if pct else f"{v:.2f}"


def _read(path: str) -> str:
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


# ---------------------------------------------------------------------------- 1. state

def parse_report_headlines(artifacts_dir: str) -> List[Dict[str, str]]:
    """One line per edge / hypothesis report: its title and its verdict sentence."""
    out = []
    pat = re.compile(r"(verdict|mechanical result|rejected|fails|does not clear|clears its)", re.I)
    for path in sorted(glob.glob(os.path.join(artifacts_dir, "edge-*-report.md"))
                       + glob.glob(os.path.join(artifacts_dir, "hypothesis-*-report.md"))):
        text = _read(path)
        lines = [ln.strip() for ln in text.splitlines()]
        title = next((ln.lstrip("# ").strip() for ln in lines if ln.startswith("# ")), os.path.basename(path))
        prose = [ln for ln in lines if ln and not ln.startswith(("|", "#")) and not ln.upper().startswith("THE BAR")]
        verdict = (next((ln for ln in prose if re.match(r"^\**verdict", ln, re.I)), None)
                   or next((ln for ln in prose if re.match(r"^\**mechanical result", ln, re.I)), None)
                   or next((ln for ln in prose if pat.search(ln)), ""))
        stem = os.path.basename(path)[:-len("-report.md")]
        verdict = re.sub(r"\s+", " ", verdict.replace("**", ""))[:400]
        out.append({"name": stem.split("-", 1)[1], "file": os.path.basename(path), "title": title,
                    "verdict": verdict, "rejected": bool(REJECTED_VERDICT_RE.search(verdict))})
    return out


REJECTED_VERDICT_RE = re.compile(r"(rejected|fails|does not clear|no arm clears|not clear)", re.I)


def _phase_b_outcome(artifacts_dir: str) -> str:
    """The authoritative rejected-edges table and the tightened bar from phase-b-design.md."""
    text = _read(os.path.join(artifacts_dir, "phase-b-design.md"))
    keep = []
    for ln in text.splitlines():
        if ln.startswith("**New bar") or ln.startswith("| ") or ln.startswith("|---") \
                or ln.startswith("**Consequence"):
            keep.append(ln)
    return "\n".join(keep)


def _sleeve_table(artifacts_dir: str) -> str:
    text = _read(os.path.join(artifacts_dir, "sleeve-design.md"))
    return "\n".join(ln for ln in text.splitlines() if ln.startswith("|") or ln.startswith("- Risk policy"))


def _baseline_table(artifacts_dir: str) -> str:
    text = _read(os.path.join(artifacts_dir, "pipeline-baselines-report.md"))
    out, on = [], False
    for ln in text.splitlines():
        if ln.startswith("## 1."):
            on = True
            out.append(ln)
            continue
        if on and ln.startswith("## "):
            break
        if on and ln.startswith("|"):
            out.append(ln)
    return "\n".join(out)


def _trial_line(result: Dict) -> str:
    s = (result.get("summary") or {}).get("1.0") or {}
    e1 = (result.get("vs_spy") or {}).get("1.0") or {}
    e2 = (result.get("vs_spy") or {}).get("2.0") or {}
    return (f"CAGR {_f(s.get('cagr'))}, Sharpe {_f(s.get('sharpe'), pct=False)}, max DD "
            f"{_f(s.get('max_drawdown'))}, trades {s.get('n_trades', '-')}; vs SPY ann. "
            f"{_f(e1.get('ann_excess'))} [day CI {_f(e1.get('ann_ci_low'))}, {_f(e1.get('ann_ci_high'))}; "
            f"month CI {_f(e1.get('ann_ci_month_low'))}, {_f(e1.get('ann_ci_month_high'))}] @1x, "
            f"{_f(e2.get('ann_excess'))} @2x")


def gather_state(db_path: Optional[str] = None, artifacts_dir: str = DEFAULT_ARTIFACTS_DIR) -> Dict:
    """Everything the model is shown. Read-only on the research store (mode=ro)."""
    st: Dict[str, Any] = {"as_of": datetime.now().strftime("%Y-%m-%d %H:%M"), "week": iso_week()}
    conn = _ro(db_path)
    try:
        st["strategies"] = []
        if _has_table(conn, "strategies"):
            for r in conn.execute("SELECT id, version, status, horizon, hypothesis_id, notes FROM strategies "
                                  "ORDER BY horizon, created_at, id"):
                st["strategies"].append({"id": r[0], "version": r[1][:12], "status": r[2], "horizon": r[3],
                                         "hypothesis_id": r[4], "notes": (r[5] or "")[:200]})
        st["hypotheses"] = []
        if _has_table(conn, "hypotheses"):
            has_hyp_col = "hypothesis_id" in {c[1] for c in conn.execute("PRAGMA table_info(variant_runs)")}
            for hid, rat, rule, mx in conn.execute("SELECT id, rationale, rule_text, max_trials FROM hypotheses "
                                                   "ORDER BY created_at, id").fetchall():
                used, last = 0, None
                if has_hyp_col:
                    used = conn.execute("SELECT COUNT(*) FROM variant_runs WHERE hypothesis_id=?",
                                        (hid,)).fetchone()[0]
                    row = conn.execute("SELECT edge_name, result_json FROM variant_runs WHERE hypothesis_id=? "
                                       "ORDER BY id DESC LIMIT 1", (hid,)).fetchone()
                    if row and row[1]:
                        try:
                            last = f"{row[0]}: " + _trial_line(json.loads(row[1]))
                        except (ValueError, TypeError):
                            last = None
                st["hypotheses"].append({"id": hid, "trials_used": int(used), "max_trials": int(mx),
                                         "rule": rule[:300], "latest_walk_forward": last})
        st["variant_counts"] = []
        if _has_table(conn, "variant_runs"):
            cols = {c[1] for c in conn.execute("PRAGMA table_info(variant_runs)")}
            grp = "COALESCE(hypothesis_id, '(edge study)')" if "hypothesis_id" in cols else "'(edge study)'"
            st["variant_counts"] = [{"group": g, "edge": e, "n": int(n)} for g, e, n in conn.execute(
                f"SELECT {grp}, edge_name, COUNT(*) FROM variant_runs GROUP BY 1, 2 ORDER BY 1, 2")]
        st["shadow"] = []
        if _has_table(conn, "shadow_equity"):
            for sid, ver, n, first, last in conn.execute(
                    "SELECT strategy_id, version, COUNT(*), MIN(date), MAX(date) FROM shadow_equity "
                    "GROUP BY strategy_id, version ORDER BY strategy_id"):
                eq = conn.execute("SELECT equity FROM shadow_equity WHERE strategy_id=? AND version=? AND date=?",
                                  (sid, ver, last)).fetchone()[0]
                st["shadow"].append({"id": sid, "version": ver[:12], "days": int(n), "first": first,
                                     "last": last, "equity": float(eq)})
        st["allocation"] = {"date": None, "weights": {"treasury": 1.0}, "note": "none recorded (all treasury)"}
        if _has_table(conn, "sleeve_allocation"):
            d = conn.execute("SELECT MAX(date) FROM sleeve_allocation").fetchone()[0]
            if d:
                st["allocation"] = {"date": d, "weights": {s: float(w) for s, w in conn.execute(
                    "SELECT sleeve, weight FROM sleeve_allocation WHERE date=?", (d,))}, "note": "approved by chris"}
        st["data"] = {}
        if _has_table(conn, "daily_bars"):
            n, ns, lo, hi = conn.execute("SELECT COUNT(*), COUNT(DISTINCT symbol), MIN(date), MAX(date) "
                                         "FROM daily_bars").fetchone()
            st["data"]["daily_bars"] = f"{n:,} rows, {ns} symbols, {lo} .. {hi}"
        if _has_table(conn, "earnings_events"):
            n, ns, lo, hi = conn.execute("SELECT COUNT(*), COUNT(DISTINCT symbol), MIN(report_date), "
                                         "MAX(report_date) FROM earnings_events").fetchone()
            st["data"]["earnings_events"] = f"{n:,} events, {ns} symbols, {lo} .. {hi}"
    finally:
        conn.close()
    st["rejected_edges"] = parse_report_headlines(artifacts_dir)
    st["phase_b_outcome"] = _phase_b_outcome(artifacts_dir)
    st["sleeve_table"] = _sleeve_table(artifacts_dir)
    st["baselines"] = _baseline_table(artifacts_dir)
    st["sleeve_floors"] = SLEEVE_FLOORS
    return st


# ---------------------------------------------------------------------------- 2. prompt

SYSTEM_PROMPT = """\
You are the quant research analyst for a solo founder's PAPER trading account. Once a week you get \
the research pipeline's state and propose up to three new hypotheses for it to test. You have no \
tools and no file access; your one reply is parsed by a program. You have NO authority over orders, \
risk limits, capital allocation or promotion: a deterministic pipeline runs each proposal for exactly \
ONE trial and decides, mechanically, with the bar you pre-register now, whether it moves from \
candidate to shadow. You will not see the results in this session.

Rules:
1. Economically motivated only. For every proposal say why the asset would be mispriced and WHO is \
on the other side of the trade (forced sellers, index rebalancers, inattentive investors, risk \
transfer, ...). Pure pattern-mining ("this indicator crossed that one") is not acceptable.
2. Use only the data that exists (listed below). Daily bars and earnings events only; no intraday \
rules, no options, no fundamentals beyond EPS. Respect survivorship bias: long-only stock picks \
from today's S&P 500 are flattered, so prefer ideas whose edge would survive that.
3. Do not re-propose an edge that was already rejected (listed below) unless you state a genuinely \
NEW reason it could work now (a different mechanism, not a re-tuned parameter); set \
related_rejected_edge and new_reason when you do.
4. Pre-register the pass bar BEFORE any result, from the criteria vocabulary given. Your criteria are \
merged with the sleeve's floor; the stricter wins, so you can only tighten. Be honest: a bar you \
expect to fail is fine; a strategy that cannot meet its sleeve floor is a wasted trial.
5. At most 3 proposals; trial_budget between 1 and 5 (only ONE trial runs this week).
6. The class must be deterministic, look-ahead free (use only the AsOfData passed in), long only, \
weights in [0, 0.08] for at most 8 names (swing), or one position up to 1.0 via chassis_override \
(long/treasury sleeves only). No imports (numpy as np, pandas as pd, math, Strategy and Chassis are \
already imported), no file/network/OS access, no dunder tricks. Keep it FAST: target_weights runs \
once per trading day for ~1,450 days at two cost levels; recompute at most weekly or monthly, cache \
the last targets on self keyed by the re-check date, and avoid scanning 500 symbols' full history \
every day (use lookback=).
7. Reply with ONE JSON object and nothing else (no prose, no markdown fences)."""


def _contract_source() -> str:
    doc = (registry.__doc__ or "").split("Registry\n--------")[0].strip()
    base = inspect.getsource(registry.Strategy)
    from research import asof
    api = inspect.getsource(asof.AsOfData)
    from research.portfolio_sim import Chassis
    ch = inspect.getsource(Chassis)
    return (f"research/strategy.py (contract)\n{doc}\n\n{base}\n\nresearch/portfolio_sim.py\n"
            f"{ch}\n\nresearch/asof.py -- what a strategy may read\n{api}")


def _template_source() -> str:
    from research.strategies import trend_filtered_core
    return inspect.getsource(trend_filtered_core)


REPLY_SCHEMA = """\
{
  "session_note": "<one or two sentences: what you looked for this week and why>",
  "proposals": [
    {
      "id": "<snake_case, 3-41 chars, unique, not an existing strategy/hypothesis id>",
      "title": "<short name>",
      "horizon": "swing" | "long" | "treasury",
      "rationale": "<why would this be mispriced>",
      "counterparty": "<who is on the other side and why they accept the loss>",
      "rule": "<the exact rule in plain English, with every parameter value>",
      "params": {<the class's default_params, exactly>},
      "bar": {"plain_english": "<the pass bar in words>",
              "criteria": {<criterion name>: <value>, ...}},
      "trial_budget": <int 1..5>,
      "data_needed": ["daily_bars"] | ["daily_bars", "earnings_events"],
      "related_rejected_edge": null | "<name of the rejected edge this resembles>",
      "new_reason": null | "<the new, stated reason it could work despite that rejection>",
      "class_name": "<CamelCase>",
      "class_source": "<the full `class <class_name>(Strategy): ...` definition, with id = \\"<id>\\" and horizon = \\"<horizon>\\" as class attributes>"
    }
  ]
}"""


def _criteria_text() -> str:
    return "\n".join(f"- {k} ({kind}): {desc}" for k, (kind, desc) in CRITERIA.items())


def build_prompt(state: Dict) -> Tuple[str, str]:
    def block(title, body):
        return f"## {title}\n{body.strip() if body and body.strip() else '(none)'}\n"

    strat = "\n".join(f"- [{s['horizon']}] {s['id']} v{s['version']} status={s['status']} "
                      f"hypothesis={s['hypothesis_id']} -- {s['notes']}" for s in state["strategies"])
    hyp = "\n".join(f"- {h['id']}: {h['trials_used']}/{h['max_trials']} trials. Rule: {h['rule']}"
                    + (f"\n  latest walk-forward ({h['latest_walk_forward']})" if h.get("latest_walk_forward") else "")
                    for h in state["hypotheses"])
    var = "\n".join(f"- {v['group']} / {v['edge']}: {v['n']} trials" for v in state["variant_counts"])
    sh = "\n".join(f"- {s['id']} v{s['version']}: {s['days']} days {s['first']}..{s['last']}, "
                   f"equity ${s['equity']:,.0f} (started at $100,000)" for s in state["shadow"])
    rej = "\n".join(f"- [{'REJECTED' if r.get('rejected') else 'see verdict'}] {r['name']} ({r['file']}): "
                    f"{r['title']} -- {r['verdict']}" for r in state["rejected_edges"])
    rej += ("\n- [REJECTED] gap-fill: its 20-day pass was survivorship (pre-membership run-ups); see the outcome "
            "table below.") if any(r["name"] == "gap-fill" for r in state["rejected_edges"]) else ""
    alloc = state["allocation"]
    al = (f"{alloc.get('date') or 'none'}: "
          + ", ".join(f"{k} {v:.0%}" for k, v in sorted(alloc["weights"].items())) + f" ({alloc.get('note')})")
    data = "\n".join(f"- {k}: {v}" for k, v in state["data"].items())
    floors = "\n".join(f"- {h}: {json.dumps(v)}" for h, v in state["sleeve_floors"].items())
    user = "\n".join([
        f"# Weekly research session {state['week']} (state as of {state['as_of']})", "",
        block("Strategy registry (by sleeve)", strat),
        block("Pre-registered hypotheses and trials used", hyp),
        block("Trials per hypothesis / edge (every trial deflates the selection-adjusted stats)", var),
        block("Shadow records (prospective, virtual, since Oct 2026)", sh),
        block("Pipeline baselines (full out-of-sample walk-forward, live chassis)", state["baselines"]),
        block("Rejected or failed edges (report headlines)", rej),
        block("Edge-study outcome table and the tightened bar (phase-b-design.md)", state["phase_b_outcome"]),
        block("Sleeves (sleeve-design.md)", state["sleeve_table"]),
        block("Current sleeve allocation", al),
        block("Data in the store", data + "\n" + DATA_AVAILABLE_TEXT),
        block("How a trial is run", "research.pipeline.evaluate: walk-forward from 2019-01-01, 24-month train / "
              "6-month test windows stitched (out-of-sample ~2021-01 .. today), targets computed at the close "
              "from completed sessions, filled at the NEXT open, 10 bp/side (2x stress = 20 bp/side). Swing = "
              "live chassis (max 8 positions, 8% each, 12% trailing stop on close, 5% daily loss limit). "
              "Long/treasury may set chassis_override=Chassis(max_positions=1, max_weight=1.0, ...). Compared "
              "with SPY buy-and-hold (cost-free) and the champion's real paper equity, date- and "
              "month-clustered bootstrap CIs, deflated Sharpe over the hypothesis's trials."),
        block("Bar criteria vocabulary (checked at 1x AND 2x cost unless noted)", _criteria_text()),
        block("Sleeve floors (merged into your bar; the stricter value wins)", floors),
        block("The Strategy contract and the data API", _contract_source()),
        block("Template: an existing strategy file (research/strategies/trend_filtered_core.py)", _template_source()),
        block("Reply: strict JSON, exactly this shape", REPLY_SCHEMA),
    ])
    return SYSTEM_PROMPT, user


def estimate_tokens(text: str) -> int:
    return int(len(text) / 4)


# ---------------------------------------------------------------------------- 3. validate

def parse_reply(text: str) -> Dict:
    from core.llm_router import _parse_trading_json
    obj = _parse_trading_json(text)
    if not isinstance(obj, dict) or not isinstance(obj.get("proposals"), list):
        raise ValueError("reply is not an object with a 'proposals' list")
    return obj


def _mentions(text: str, phrase: str) -> bool:
    return re.search(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", text) is not None


def recycled_edge(p: Dict, rejected_names: List[str]) -> Optional[str]:
    """The rejected edge a proposal resembles (declared, or detected from its wording), else None."""
    declared = p.get("related_rejected_edge")
    if declared:
        return str(declared)
    text = " ".join(str(p.get(k) or "") for k in ("id", "title", "rationale", "rule")).lower().replace("_", " ")
    for name in rejected_names:
        for phrase in REJECTED_EDGE_KEYWORDS.get(name, [name.replace("-", " ")]):
            if _mentions(text, phrase.lower()):
                return name
    return None


def _check_criteria(crit: Any) -> Optional[str]:
    if not isinstance(crit, dict) or not crit:
        return "bar.criteria must be a non-empty object"
    for k, v in crit.items():
        if k not in CRITERIA:
            return f"unknown bar criterion {k!r} (not machine-checkable)"
        kind = CRITERIA[k][0]
        if kind == "ci" and v not in CI_ORDER:
            return f"{k} must be one of {sorted(CI_ORDER)}"
        if kind == "flag" and v is not True:
            return f"{k} must be true when given"
        if kind in ("min", "floor") and (isinstance(v, bool) or not isinstance(v, (int, float))):
            return f"{k} must be a number"
        if kind == "floor" and not -1.0 <= float(v) <= 0.0:
            return f"{k} must be in [-1, 0]"
    return None


def validate_proposal(p: Any, state: Dict, rejected_names: List[str]) -> Optional[str]:
    """None if acceptable, else the reason it is rejected."""
    if not isinstance(p, dict):
        return "proposal is not an object"
    for k in ("id", "title", "horizon", "rationale", "counterparty", "rule", "class_name", "class_source"):
        if not isinstance(p.get(k), str) or not p[k].strip():
            return f"missing or empty field {k!r}"
    if not ID_RE.match(p["id"]):
        return f"id {p['id']!r} is not snake_case (3-41 chars)"
    if not CLASS_RE.match(p["class_name"]):
        return f"class_name {p['class_name']!r} is not CamelCase"
    taken = {s["id"] for s in state.get("strategies", [])} | {h["id"] for h in state.get("hypotheses", [])}
    if p["id"] in taken:
        return f"id {p['id']!r} already exists in the registry"
    if p["horizon"] not in PROPOSAL_HORIZONS:
        return f"horizon {p['horizon']!r} not testable here (allowed: {PROPOSAL_HORIZONS})"
    b = p.get("trial_budget")
    if isinstance(b, bool) or not isinstance(b, int) or not 1 <= b <= MAX_TRIAL_BUDGET:
        return f"trial_budget must be an integer 1..{MAX_TRIAL_BUDGET}"
    if not isinstance(p.get("params"), dict):
        return "params must be an object"
    bar = p.get("bar")
    if not isinstance(bar, dict) or not isinstance(bar.get("plain_english"), str) or not bar["plain_english"].strip():
        return "bar.plain_english is required (the bar is pre-registered in words AND criteria)"
    err = _check_criteria(bar.get("criteria"))
    if err:
        return err
    dn = p.get("data_needed")
    if not isinstance(dn, list) or not dn or any(d not in DATA_SOURCES for d in dn):
        return f"data_needed must list only {DATA_SOURCES} (got {dn!r})"
    edge = recycled_edge(p, rejected_names)
    if edge is not None:
        why = p.get("new_reason")
        if not isinstance(why, str) or len(why.strip()) < MIN_NEW_REASON_CHARS:
            return (f"reuses the already-rejected edge {edge!r} without a new, stated reason "
                    f"(new_reason must be >= {MIN_NEW_REASON_CHARS} chars)")
        p["related_rejected_edge"] = edge
    return None


def validate_reply(obj: Dict, state: Dict) -> Tuple[List[Dict], List[Dict]]:
    """(accepted, rejected); rejected items are {proposal, reason}."""
    names = sorted({r["name"] for r in state.get("rejected_edges", []) if r.get("rejected")}
                   | set(REJECTED_EDGE_KEYWORDS))
    accepted, rejected, seen = [], [], set()
    for i, p in enumerate(obj.get("proposals") or []):
        if i >= MAX_PROPOSALS:
            rejected.append({"proposal": p, "reason": f"over the cap of {MAX_PROPOSALS} proposals"})
            continue
        why = validate_proposal(p, state, names)
        if why is None and p["id"] in seen:
            why = "duplicate id within this reply"
        if why is None:
            seen.add(p["id"])
            accepted.append(p)
        else:
            rejected.append({"proposal": p, "reason": why})
    return accepted, rejected


# ---------------------------------------------------------------------------- 4a. module

BANNED_NAMES = {"open", "exec", "eval", "compile", "__import__", "globals", "locals", "vars", "getattr",
                "setattr", "delattr", "input", "breakpoint", "exit", "quit", "help", "memoryview",
                "os", "sys", "subprocess", "importlib", "builtins", "shutil", "socket", "pathlib",
                "pickle", "sqlite3", "requests", "urllib", "inspect"}
ALLOWED_DUNDER_ATTRS = {"__init__"}
MODULE_HEADER_IMPORTS = ("from __future__ import annotations\n\nimport math\n\nimport numpy as np\n"
                         "import pandas as pd\n\nfrom research.portfolio_sim import Chassis\n"
                         "from research.strategy import Strategy\n")


def check_strategy_source(src: str, p: Dict) -> Optional[str]:
    """Static checks on the model's class source. None if fine, else the failure (recorded, not fixed)."""
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return f"syntax error: {e.msg} (line {e.lineno})"
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
    others = [n for n in tree.body if not isinstance(n, (ast.ClassDef, ast.Expr))]
    if len(classes) != 1 or others:
        return "class_source must contain exactly one top-level class definition and nothing else"
    cls = classes[0]
    if cls.name != p["class_name"]:
        return f"class is named {cls.name!r}, proposal says {p['class_name']!r}"
    if not any(isinstance(b, ast.Name) and b.id == "Strategy" for b in cls.bases):
        return "class must subclass Strategy"
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            return "imports are not allowed in the class source"
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            return "global/nonlocal is not allowed"
        if isinstance(node, ast.Name) and (node.id in BANNED_NAMES or node.id.startswith("__")):
            return f"forbidden name {node.id!r}"
        if isinstance(node, ast.Attribute) and node.attr.startswith("__") and node.attr not in ALLOWED_DUNDER_ATTRS:
            return f"forbidden attribute {node.attr!r}"
    attrs = {}
    for n in cls.body:
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name) \
                and isinstance(n.value, ast.Constant):
            attrs[n.targets[0].id] = n.value.value
    if attrs.get("id") != p["id"]:
        return f"class attribute id is {attrs.get('id')!r}, must be {p['id']!r}"
    if attrs.get("horizon") != p["horizon"]:
        return f"class attribute horizon is {attrs.get('horizon')!r}, must be {p['horizon']!r}"
    if not any(isinstance(n, ast.FunctionDef) and n.name == "target_weights" for n in cls.body):
        return "class must define target_weights(self, as_of, data)"
    return None


def _docstring(text: str) -> str:
    return '"""' + text.replace("\\", "\\\\").replace('"""', '\\"\\"\\"') + '\n"""\n'


def module_text(p: Dict, week: str, hypothesis_id: str, model: str) -> str:
    bar = p["bar"]
    doc = (f"{p['title']} -- drafted by a model in weekly research session {week} ({model}); frozen as written.\n\n"
           f"Hypothesis (pre-registered): {hypothesis_id}\n"
           f"Sleeve: {p['horizon']}\n\n"
           f"Why mispriced: {p['rationale']}\n\n"
           f"Who is on the other side: {p['counterparty']}\n\n"
           f"Rule: {p['rule']}\n\n"
           f"Pre-registered bar: {bar['plain_english']}\n"
           f"Bar criteria (merged with the sleeve floor): {json.dumps(effective_bar(p))}\n"
           f"Trial budget: {p['trial_budget']}\n")
    return _docstring(doc) + "\n" + MODULE_HEADER_IMPORTS + "\n\n" + p["class_source"].strip() + "\n"


def write_module(p: Dict, week: str, hypothesis_id: str, model: str,
                 proposed_dir: str = PROPOSED_DIR) -> Tuple[Optional[str], Optional[str]]:
    """(path, None) on success, (path or None, failure) otherwise. Never overwrites."""
    err = check_strategy_source(p["class_source"], p)
    if err:
        return None, err
    text = module_text(p, week, hypothesis_id, model)
    try:
        ast.parse(text)
    except SyntaxError as e:
        return None, f"module syntax error: {e.msg} (line {e.lineno})"
    d = os.path.join(proposed_dir, week)
    path = os.path.join(d, f"{p['id']}.py")
    if os.path.exists(path):
        return path, "a module with this id already exists for this week (frozen; not overwritten)"
    os.makedirs(d, exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    return path, None


def module_name(path: str) -> str:
    rel = os.path.relpath(os.path.abspath(path), REPO_ROOT)
    return rel[:-3].replace(os.sep, ".")


def load_class(path: str, class_name: str):
    """Import the written module by its package path (as the shadow runner will)."""
    import sys
    if os.path.relpath(os.path.abspath(path), REPO_ROOT).startswith(".."):
        # outside the repo (tests): load from the file, registered so inspect finds the source
        name = "_wr_proposed_" + re.sub(r"\W", "_", os.path.abspath(path))
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    else:
        mod = importlib.import_module(module_name(path))
    return getattr(mod, class_name)


# ---------------------------------------------------------------------------- 4b. bar

def effective_bar(p: Dict) -> Dict[str, Any]:
    """The model's criteria merged with the sleeve floor; the stricter value wins per criterion."""
    out = dict(SLEEVE_FLOORS.get(p["horizon"], {}))
    for k, v in (p.get("bar") or {}).get("criteria", {}).items():
        kind = CRITERIA[k][0]
        if k not in out:
            out[k] = v
        elif kind == "ci":
            out[k] = v if CI_ORDER[v] > CI_ORDER[out[k]] else out[k]
        elif kind in ("min", "floor"):
            out[k] = max(float(out[k]), float(v))
        else:
            out[k] = True
    return out


def _year_return(daily_ret: pd.Series, year: int) -> Optional[float]:
    r = daily_ret[(daily_ret.index.year == year)]
    return float((1 + r).prod() - 1) if len(r) else None


def check_bar(bar: Dict[str, Any], res, sel: Dict, spy_daily_ret: pd.Series) -> Tuple[bool, List[Dict]]:
    """Mechanical bar check on one walk-forward result. Returns (passed, checks)."""
    from research.walk_forward import excess_stats
    checks: List[Dict] = []
    spy = res.spy_summary

    def add(name, cost, value, threshold, ok):
        checks.append({"criterion": name, "cost": cost, "value": value, "threshold": threshold, "ok": bool(ok)})

    def num(v):
        return None if v is None or (isinstance(v, float) and np.isnan(v)) else float(v)

    for k, v in bar.items():
        if k == "min_dsr":
            d = num((sel.get("dsr") or {}).get("dsr"))
            add(k, "1x", d, v, d is not None and d >= v)
            continue
        for cm in sorted(res.runs):
            tag = f"{cm:g}x"
            run = res.runs[cm]
            s = run.summary
            e = res.vs_spy.get(cm) or {}
            if k == "excess_vs_spy_ci":
                lo_d, lo_m = num(e.get("ann_ci_low")), num(e.get("ann_ci_month_low"))
                need = {"date": [lo_d], "month": [lo_m], "both": [lo_d, lo_m]}[v]
                add(k, tag, {"day_ci_low": lo_d, "month_ci_low": lo_m}, f"> 0 ({v})",
                    all(x is not None and x > 0 for x in need))
            elif k == "min_ann_excess_vs_spy":
                x = num(e.get("ann_excess"))
                add(k, tag, x, v, x is not None and x >= v)
            elif k == "beat_champion":
                c = res.vs_champion.get(cm) or {}
                x = num(c.get("ann_excess"))
                add(k, tag, x, "> 0", x is not None and x > 0)
            elif k == "min_sharpe":
                x = num(s.get("sharpe"))
                add(k, tag, x, v, x is not None and x >= v)
            elif k == "sharpe_at_least_spy":
                x, b = num(s.get("sharpe")), num(spy.get("sharpe"))
                add(k, tag, x, b, x is not None and b is not None and x >= b)
            elif k == "max_drawdown_shallower_than_spy":
                x, b = num(s.get("max_drawdown")), num(spy.get("max_drawdown"))
                add(k, tag, x, b, x is not None and b is not None and x > b)
            elif k == "max_drawdown_floor":
                x = num(s.get("max_drawdown"))
                add(k, tag, x, v, x is not None and x >= v)
            elif k == "not_worse_than_spy_2022":
                r = run.daily["ret"]
                x, b = _year_return(r, 2022), _year_return(spy_daily_ret.reindex(r.index).fillna(0.0), 2022)
                add(k, tag, x, b, x is not None and b is not None and x >= b)
            elif k == "min_trades":
                x = s.get("n_trades")
                add(k, tag, x, v, x is not None and x >= v)
            elif k == "min_ann_return":
                x = num(s.get("cagr"))
                add(k, tag, x, v, x is not None and x >= v)
            elif k == "return_ci_above_zero":
                r = run.daily["ret"].iloc[1:]
                z = excess_stats(r, pd.Series(0.0, index=r.index))
                lo_d, lo_m = num(z.get("ann_ci_low")), num(z.get("ann_ci_month_low"))
                need = {"date": [lo_d], "month": [lo_m], "both": [lo_d, lo_m]}[v]
                add(k, tag, {"day_ci_low": lo_d, "month_ci_low": lo_m}, f"> 0 ({v})",
                    all(x is not None and x > 0 for x in need))
    return bool(checks) and all(c["ok"] for c in checks), checks


# ---------------------------------------------------------------------------- 4c. run one

class EvaluationTimeout(RuntimeError):
    pass


@contextmanager
def _time_limit(seconds: int):
    if seconds <= 0 or threading.current_thread() is not threading.main_thread():
        yield
        return

    def _raise(signum, frame):
        raise EvaluationTimeout(f"evaluation exceeded {seconds}s")
    old = signal.signal(signal.SIGALRM, _raise)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def hypothesis_id_for(p: Dict, week: str) -> str:
    return f"research-{week}-{p['id']}"


def register_proposal_hypothesis(p: Dict, week: str, db_path: Optional[str] = None) -> Dict:
    bar = effective_bar(p)
    rationale = f"{p['rationale'].strip()} Other side: {p['counterparty'].strip()}"
    rule = (f"{p['rule'].strip()} | params {json.dumps(p.get('params') or {}, sort_keys=True)} | "
            f"PRE-REGISTERED BAR: {p['bar']['plain_english'].strip()} | criteria at 1x and 2x cost "
            f"(sleeve floor merged): {json.dumps(bar, sort_keys=True)} | weekly research session {week}")
    return experiments.register_hypothesis(hypothesis_id_for(p, week), rationale, rule,
                                           max_trials=int(p["trial_budget"]), db_path=db_path)


def promote_to_shadow(strategy_id: str, version: str, week: str, db_path: Optional[str] = None) -> Dict:
    """The ONLY status change this module makes: candidate -> shadow, after a mechanical pass."""
    return registry.set_status(strategy_id, version, "shadow",
                               notes=f"weekly research {week}: cleared its pre-registered bar (mechanical)",
                               db_path=db_path)


def run_proposal(p: Dict, week: str, model: str, db_path: Optional[str] = None, market=None,
                 proposed_dir: str = PROPOSED_DIR, eval_timeout: int = EVAL_TIMEOUT_S,
                 evaluate_fn: Optional[Callable] = None) -> Dict:
    """Write, pre-register, run ONE trial, check the bar. Returns the outcome record."""
    from research import pipeline
    from research.walk_forward import spy_returns
    hid = hypothesis_id_for(p, week)
    out: Dict[str, Any] = {"id": p["id"], "hypothesis_id": hid, "status": None, "module": None,
                           "version": None, "run_id": None, "checks": [], "bar": effective_bar(p),
                           "summary": None, "error": None}
    path, err = write_module(p, week, hid, model, proposed_dir=proposed_dir)
    out["module"] = path
    if err:
        out.update(status="code_rejected", error=err)
        return out
    try:
        strat = load_class(path, p["class_name"])(None)
    except Exception as e:
        out.update(status="load_failed", error=f"{type(e).__name__}: {e}"[:500])
        return out
    out["version"] = strat.version
    try:
        register_proposal_hypothesis(p, week, db_path=db_path)
    except Exception as e:
        out.update(status="register_failed", error=f"{type(e).__name__}: {e}"[:500])
        return out
    t0 = time.time()
    try:
        with _time_limit(eval_timeout):
            ev = (evaluate_fn or pipeline.evaluate)(strat, hid, db_path=db_path, market=market,
                                                    notes=f"weekly research {week}")
    except Exception as e:
        out.update(status="evaluation_failed", error=f"{type(e).__name__}: {e}"[:500],
                   eval_seconds=round(time.time() - t0, 1))
        return out
    out["eval_seconds"] = round(time.time() - t0, 1)
    res, sel = ev["result"], ev["selection"]
    out["run_id"] = ev["run_id"]
    b = res.runs[min(res.runs)].summary
    e1 = res.vs_spy.get(min(res.runs)) or {}
    e2 = res.vs_spy.get(max(res.runs)) or {}
    out["summary"] = {"start": b.get("start"), "end": b.get("end"), "cagr": b.get("cagr"),
                      "sharpe": b.get("sharpe"), "max_drawdown": b.get("max_drawdown"),
                      "avg_exposure": b.get("avg_exposure"), "n_trades": b.get("n_trades"),
                      "spy_cagr": res.spy_summary.get("cagr"), "spy_sharpe": res.spy_summary.get("sharpe"),
                      "spy_max_drawdown": res.spy_summary.get("max_drawdown"),
                      "excess_vs_spy_1x": e1.get("ann_excess"), "excess_ci_1x": [e1.get("ann_ci_low"), e1.get("ann_ci_high")],
                      "excess_month_ci_1x": [e1.get("ann_ci_month_low"), e1.get("ann_ci_month_high")],
                      "excess_vs_spy_2x": e2.get("ann_excess"),
                      "dsr": (sel.get("dsr") or {}).get("dsr")}
    if market is not None:
        spy_r = spy_returns(market, res.runs[min(res.runs)].daily.index)
    else:
        from research.asof import MarketData
        m = MarketData(db_path or DEFAULT_DB_PATH)
        try:
            spy_r = spy_returns(m, res.runs[min(res.runs)].daily.index)
        finally:
            m.close()
    passed, checks = check_bar(out["bar"], res, sel, spy_r.fillna(0.0))
    out["checks"] = checks
    if passed:
        promote_to_shadow(strat.id, strat.version, week, db_path=db_path)
        out["status"] = "shadow"
    else:
        out["status"] = "failed_bar"
    return out


# ---------------------------------------------------------------------------- 5. report + log

SESSIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS research_sessions (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at         TEXT NOT NULL,
    ended_at           TEXT,
    week               TEXT NOT NULL,
    model_requested    TEXT,
    model_resolved     TEXT,
    effort             TEXT,
    input_tokens       INTEGER,
    output_tokens      INTEGER,
    cache_read_tokens  INTEGER,
    cache_write_tokens INTEGER,
    model_seconds      REAL,
    session_seconds    REAL,
    cost_usd_estimate  REAL,
    n_proposed         INTEGER,
    n_accepted         INTEGER,
    n_cleared          INTEGER,
    status             TEXT,
    error              TEXT,
    report_path        TEXT,
    outcome_json       TEXT
);
"""


def log_session(rec: Dict, db_path: Optional[str] = None) -> int:
    conn = connect(db_path)
    try:
        conn.executescript(SESSIONS_SCHEMA)
        cols = ["started_at", "ended_at", "week", "model_requested", "model_resolved", "effort", "input_tokens",
                "output_tokens", "cache_read_tokens", "cache_write_tokens", "model_seconds", "session_seconds",
                "cost_usd_estimate", "n_proposed", "n_accepted", "n_cleared", "status", "error", "report_path",
                "outcome_json"]
        cur = conn.execute(f"INSERT INTO research_sessions({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                           [rec.get(c) for c in cols])
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def summary_line(n_proposed: int, outcomes: List[Dict], error: Optional[str] = None) -> str:
    if error:
        return f"🧪 Weekly research: no proposals this week ({error[:120]})"
    cleared = [o["id"] for o in outcomes if o.get("status") == "shadow"]
    line = f"🧪 Weekly research: {n_proposed} proposed, {len(cleared)} cleared its pre-registered bar"
    if cleared:
        line += f" → shadow ({', '.join(cleared)})"
    return line + f", {n_proposed - len(cleared)} rejected"


def _fmt_check_value(v) -> str:
    if isinstance(v, dict):
        return ", ".join(f"{k} {_f(x)}" for k, x in v.items())
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)


def render_report(rec: Dict, proposals: List[Dict], rejected: List[Dict], outcomes: List[Dict],
                  session_note: str = "") -> str:
    lines = [f"# Weekly research session {rec['week']} ({rec['started_at'][:10]})", "",
             "A model proposed; the pipeline decided. The model had no tools, did not see any result, and "
             "has no authority over orders, risk limits, allocation or promotion. A proposal that cleared its "
             "pre-registered bar moved candidate -> shadow mechanically; champion needs Chris "
             "(`research.pipeline promote --approved-by chris`).", "",
             f"- Model: requested `{rec.get('model_requested')}` effort {rec.get('effort')}, resolved "
             f"`{rec.get('model_resolved') or '?'}` (from the call envelope)",
             f"- Tokens: in {rec.get('input_tokens') or 0:,}, out {rec.get('output_tokens') or 0:,}, cache read "
             f"{rec.get('cache_read_tokens') or 0:,}, cache write {rec.get('cache_write_tokens') or 0:,}; model call "
             f"{rec.get('model_seconds') or 0:.0f}s; whole session {rec.get('session_seconds') or 0:.0f}s; cost "
             f"estimate ${rec.get('cost_usd_estimate') or 0:.2f} at list rates (paid by the subscription)",
             f"- Outcome: {rec.get('summary_line', '')}"]
    if rec.get("error"):
        lines += ["", f"**Session error:** {rec['error']}"]
    if session_note:
        lines += ["", f"Model's note: {session_note}"]
    by_id = {o["id"]: o for o in outcomes}
    for p in proposals:
        o = by_id.get(p["id"], {})
        lines += ["", f"## {p['id']} -- {p['title']} [{p['horizon']}] -> **{o.get('status')}**", "",
                  f"- Why mispriced: {p['rationale']}", f"- Who is on the other side: {p['counterparty']}",
                  f"- Rule: {p['rule']}", f"- Params: `{json.dumps(p.get('params'), sort_keys=True)}`",
                  f"- Pre-registered bar: {p['bar']['plain_english']}",
                  f"- Effective bar (model criteria + sleeve floor, 1x and 2x cost): `{json.dumps(o.get('bar'))}`",
                  f"- Trial budget: {p['trial_budget']} (1 used this session); data: {', '.join(p['data_needed'])}"]
        if p.get("related_rejected_edge"):
            lines.append(f"- Resembles rejected edge `{p['related_rejected_edge']}`; new reason: {p.get('new_reason')}")
        lines.append(f"- Hypothesis: `{o.get('hypothesis_id')}`; module: `{os.path.relpath(o['module'], REPO_ROOT) if o.get('module') else '-'}`"
                     + (f"; version {o['version'][:12]}" if o.get("version") else "")
                     + (f"; trial run_id {o['run_id']}" if o.get("run_id") else ""))
        if o.get("error"):
            lines.append(f"- Failure (recorded, not repaired): {o['error']}")
        s = o.get("summary")
        if s:
            lines.append(f"- Walk-forward {s['start']} .. {s['end']} @1x: CAGR {_f(s['cagr'])} (SPY {_f(s['spy_cagr'])}), "
                         f"Sharpe {_f(s['sharpe'], pct=False)} (SPY {_f(s['spy_sharpe'], pct=False)}), max DD "
                         f"{_f(s['max_drawdown'])} (SPY {_f(s['spy_max_drawdown'])}), avg invested {_f(s['avg_exposure'])}, "
                         f"trades {s['n_trades']}; excess vs SPY {_f(s['excess_vs_spy_1x'])}/yr "
                         f"[day CI {_f(s['excess_ci_1x'][0])}, {_f(s['excess_ci_1x'][1])}; month CI "
                         f"{_f(s['excess_month_ci_1x'][0])}, {_f(s['excess_month_ci_1x'][1])}], @2x "
                         f"{_f(s['excess_vs_spy_2x'])}/yr; DSR {_f(s['dsr'], pct=False)}")
        if o.get("checks"):
            lines += ["", "| criterion | cost | value | threshold | pass |", "|---|---|---|---|---|"]
            for c in o["checks"]:
                lines.append(f"| {c['criterion']} | {c['cost']} | {_fmt_check_value(c['value'])} | "
                             f"{_fmt_check_value(c['threshold'])} | {'yes' if c['ok'] else '**no**'} |")
    if rejected:
        lines += ["", "## Rejected before any run", ""]
        for r in rejected:
            p = r["proposal"] if isinstance(r["proposal"], dict) else {}
            lines.append(f"- `{p.get('id', '?')}` ({p.get('title', '')}): {r['reason']}")
    return "\n".join(lines) + "\n"


def _default_post(text: str) -> bool:
    path = os.path.join(REPO_ROOT, "scripts", "daily_digest.py")
    spec = importlib.util.spec_from_file_location("_wi_daily_digest", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._post_via_webhook(text)


def sessions_dir(db_path: Optional[str] = None) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(db_path or DEFAULT_DB_PATH)), "persistent",
                        "research-sessions")


def _unique(path: str) -> str:
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    return f"{base}-{datetime.now().strftime('%H%M%S')}{ext}"


def call_model(system_prompt: str, user_prompt: str, model: str = MODEL, effort: str = EFFORT,
               timeout: int = MODEL_TIMEOUT_S) -> Tuple[str, Dict]:
    from core.llm_router import _run_anthropic_cli
    return _run_anthropic_cli(system_prompt, user_prompt, model, timeout, effort=effort)


def run_weekly_session(dry_run: bool = False, db_path: Optional[str] = None,
                       artifacts_dir: str = DEFAULT_ARTIFACTS_DIR, proposed_dir: str = PROPOSED_DIR,
                       post: Optional[Callable[[str], bool]] = None, model_call: Optional[Callable] = None,
                       evaluate_fn: Optional[Callable] = None, report_dir: Optional[str] = None,
                       copy_to_artifacts: bool = True, market=None) -> Dict:
    """One weekly session. dry_run: build and return the prompt only (no model call, no writes)."""
    t_start = time.time()
    state = gather_state(db_path, artifacts_dir)
    system_prompt, user_prompt = build_prompt(state)
    week = state["week"]
    if dry_run:
        return {"dry_run": True, "week": week, "system_prompt": system_prompt, "user_prompt": user_prompt,
                "chars": len(system_prompt) + len(user_prompt),
                "tokens_estimate": estimate_tokens(system_prompt + user_prompt)}

    from core.llm_pricing import compute_cost
    from core.llm_router import _normalize_usage
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rec: Dict[str, Any] = {"started_at": started, "week": week, "model_requested": MODEL, "effort": EFFORT,
                           "n_proposed": 0, "n_accepted": 0, "n_cleared": 0, "status": "ok", "error": None}
    proposals, rejected, outcomes, note = [], [], [], ""
    t0 = time.time()
    try:
        text, raw = (model_call or call_model)(system_prompt, user_prompt)
        rec["model_seconds"] = round(time.time() - t0, 1)
        resolved = (raw or {}).pop("resolved_model", None)
        u = _normalize_usage(raw or {}, "anthropic_cli")
        rec.update(model_resolved=resolved, input_tokens=u["input_tokens"], output_tokens=u["output_tokens"],
                   cache_read_tokens=u["cache_read_tokens"], cache_write_tokens=u["cache_write_tokens"],
                   cost_usd_estimate=round(compute_cost(resolved or "claude-opus-5-5", **u), 4))
        obj = parse_reply(text)
        note = str(obj.get("session_note") or "")
        rec["n_proposed"] = len(obj["proposals"])
        proposals, rejected = validate_reply(obj, state)
        rec["n_accepted"] = len(proposals)
    except subprocess.TimeoutExpired:
        rec.update(status="model_failed", error=f"model call exceeded {MODEL_TIMEOUT_S}s",
                   model_seconds=round(time.time() - t0, 1))
    except Exception as e:
        rec.update(status="model_failed" if "model_seconds" not in rec else "reply_invalid",
                   error=f"{type(e).__name__}: {e}"[:500], model_seconds=rec.get("model_seconds")
                   or round(time.time() - t0, 1))

    if proposals:
        from research.asof import MarketData
        own = market is None
        mkt = MarketData(db_path or DEFAULT_DB_PATH) if own else market
        try:
            for p in proposals:
                outcomes.append(run_proposal(p, week, rec.get("model_resolved") or MODEL, db_path=db_path,
                                             market=mkt, proposed_dir=proposed_dir, evaluate_fn=evaluate_fn))
        finally:
            if own:
                mkt.close()
    rec["n_cleared"] = sum(1 for o in outcomes if o["status"] == "shadow")
    rec["summary_line"] = summary_line(rec["n_proposed"], outcomes, rec["error"] if rec["status"] != "ok" else None)
    rec["ended_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rec["session_seconds"] = round(time.time() - t_start, 1)

    report = render_report(rec, proposals, rejected, outcomes, note)
    rdir = report_dir or sessions_dir(db_path)
    os.makedirs(rdir, exist_ok=True)
    rpath = _unique(os.path.join(rdir, f"{date.today().isoformat()}.md"))
    with open(rpath, "w") as f:
        f.write(report)
    rec["report_path"] = rpath
    if copy_to_artifacts and artifacts_dir and os.path.isdir(artifacts_dir):
        apath = _unique(os.path.join(artifacts_dir, f"research-session-{date.today().isoformat()}.md"))
        shutil.copyfile(rpath, apath)
        rec["artifact_path"] = apath
    rec["outcome_json"] = json.dumps({"outcomes": outcomes, "rejected": [
        {"id": (r["proposal"] or {}).get("id") if isinstance(r["proposal"], dict) else None, "reason": r["reason"]}
        for r in rejected]}, default=str)
    rec["session_id"] = log_session(rec, db_path)
    try:
        rec["posted"] = bool((post or _default_post)(rec["summary_line"]))
    except Exception as e:
        rec["posted"] = False
        rec["post_error"] = f"{type(e).__name__}"
    rec["outcomes"] = outcomes
    rec["rejected"] = rejected
    return rec
