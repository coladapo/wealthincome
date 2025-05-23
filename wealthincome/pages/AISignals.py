### 🏁 Signals:
- 🟢 **BUY** if Score ≥ 60 **and** RSI(14) is between 30–70  
- 🟡 **WATCH** if Score ≥ 45  
- 🔴 **AVOID** if Score < 45  

### 🚦 Additional Filters:
1. **50‑Day MA Trend**  
   Only “🏆 Top Pick” if today’s price > 50‑day moving average.  
2. **RSI Confirmation**  
   A BUY signal requires 30 < RSI(14) < 70 to avoid extremes.  
3. **Short‑Float Cap**  
   Short interest contribution is capped at 30 % for score stability.
    """)

# ─── Signal Filter ─────────────────────────────────────────────────────────
selected_signal = st.selectbox(
    "📍 Filter by Signal",
    options=["All", "BUY", "WATCH", "AVOID"]
)

# ─── Fetch & Compute ───────────────────────────────────────────────────────
data = []
for ticker in tickers:
    try:
        tk   = yf.Ticker(ticker)
        info = tk.info
        hist = tk.history(period="3mo")

        # core metrics
        price            = info.get("regularMarketPrice", 0)
        change           = info.get("regularMarketChangePercent", 0)
        rvol             = info.get("regularMarketVolume", 1) / info.get("averageVolume", 1)
        short_pct        = info.get("shortPercentOfFloat", 0) * 100
        short_pct_capped = min(short_pct, 30)

        # RSI(14)
        if hist["Close"].shape[0] >= 14:
            rsi = RSIIndicator(hist["Close"], window=14).rsi().iloc[-1]
        else:
            rsi = 50  # neutral fallback

        # AI Score
        ai_score = round(
            (change * 2) +
            (rvol * 10) +
            (short_pct_capped * 2),
            2
        )

        # 50‑day MA
        ma50 = hist["Close"].rolling(50).mean().iloc[-1] if hist.shape[0] >= 50 else price

        # basic tags
        tags = []
        if change >= 2 and rvol >= 1.5:
            tags.append("🔁 Momentum")
        if hist["High"].rolling(20).max().iloc[-1] < price:
            tags.append("📈 Breakout")

        data.append({
            "Ticker": ticker,
            "Price": price,
            "% Change": change,
            "RVOL": rvol,
            "Short %": short_pct,
            "AI Score": ai_score,
            "RSI": rsi,
            "MA50": ma50,
            "Signal": None,
            "Tags": ", ".join(tags)
        })

    except Exception:
        # skip tickers we can’t fetch
        continue

df = pd.DataFrame(data)

if not df.empty:
    # ── 1) Assign Signals ────────────────────────────────────────────────────
    def compute_signal(row):
        if row["AI Score"] >= 60 and 30 < row["RSI"] < 70:
            return "BUY"
        elif row["AI Score"] >= 45:
            return "WATCH"
        else:
            return "AVOID"

    df["Signal"] = df.apply(compute_signal, axis=1)

    # ── 2) Tag Top Pick if > MA50 ────────────────────────────────────────────
    top_idx = df["AI Score"].idxmax()
    if pd.notna(top_idx) and df.at[top_idx, "Price"] > df.at[top_idx, "MA50"]:
        base = df.at[top_idx, "Tags"]
        df.at[top_idx, "Tags"] = "🏆 Top Pick" + (", " + base if base else "")

    # ── 3) Filter by chosen signal ──────────────────────────────────────────
    if selected_signal != "All":
        df = df[df["Signal"] == selected_signal]

    # ── 4) Sort: BUY first, then WATCH, then AVOID; within each by AI Score ─
    order_map = {"BUY": 0, "WATCH": 1, "AVOID": 2}
    df = (
        df
        .sort_values(
            by=["Signal","AI Score"],
            key=lambda col: col.map(order_map) if col.name=="Signal" else col,
            ascending=[True, False]
        )
        .reset_index(drop=True)
    )

    # ── 5) Style & Render ───────────────────────────────────────────────────
    def highlight_signal(v):
        if v=="BUY":   return "background-color:#16a34a;color:white;"
        if v=="WATCH": return "background-color:#facc15;color:black;"
        if v=="AVOID": return "background-color:#dc2626;color:white;"
        return ""

    styled = (
        df[["Ticker","Signal","Tags","Price","% Change","RVOL","Short %","AI Score","RSI"]]
        .style
        .applymap(highlight_signal, subset=["Signal"])
        .format({
            "Price":     "${:.2f}",
            "% Change":  "{:.2f}%",
            "RVOL":      "{:.3f}",
            "Short %":   "{:.1f}%",
            "AI Score":  "{:.2f}",
            "RSI":       "{:.1f}"
        })
    )

    st.dataframe(styled, use_container_width=True)

else:
    st.warning("No data available. Check your tickers?")
