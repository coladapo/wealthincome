"""
AI Signals Page — wired to real backend API data.
Shows Claude's actual decisions, trade outcomes, and win rate from the DB.
"""

import json
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import requests

API_BASE = "http://localhost:8000"


def _api(path, params=None):
    try:
        r = requests.get(f"{API_BASE}{path}", params=params, timeout=8)
        r.raise_for_status()
        return r.json(), None
    except requests.exceptions.ConnectionError:
        return None, "Backend API not running"
    except Exception as e:
        return None, str(e)


def render_ai_signals():
    st.title("AI Signals")

    trades_data, err = _api("/trades", {"limit": 200})
    decisions_data, _ = _api("/decisions", {"limit": 100})
    perf_data, _ = _api("/performance")

    if err:
        st.error(err)
        return

    trades = (trades_data or {}).get("trades", [])
    decision_records = (decisions_data or {}).get("decisions", [])
    perf = perf_data or {}

    # Flatten parsed decisions from each cycle record
    flat_decisions = []
    for rec in decision_records:
        raw = rec.get("parsed_decisions_json")
        decided_at = rec.get("decided_at", "")
        cycle_id = rec.get("cycle_id")
        if raw:
            try:
                parsed = json.loads(raw)
                for d in parsed:
                    d["decided_at"] = decided_at
                    d["cycle_id"] = cycle_id
                    d["cost_usd"] = rec.get("cost_usd")
                    flat_decisions.append(d)
            except Exception:
                pass

    # Compute real metrics
    closed = [t for t in trades if t.get("order_status") in ("filled", "closed") and t.get("pnl") is not None]
    wins = [t for t in closed if float(t.get("pnl", 0)) > 0]
    losses = [t for t in closed if float(t.get("pnl", 0)) <= 0]
    win_rate = len(wins) / len(closed) if closed else None

    buy_decisions = [d for d in flat_decisions if d.get("action", "").lower() == "buy"]
    avg_confidence = (
        sum(float(d.get("confidence", 0)) for d in buy_decisions) / len(buy_decisions)
        if buy_decisions else None
    )

    total_pnl = sum(float(t.get("pnl", 0)) for t in closed)
    total_cost = sum(float(r.get("cost_usd", 0) or 0) for r in decision_records)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Cycle Decisions", len(decision_records))
    c2.metric("Total Signals", len(flat_decisions))
    c3.metric(
        "Avg Confidence",
        f"{avg_confidence:.0%}" if avg_confidence is not None else "—",
    )
    c4.metric(
        "Win Rate",
        f"{win_rate:.0%}" if win_rate is not None else "—",
        f"{len(wins)}W / {len(losses)}L" if closed else "No closed trades",
    )
    c5.metric(
        "Claude API Cost",
        f"${total_cost:.4f}" if total_cost > 0 else "—",
    )

    st.markdown("---")

    # ── Recent Claude signals ────────────────────────────────────────────────
    st.subheader("Recent Claude Signals")

    if flat_decisions:
        rows = []
        for d in flat_decisions[:50]:
            rows.append({
                "Time": str(d.get("decided_at", ""))[:19],
                "Symbol": d.get("symbol", ""),
                "Action": str(d.get("action", "")).upper(),
                "Confidence": f"{float(d.get('confidence', 0)):.0%}",
                "Position Size": f"{float(d.get('position_size_pct', 0)):.0%}",
                "Reasoning": str(d.get("reasoning") or "")[:100],
            })
        df = pd.DataFrame(rows)

        def color_action(val):
            if val == "BUY":
                return "color: #00cc66; font-weight: bold"
            elif val == "SELL":
                return "color: #ff4444; font-weight: bold"
            return "color: #aaaaaa"

        st.dataframe(
            df.style.map(color_action, subset=["Action"]),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No signals recorded yet — daemon needs to run at least one cycle")

    st.markdown("---")

    # ── Confidence distribution ──────────────────────────────────────────────
    if flat_decisions:
        st.subheader("Confidence Distribution")
        confs = [float(d.get("confidence", 0)) for d in flat_decisions if d.get("confidence")]
        fig = go.Figure(go.Histogram(
            x=confs,
            nbinsx=20,
            marker_color="rgba(0, 180, 255, 0.7)",
        ))
        fig.update_layout(
            xaxis_title="Confidence",
            yaxis_title="Count",
            template="plotly_dark",
            height=250,
            margin=dict(t=20),
        )
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("---")

    # ── Trade outcomes ───────────────────────────────────────────────────────
    st.subheader("Trade Outcomes")
    if closed:
        col1, col2 = st.columns(2)

        with col1:
            fig = go.Figure(data=[
                go.Bar(name="Wins", x=["Trades"], y=[len(wins)], marker_color="green"),
                go.Bar(name="Losses", x=["Trades"], y=[len(losses)], marker_color="red"),
            ])
            fig.update_layout(
                title=f"Win/Loss ({win_rate:.0%} win rate)" if win_rate is not None else "Win/Loss",
                template="plotly_dark",
                height=280,
                margin=dict(t=40),
            )
            st.plotly_chart(fig, use_container_width=True)

        with col2:
            avg_win = sum(float(t["pnl"]) for t in wins) / len(wins) if wins else 0
            avg_loss = sum(float(t["pnl"]) for t in losses) / len(losses) if losses else 0
            profit_factor = abs(avg_win / avg_loss) if avg_loss != 0 else None

            st.metric("Avg Win", f"${avg_win:+.2f}")
            st.metric("Avg Loss", f"${avg_loss:+.2f}")
            st.metric("Profit Factor", f"{profit_factor:.2f}" if profit_factor else "—")
            st.metric("Total Closed Trades", len(closed))
    else:
        st.info("No closed trades yet — P&L will appear here once trades are completed")

    st.markdown("---")

    # ── Risk-adjusted performance ────────────────────────────────────────────
    st.subheader("Risk-Adjusted Performance")
    if perf and "note" not in perf:
        p1, p2, p3, p4 = st.columns(4)
        p1.metric("Sharpe Ratio", f"{float(perf['sharpe_ratio']):.2f}" if perf.get("sharpe_ratio") is not None else "—")
        p2.metric("Max Drawdown", f"{float(perf['max_drawdown_pct']):.1f}%" if perf.get("max_drawdown_pct") is not None else "—")
        p3.metric("Total Return", f"{float(perf['total_return_pct']):+.2f}%" if perf.get("total_return_pct") is not None else "—")
        p4.metric("Volatility", f"{float(perf['volatility_pct']):.1f}%" if perf.get("volatility_pct") is not None else "—")
    else:
        note = perf.get("note", "Performance metrics will appear after the first completed trades")
        st.info(note)
