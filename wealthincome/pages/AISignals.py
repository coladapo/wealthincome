st.title("🧠 AI Stock Screener")

with st.expander("🧠 How This Screener Works", expanded=False):
    st.markdown("""
    This tool scans the market for **momentum setups** using the logic below:

    ### 📊 Key Metrics:
    - **% Change** — price movement today. Positive = bullish.
    - **RVOL (Relative Volume)** — volume compared to average. >1 = unusual activity.
    - **Short % Interest** — how many people are betting against the stock.

    ### ✏️ AI Signal Score Formula:
    ```
    AI Score = (% Change × 2) + (RVOL × 10) + (Short % × 2)
    ```

    This composite score helps rank stocks by **momentum + volume + sentiment**.

    ### 🏁 Signals:
    - 🟢 **BUY** if AI Score ≥ 60  
    - 🟡 **WATCH** if AI Score ≥ 45  
    - 🔴 **AVOID** if AI Score < 45

    Use the **signal dropdown below** to focus on actionable opportunities.

    🧠 *Tip: Use this screener before market open or during power hour (last hour of trading).*
    """)
