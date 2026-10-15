"""Champion replay: decisions -> target book with host-time (PT) cutoffs, read-only. Offline."""

import json
import sqlite3
from datetime import date

import pandas as pd
import pytest

from research.asof import AsOfData
from research.strategies.champion_replay import ChampionReplay, connect_live_ro, host_to_et
from research_synth import flat_market

DATES = pd.bdate_range("2024-03-04", "2024-03-15")


@pytest.fixture()
def live_db(tmp_path):
    path = str(tmp_path / "wealthincome.db")
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE ai_decisions (id INTEGER PRIMARY KEY, decided_at TEXT, parsed_decisions_json TEXT,
            positions_snapshot_json TEXT, account_snapshot_json TEXT);
        CREATE TABLE position_lifecycle (id INTEGER PRIMARY KEY, symbol TEXT, closed_at TEXT,
            close_reason TEXT);""")
    rows = [
        ("2024-03-05T12:00:00", [{"symbol": "AAA", "action": "buy", "position_size_pct": 0.05}]),
        ("2024-03-05T13:30:00", [{"symbol": "BBB", "action": "buy", "position_size_pct": 0.06}]),
        ("2024-03-06T09:00:00", [{"symbol": "AAA", "action": "sell", "reduce_pct": 0.5},
                                 {"symbol": "BBB", "action": "hold"}]),
    ]
    for i, (t, d) in enumerate(rows, 1):
        c.execute("INSERT INTO ai_decisions VALUES (?,?,?,?,?)",
                  (i, t, json.dumps(d), json.dumps({"positions": []}),
                   json.dumps({"portfolio_value": 100000})))
    c.execute("INSERT INTO position_lifecycle VALUES (1, 'BBB', '2024-03-07T08:00:00', 'sma50_breach')")
    c.commit()
    c.close()
    return path


def test_host_time_is_pacific():
    assert host_to_et("2024-03-05T13:30:00").hour == 16          # 13:30 PT = 16:30 ET


def test_replay_book_respects_close_cutoff(live_db):
    m = flat_market(DATES)
    s = ChampionReplay(db_path=live_db)
    book = {d: s.target_weights(d, AsOfData(d, m)) for d in
            (date(2024, 3, 4), date(2024, 3, 5), date(2024, 3, 6), date(2024, 3, 7))}
    assert book[date(2024, 3, 4)] == {}
    assert book[date(2024, 3, 5)] == {"AAA": 0.05}                 # BBB decided after 16:00 ET
    assert book[date(2024, 3, 6)] == {"AAA": 0.025, "BBB": 0.06}
    assert book[date(2024, 3, 7)] == {"AAA": 0.025}                # chassis sma50 exit applied
    # asking for an earlier date again rebuilds from scratch (no state leak)
    assert s.target_weights(date(2024, 3, 5), AsOfData(date(2024, 3, 5), m)) == {"AAA": 0.05}


def test_live_ledger_is_opened_read_only(live_db):
    c = connect_live_ro(live_db)
    with pytest.raises(sqlite3.OperationalError):
        c.execute("DELETE FROM ai_decisions")
    c.close()
