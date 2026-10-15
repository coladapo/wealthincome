"""The research pipeline every strategy must pass (CLI + library entry points).

  venv/bin/python -m research.pipeline hypothesis register <id> --rationale "..." --rule "..." [--max-trials 10]
  venv/bin/python -m research.pipeline hypothesis list
  venv/bin/python -m research.pipeline evaluate <module:Class> --hypothesis <id> [--params JSON]
        [--start 2019-01-01] [--end YYYY-MM-DD] [--test-start YYYY-MM-DD]
  venv/bin/python -m research.pipeline baselines [--report PATH]

evaluate = budget check (refused when the hypothesis's trials are used up) ->
register the frozen (id, version) in `strategies` as a candidate -> walk-forward
under the live chassis at 1x and 2x cost vs SPY and vs the champion's real paper
equity -> selection-adjusted stats (deflated Sharpe over all trials of the
hypothesis; PBO by CSCV once >= 4 trials have daily returns) -> one trial logged
in variant_runs. Nothing here places orders, touches the live trader, or promotes
a strategy (promotion is research.strategy.set_status(..., approved_by="chris")).
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from research import experiments, selection
from research import strategy as registry
from research.asof import MarketData
from research.bars import DEFAULT_DB_PATH, connect
from research.portfolio_sim import summarize
from research.walk_forward import WalkForwardResult, format_table, walk_forward

DEFAULT_BASELINE_REPORT = ("/Users/wivak/.claude/shared-context/2026-10-14-audit-wealthincome-c7e2/"
                           "artifacts/pipeline-baselines-report.md")


def load_strategy_class(spec: str):
    if ":" not in spec:
        raise ValueError("strategy must be given as module:Class")
    mod, cls = spec.split(":", 1)
    return getattr(importlib.import_module(mod), cls)


def champion_equity() -> Optional[pd.Series]:
    from research.strategies.champion_replay import live_db_path, load_real_equity, connect_live_ro
    if not os.path.exists(live_db_path()):
        return None
    eq = load_real_equity()
    conn = connect_live_ro()
    try:
        ds = pd.read_sql_query("SELECT date, close_equity FROM daily_summaries WHERE close_equity IS NOT NULL",
                               conn, parse_dates=["date"]).set_index("date")["close_equity"]
    finally:
        conn.close()
    missing = ds[~ds.index.isin(eq.index)]
    return pd.concat([eq, missing.astype(float)]).sort_index().rename("equity")


def _selection(res: WalkForwardResult, hypothesis_id: str, conn) -> Dict:
    prior = experiments.trial_results(hypothesis_id, conn=conn)
    r = res.base.daily["ret"].iloc[1:]
    this_sr = selection.sharpe(r.values)
    srs = [p["result"].get("sharpe_daily") for p in prior] + [this_sr]
    dsr = selection.deflated_sharpe(r.values, n_trials=len(prior) + 1, trial_sharpes=srs)
    series = [pd.Series(p["result"]["daily_returns"]) for p in prior if p["result"].get("daily_returns")]
    series.append(pd.Series({d.strftime("%Y-%m-%d"): float(v) for d, v in r.items()}))
    pbo: Dict = {"pbo": None, "n_trials": len(series)}
    if len(series) >= selection.MIN_TRIALS_PBO:
        M = pd.concat(series, axis=1, join="inner").sort_index()
        pbo = selection.pbo_cscv(M.values)
        pbo["n_common_days"] = int(len(M))
    return {"dsr": dsr, "pbo": pbo}


def format_selection(sel: Dict) -> str:
    d, p = sel["dsr"], sel["pbo"]

    def f(v, nd=3):
        return "-" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:.{nd}f}"
    lines = [f"selection-adjusted: trials for this hypothesis N={d['n_trials']}; Sharpe {f(d['sr_annual'], 2)} "
             f"annualised; expected max Sharpe of N skill-less trials {f(d['sr0_annual'], 2)}; "
             f"PSR(0)={f(d['psr0'])}; deflated Sharpe ratio DSR={f(d['dsr'])} ({d['var_source']})"]
    if p.get("pbo") is None:
        lines.append(f"PBO: not computed ({p.get('reason', 'needs >= 4 trials with daily returns')}; "
                     f"have {p.get('n_trials')})")
    else:
        lines.append(f"PBO (CSCV, {p['n_blocks']} blocks, {p['n_splits']} splits, {p['n_trials']} trials, "
                     f"{p.get('n_common_days')} common days): {f(p['pbo'])}")
    return "\n".join(lines)


def evaluate(strategy, hypothesis_id: str, db_path: Optional[str] = None,
             market: Optional[MarketData] = None, start="2019-01-01", end=None, test_start=None,
             champion: Optional[pd.Series] = "auto", notes: Optional[str] = None) -> Dict:
    """One budgeted trial. Raises TrialBudgetExhausted / UnknownHypothesis before any work."""
    conn = connect(db_path)
    try:
        experiments.ensure_schema(conn)
        experiments.check_budget(hypothesis_id, conn=conn)
        reg = registry.register(strategy, hypothesis_id=hypothesis_id, notes=notes, conn=conn)
        own_market = market is None
        market = market or MarketData(db_path or DEFAULT_DB_PATH)
        champ = champion_equity() if isinstance(champion, str) and champion == "auto" else champion
        try:
            res = walk_forward(strategy, market, start=start, end=end, champion_equity=champ,
                               test_start=test_start)
        finally:
            if own_market:
                market.close()
        sel = _selection(res, hypothesis_id, conn)
        r = res.base.daily["ret"].iloc[1:]
        result = {
            "strategy_id": strategy.id, "version": strategy.version,
            "reference_only": strategy.reference_only, "chassis": res.chassis.label(),
            "windows": res.windows,
            "summary": {str(cm): run.summary for cm, run in res.runs.items()},
            "vs_spy": {str(cm): v for cm, v in res.vs_spy.items()},
            "vs_champion": {str(cm): v for cm, v in res.vs_champion.items()},
            "violations": res.base.violations,
            "sharpe_daily": selection.sharpe(r.values),
            "dsr": sel["dsr"], "pbo": sel["pbo"],
            "daily_returns": {d.strftime("%Y-%m-%d"): float(v) for d, v in r.items()},
        }
        run_id = experiments.log_trial(hypothesis_id, strategy.id,
                                       {"version": strategy.version, "params": strategy.params,
                                        "start": str(start), "end": str(end), "test_start": str(test_start)},
                                       result, n_events=res.base.summary.get("n_trades"), conn=conn)
        return {"run_id": run_id, "registry": reg, "result": res, "selection": sel}
    finally:
        conn.close()


# --------------------------------------------------------------------------- baselines

SPY_HYPOTHESIS = dict(
    id="baseline-spy-hold",
    rationale=("Reference, not an edge: what simply owning the index is worth inside the live chassis "
               "(8 positions x 8%) and with the cap lifted, so every challenger is judged against the "
               "honest floor and the cost of the caps themselves is visible."),
    rule_text=("Hold SPY. capped: SPY in all 8 slots at 8% (64% invested), live chassis (12% trailing "
               "stop on close, 5% daily loss limit, 10 bp/side). uncapped (reference only): 100% SPY, "
               "with and without the stop/loss-limit rules."),
    max_trials=10)
CHAMPION_HYPOTHESIS = dict(
    id="baseline-champion-replay",
    rationale=("The incumbent champion (live Claude picker) scored on the same simulator rules as every "
               "challenger, so champion and challengers are comparable."),
    rule_text=("Replay ai_decisions parsed buy/sell decisions (decided_at <= as-of close) as target weights, "
               "plus the chassis's own sma50/position-closed exits; next-open execution under the live "
               "chassis. Fidelity check variant: replay the logged broker holdings instead."),
    max_trials=10)


def _row(label: str, daily: pd.DataFrame, trades: Optional[pd.DataFrame], spy_close: pd.Series,
         champ_eq: Optional[pd.Series], equity_for_excess: Optional[pd.Series] = None) -> Dict:
    """Summary + excess vs SPY and vs the real champion, compared over common observed dates."""
    from research.walk_forward import excess_from_equity
    m = summarize(daily, trades)
    eq = equity_for_excess if equity_for_excess is not None else daily["equity"]
    spy = spy_close[(spy_close.index >= eq.index[0]) & (spy_close.index <= eq.index[-1])]
    return {"label": label, "summary": m, "vs_spy": excess_from_equity(eq, spy),
            "vs_champ": excess_from_equity(eq, champ_eq) if champ_eq is not None else None}


def _fmt_rows(rows: List[Dict], with_champ: bool) -> str:
    def p(v, nd=1):
        return "-" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v * 100:.{nd}f}%"

    def n(v):
        return "-" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:.2f}"
    head = ("| series | total return | CAGR | Sharpe | max drawdown | avg invested | turnover/yr | "
            "excess vs SPY (ann.) | 95% CI (day / month clusters) |")
    sep = "|---|---:|---:|---:|---:|---:|---:|---:|---|"
    if with_champ:
        head += " excess vs real champion (ann.) | 95% CI (day / month) |"
        sep += "---:|---|"
    out = [head, sep]
    for r in rows:
        m, e = r["summary"], r["vs_spy"]
        line = (f"| {r['label']} | {p(m['total_return'])} | {p(m['cagr'])} | {n(m['sharpe'])} | "
                f"{p(m['max_drawdown'])} | {p(m.get('avg_exposure'))} | "
                f"{n(m.get('annual_turnover')) + 'x' if m.get('annual_turnover') is not None else '-'} | ")
        if e.get("mean_daily") is None:
            line += "- | - |"
        else:
            line += (f"{p(e['ann_excess'])} | [{p(e['ann_ci_low'])}, {p(e['ann_ci_high'])}] / "
                     f"[{p(e['ann_ci_month_low'])}, {p(e['ann_ci_month_high'])}] |")
        if with_champ:
            c = r.get("vs_champ") or {}
            if c.get("mean_daily") is None:
                line += " - | - |"
            else:
                line += (f" {p(c['ann_excess'])} | [{p(c['ann_ci_low'])}, {p(c['ann_ci_high'])}] / "
                         f"[{p(c['ann_ci_month_low'])}, {p(c['ann_ci_month_high'])}] |")
        out.append(line)
    return "\n".join(out)


def run_baselines(report_path: str = DEFAULT_BASELINE_REPORT, db_path: Optional[str] = None,
                  start="2019-01-01") -> str:
    from research.strategies.champion_replay import ChampionReplay
    from research.strategies.spy_hold import SpyHoldCapped, SpyHoldUncapped

    db = db_path or DEFAULT_DB_PATH
    for h in (SPY_HYPOTHESIS, CHAMPION_HYPOTHESIS):
        experiments.register_hypothesis(db_path=db, **h)
    market = MarketData(db)
    champ = champion_equity()
    if champ is None or len(champ) < 2:
        raise RuntimeError("live ledger not readable: cannot build the champion series")
    live_start, live_end = champ.index[0], min(champ.index[-1], market.trading_days()[-1])
    champ = champ[champ.index <= live_end]

    spy_variants = [("SPY under the live caps (8 x 8% = 64%, stop + loss limit)", SpyHoldCapped()),
                    ("SPY 100%, cap lifted, stop + loss limit kept (reference)",
                     SpyHoldUncapped({"chassis_rules": True})),
                    ("SPY 100%, plain buy-and-hold (reference)", SpyHoldUncapped({"chassis_rules": False}))]
    champ_variants = [("Champion replay: parsed decisions (the champion's rule)", ChampionReplay()),
                      ("Champion fidelity check: replay of logged broker holdings",
                       ChampionReplay({"source": "holdings"}))]

    out = {}
    for label, s in spy_variants:
        out[label] = evaluate(s, SPY_HYPOTHESIS["id"], db_path=db, market=market, start=start,
                              end=live_end, champion=champ, notes="baseline")
    for label, s in champ_variants:
        out[label] = evaluate(s, CHAMPION_HYPOTHESIS["id"], db_path=db, market=market, start=start,
                              end=live_end, test_start=live_start, champion=champ, notes="baseline")

    # ---- full out-of-sample period (SPY variants)
    spy_close = market.frame("SPY")["close"]
    full_rows = []
    for label, _ in spy_variants:
        res = out[label]["result"]
        for cm in (1.0, 2.0):
            run = res.runs[cm]
            full_rows.append(_row(f"{label} @ {cm:g}x cost", run.daily, run.trades, spy_close, None))

    # ---- live window: everything restarted flat at the champion's first day, same dates
    from research.portfolio_sim import simulate
    live_rows = []
    cal = None
    for label, _ in spy_variants + champ_variants:
        res = out[label]["result"]
        if label in dict(champ_variants):
            d, tr = res.base.daily, res.base.trades
        else:
            # re-measure the SAME registered versions from a flat start on the live dates
            # (a re-measurement of already-logged trials, not a new trial)
            sim = simulate(dict(spy_variants)[label], live_start, live_end, market)
            d, tr = sim.daily, sim.trades
        cal = d.index if cal is None else cal
        live_rows.append(_row(label, d, tr, spy_close, champ))
    from research.strategies.champion_replay import load_real_snapshots
    snaps = load_real_snapshots()
    # summary on the trading calendar (days the equity log missed are carried forward);
    # excess returns use only the dates the account was actually observed
    real = pd.DataFrame({"equity": champ.astype(float).reindex(cal).ffill()})
    real["ret"] = real["equity"].pct_change().fillna(0.0)
    real["exposure"] = (snaps["invested"] / snaps["equity"]).reindex(cal).ffill()
    real["n_positions"] = snaps["n_positions"].reindex(cal).ffill()
    live_rows.append(_row("REAL champion: live paper account equity (truth)", real, None,
                          spy_close, champ, equity_for_excess=champ))
    spy_c = spy_close.reindex(cal)
    live_rows.append(_row("SPY buy-and-hold benchmark (cost-free)",
                          pd.DataFrame({"equity": spy_c / spy_c.iloc[0] * 100_000,
                                        "ret": spy_c.pct_change().fillna(0.0)}),
                          None, spy_close, champ))
    n_missing = int((~cal.isin(champ.index)).sum())

    replay = out[champ_variants[0][0]]["result"]
    holdings = out[champ_variants[1][0]]["result"]
    strat_dec = champ_variants[0][1]
    text = _baseline_report(full_rows, live_rows, out, spy_variants, champ_variants, live_start,
                            live_end, replay, holdings, strat_dec, market, champ, len(cal), n_missing)
    if report_path:
        os.makedirs(os.path.dirname(report_path), exist_ok=True)
        with open(report_path, "w") as f:
            f.write(text)
    market.close()
    return text


def _baseline_report(full_rows, live_rows, out, spy_variants, champ_variants, live_start, live_end,
                     replay, holdings, strat_dec, market, champ, n_sessions, n_missing) -> str:
    def pct(v):
        return f"{v * 100:.1f}%"
    cap = full_rows[0]["summary"]
    lift = full_rows[2]["summary"]
    plain = full_rows[4]["summary"]
    rl = {r["label"]: r for r in live_rows}
    real = rl["REAL champion: live paper account equity (truth)"]["summary"]
    rep = rl[champ_variants[0][0]]["summary"]
    hold = rl[champ_variants[1][0]]["summary"]
    lines = [
        "# Pipeline baselines: SPY under the caps vs uncapped vs champion replay vs real champion",
        "",
        f"Generated by `venv/bin/python -m research.pipeline baselines` on {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}.",
        "Simulator: daily, targets computed at the close from completed sessions only, executed at the NEXT "
        "open; live chassis = max 8 positions, max 8% per position, 12% trailing stop on close, 5% daily "
        "loss limit (no new buys), long only, 10 bp/side (2x stress = 20 bp/side). SPY benchmark = "
        "close-to-close, fully invested, cost-free. CIs = bootstrap 95% of the mean daily excess return "
        "(annualised x252), clustered by trading day and, more conservatively, by calendar month.",
        "",
        f"## 1. Full out-of-sample walk-forward ({full_rows[0]['summary']['start']} .. "
        f"{full_rows[0]['summary']['end']}, 24m train / 6m test, {len(out[spy_variants[0][0]]['result'].windows)} windows)",
        "",
        _fmt_rows(full_rows, with_champ=False),
        "",
        f"## 2. Live window, same dates for all ({live_start.date()} .. {live_end.date()}, "
        f"{n_sessions} sessions; every simulated series starts flat with $100k on the first day, as the "
        "live account did)",
        "",
        _fmt_rows(live_rows, with_champ=True),
        "",
        (f"The live equity log has no snapshot on {n_missing} of these {n_sessions} sessions (trader "
         "down); comparisons with the real account use returns between the dates it WAS observed, and its "
         "summary row carries the last value forward over the gaps. Six months is far too short for any "
         "of these differences to be distinguishable from zero -- see the CI widths."),
        "",
        "## What the caps alone cost",
        "",
        (f"Over the {cap['days']} out-of-sample sessions ({cap['start']} .. {cap['end']}) plain SPY "
         f"buy-and-hold returned {pct(plain['total_return'])} ({pct(plain['cagr'])}/yr, max drawdown "
         f"{pct(plain['max_drawdown'])}). Keeping 100% in SPY but adding the chassis's 12% trailing stop "
         f"and 5% loss limit returned {pct(lift['total_return'])} ({pct(lift['cagr'])}/yr): the stop rules "
         f"{'cost' if plain['cagr'] > lift['cagr'] else 'added'} {pct(abs(plain['cagr'] - lift['cagr']))} "
         f"a year -- essentially nothing. The 8% per-position cap is what matters: SPY in all eight 8% "
         f"slots, the most the live chassis can ever buy, returned {pct(cap['total_return'])} "
         f"({pct(cap['cagr'])}/yr; average {pct(cap['avg_exposure'])} invested because winners drift above "
         f"8% between stops; max drawdown {pct(cap['max_drawdown'])}). So the caps alone, with no stock "
         f"picking at all, give up {pct(plain['cagr'] - cap['cagr'])} a year against plain SPY "
         f"({pct(plain['total_return'] - cap['total_return'])} of cumulative return over the period), in "
         f"exchange for a smaller drawdown. In the excess-return terms used for promotion, SPY-under-caps "
         f"trails SPY by {pct(-full_rows[0]['vs_spy']['ann_excess'])}/yr and the 95% CI excludes zero under "
         f"both clusterings. Any challenger starts in that hole: holding ~{pct(cap['avg_exposure'])} of the "
         f"account, its picks must beat the index by roughly "
         f"{pct(plain['cagr'] / max(cap['avg_exposure'], 1e-9) - plain['cagr'])} a year on the invested "
         f"part just to tie SPY."),
        "",
        "## Champion: replay vs the real record (fidelity)",
        "",
        (f"Real paper account over the live window: {pct(real['total_return'])} (max drawdown "
         f"{pct(real['max_drawdown'])}; avg invested {pct(real.get('avg_exposure'))}, avg "
         f"{real.get('avg_positions', float('nan')):.1f} positions). The decisions replay -- the champion's "
         f"rule scored on the challengers' rules -- returned {pct(rep['total_return'])} (avg invested "
         f"{pct(rep['avg_exposure'])}, avg {rep.get('avg_positions', float('nan')):.1f} positions); replaying "
         f"the logged broker holdings returned {pct(hold['total_return'])} (avg invested "
         f"{pct(hold['avg_exposure'])}, avg {hold.get('avg_positions', float('nan')):.1f} positions)."),
        "",
        ("Reading: the holdings replay matches the real account's position count but runs at lower "
         "exposure (the 8% clip); the decisions replay holds far MORE than the real account did, because "
         "most of the picker's parsed buys never became live positions. The decisions replay is therefore "
         "the champion's stated rule run cleanly under the chassis -- it is NOT a reconstruction of the real "
         "record, and its return matching the real return is not evidence of fidelity. Promotion decisions "
         "should compare challengers with BOTH the real equity (truth) and the decisions replay (same rules)."),
        "",
        "Why they differ (none of this is verified trade-by-trade):",
        "- Timing: live orders filled intraday at decision time; the replay applies a decision at the close "
        "of its day and fills at the next open (one session late, up to ~1.5 sessions for overnight cycles).",
        "- Rejections: the replay re-submits every parsed buy, including ones the live validator, calendar "
        "block, PDT guard or broker rejected (77 live buy orders were cancelled); only a full 8-name book, "
        "the simulator's caps and the loss limit reject them.",
        "- Sizing: the early live book held positions far above 8% (e.g. GS ~44% of equity on day one); the "
        "simulator clips to 8%.",
        "- Exits: the live trailing-stop orders and partial fills are not replayed; the simulator applies "
        "its own 12% stop on close. sma50-breach / position-closed exits from position_lifecycle are applied.",
        "- Ledger gaps (issue 6: realized P&L ~$5.5k gap) mean the real equity is the broker's number; the "
        "replay cannot reproduce cash events, rounding or intraday prices.",
    ]
    rv = replay.base.violations
    hv = holdings.base.violations
    lines.append(f"- Chassis interventions in the decisions replay: "
                 + (", ".join(f"{k}={v}" for k, v in sorted(rv.items())) or "none")
                 + "; holdings replay: " + (", ".join(f"{k}={v}" for k, v in sorted(hv.items())) or "none") + ".")
    lines.append(f"- Buys of a 9th name dropped by the decisions replay (full book): "
                 f"{getattr(strat_dec, 'rejected_full_book', 0)} (last pass).")
    if getattr(strat_dec, "missing_symbols", None):
        lines.append(f"- Decided symbols with no bars in the research store (skipped): "
                     f"{', '.join(sorted(strat_dec.missing_symbols))}.")
    lines += ["", "## Selection-adjusted statistics (as logged)", ""]
    for label, o in out.items():
        lines.append(f"**{label}** (run {o['run_id']}, {o['registry']['id']} v{o['registry']['version'][:12]})")
        lines.append("")
        lines.append(format_selection(o["selection"]))
        lines.append("")
    lines += ["## Walk-forward tables (CLI format)", ""]
    for label, o in out.items():
        lines += [f"### {label}", "", "```", format_table(o["result"]), "```", ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------- CLI

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m research.pipeline")
    ap.add_argument("--db", default=None, help="research store (default data/research.db)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ev = sub.add_parser("evaluate", help="one budgeted walk-forward trial")
    ev.add_argument("strategy", help="module:Class, e.g. research.strategies.spy_hold:SpyHoldCapped")
    ev.add_argument("--hypothesis", required=True)
    ev.add_argument("--params", default=None, help="JSON object of strategy params")
    ev.add_argument("--start", default="2019-01-01")
    ev.add_argument("--end", default=None)
    ev.add_argument("--test-start", default=None)
    hy = sub.add_parser("hypothesis", help="pre-register / list hypotheses")
    hs = hy.add_subparsers(dest="hcmd", required=True)
    hr = hs.add_parser("register")
    hr.add_argument("id")
    hr.add_argument("--rationale", required=True)
    hr.add_argument("--rule", required=True)
    hr.add_argument("--max-trials", type=int, default=10)
    hs.add_parser("list")
    bl = sub.add_parser("baselines", help="SPY capped/uncapped + champion replay report")
    bl.add_argument("--report", default=DEFAULT_BASELINE_REPORT)
    args = ap.parse_args(argv)

    if args.cmd == "hypothesis":
        if args.hcmd == "register":
            h = experiments.register_hypothesis(args.id, args.rationale, args.rule, args.max_trials,
                                                db_path=args.db)
            print(json.dumps(h, indent=2))
        else:
            for h in experiments.list_hypotheses(db_path=args.db):
                print(f"{h['id']}: {h['trials_used']}/{h['max_trials']} trials -- {h['rule_text'][:90]}")
        return 0
    if args.cmd == "baselines":
        print(run_baselines(args.report, db_path=args.db))
        print(f"\nwrote {args.report}")
        return 0

    params = json.loads(args.params) if args.params else None
    strat = load_strategy_class(args.strategy)(params)
    try:
        out = evaluate(strat, args.hypothesis, db_path=args.db, start=args.start, end=args.end,
                       test_start=args.test_start)
    except experiments.TrialBudgetExhausted as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    except experiments.UnknownHypothesis:
        print(f"REFUSED: hypothesis {args.hypothesis!r} is not pre-registered; run "
              "`python -m research.pipeline hypothesis register` first", file=sys.stderr)
        return 2
    print(strat.describe())
    print(format_table(out["result"]))
    print()
    print(format_selection(out["selection"]))
    print(f"\nlogged trial run_id={out['run_id']} (hypothesis {args.hypothesis}: "
          f"{experiments.trials_used(args.hypothesis, db_path=args.db)} trials used)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
