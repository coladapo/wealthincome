#!/usr/bin/env python3
"""Rule-variant backtest grid (G4). Downloads 3yr daily bars once, replays the
live rule set plus variants, writes backtest_runs rows and BACKTEST-REPORT.md.

Usage: venv/bin/python scripts/run_backtest_grid.py [--years 3]
"""

import json
import os
import sys
from datetime import datetime, timedelta

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)

import yfinance as yf  # noqa: E402

from core.backtest_engine import RuleSet, prepare, run_variant  # noqa: E402

# Sector-diverse universe — the watchlist builder's static fallback list.
UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "AMD", "AVGO",
    "ORCL", "CRM", "ADBE", "NOW", "PANW", "SNPS", "CDNS", "AMAT", "MU",
    "LLY", "UNH", "JNJ", "ABBV", "TMO", "ABT", "DHR", "ISRG", "BSX",
    "JPM", "GS", "MS", "BAC", "BLK", "SPGI", "ICE", "V", "MA",
    "XOM", "CVX", "COP", "EOG", "CAT", "DE", "HON", "RTX", "GE", "UNP",
    "WMT", "COST", "HD", "MCD", "SBUX", "TGT", "CSX", "TSLA",
]

VARIANTS = [
    RuleSet(name="live_baseline"),
    RuleSet(name="no_breach_exit", breach_exit=False),
    RuleSet(name="breach_3bars", breach_bars=3),
    RuleSet(name="no_grace_window", grace_window=False),
    RuleSet(name="rsi_40_55", rsi_min=40, rsi_max=55),
    RuleSet(name="rsi_45_65", rsi_min=45, rsi_max=65),
    RuleSet(name="rsi_50_70", rsi_min=50, rsi_max=70),
    RuleSet(name="trail_tight_1.5x", trail_atr_mult=1.5),
    RuleSet(name="trail_wide_3.5x", trail_atr_mult=3.5),
    RuleSet(name="no_momentum_collapse", momentum_collapse_exit=False),
    RuleSet(name="no_breach_no_collapse", breach_exit=False, momentum_collapse_exit=False),
    RuleSet(name="tight_rsi_no_breach", rsi_min=45, rsi_max=65, breach_exit=False),
]


def download(years: int):
    end = datetime.now()
    start = end - timedelta(days=int(years * 365.25) + 80)  # +80d indicator warmup
    print(f"Downloading {len(UNIVERSE)} symbols, {start.date()} → {end.date()} ...")
    raw = yf.download(UNIVERSE, start=start.strftime("%Y-%m-%d"),
                      end=end.strftime("%Y-%m-%d"), group_by="ticker",
                      auto_adjust=True, progress=False, threads=True)
    data = {}
    for sym in UNIVERSE:
        try:
            df = raw[sym].dropna()
            if len(df) > 150:
                data[sym] = prepare(df)
        except Exception as e:
            print(f"  skip {sym}: {e}")
    print(f"Prepared {len(data)} symbols.")
    return data


def save_to_db(results):
    import sqlite3
    conn = sqlite3.connect(os.path.join(PROJECT, "data", "wealthincome.db"))
    conn.execute("PRAGMA busy_timeout = 30000")
    for r in results:
        conn.execute(
            "INSERT INTO backtest_runs (ran_at, config_json, summary_json, recommended_strategy)"
            " VALUES (?,?,?,?)",
            (datetime.now().isoformat(), json.dumps(r["rules"]),
             json.dumps({k: v for k, v in r.items() if k != "rules"}), r["variant"]),
        )
    conn.commit()
    conn.close()


def write_report(results, years):
    results = sorted(results, key=lambda r: r.get("expectancy_pct", -99), reverse=True)
    lines = [
        "# Backtest grid — rule variants vs live baseline",
        f"\n**Run:** {datetime.now().date()} · {years}yr daily bars · {len(UNIVERSE)}-symbol universe ·"
        " per-symbol replay, entries at signal close (uniform across variants — relative numbers"
        " are the signal, absolute numbers are optimistic vs real fills).\n",
        "| Variant | Trades | Win % | Expectancy %/trade | PF | Avg win | Avg loss | Hold (d) | Worst |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        if r.get("trades", 0) == 0:
            continue
        lines.append(
            f"| {r['variant']} | {r['trades']} | {r['win_rate_pct']} | {r['expectancy_pct']} "
            f"| {r['profit_factor']} | {r['avg_win_pct']} | {r['avg_loss_pct']} "
            f"| {r['avg_hold_days']} | {r['worst_trade_pct']} |"
        )
    base = next((r for r in results if r["variant"] == "live_baseline"), None)
    if base:
        lines.append("\n## Live baseline exit-reason breakdown\n")
        for reason, d in base["by_exit_reason"].items():
            wr = round(d["wins"] / d["n"] * 100) if d["n"] else 0
            lines.append(f"- **{reason}**: {d['n']} trades, {wr}% wins, {d['pnl_pct_sum']}% total")
    path = os.path.join(PROJECT, "BACKTEST-REPORT.md")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Report → {path}")


def main():
    years = 3
    if "--years" in sys.argv:
        years = int(sys.argv[sys.argv.index("--years") + 1])
    data = download(years)
    results = []
    for rules in VARIANTS:
        r = run_variant(rules, data)
        results.append(r)
        print(f"{rules.name:24s} trades={r.get('trades', 0):4d} win%={r.get('win_rate_pct', 0):5.1f} "
              f"exp%={r.get('expectancy_pct', 0):6.3f} PF={r.get('profit_factor', 0):4.2f}")
    save_to_db(results)
    write_report(results, years)


if __name__ == "__main__":
    main()
