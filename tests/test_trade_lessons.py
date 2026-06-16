"""Self-critique lessons scaffold — bright-line + overfitting guards."""

import sqlite3
import core.trade_lessons as tl


def _seed_db(path):
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE position_lifecycle (
        id INTEGER PRIMARY KEY, symbol TEXT, status TEXT, regime_at_entry TEXT,
        realized_pnl_pct REAL, entry_price REAL, exit_price REAL)""")
    conn.execute("""CREATE TABLE trade_lessons (
        id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, position_id INTEGER,
        symbol TEXT, regime TEXT, exit_reason TEXT, realized_pnl_pct REAL,
        setup TEXT, expected TEXT, outcome TEXT, lesson TEXT,
        sample_n INTEGER DEFAULT 1, confidence TEXT DEFAULT 'low')""")
    conn.commit(); conn.close()


def test_loss_lesson_is_a_caution(tmp_path, monkeypatch):
    p = str(tmp_path / "t.db"); _seed_db(p)
    monkeypatch.setattr(tl, "DB_PATH", p)
    tl.record_lesson({"id": 1, "symbol": "XYZ", "regime_at_entry": "BULL",
                      "close_reason": "sma50_breach", "realized_pnl_pct": -4.0})
    digest = tl.build_lessons_digest()
    assert "CAUTIONS" in digest and "XYZ" in digest
    assert "extra caution" in digest.lower()


def test_digest_header_enforces_bright_line(tmp_path, monkeypatch):
    p = str(tmp_path / "t.db"); _seed_db(p)
    monkeypatch.setattr(tl, "DB_PATH", p)
    tl.record_lesson({"id": 1, "symbol": "ABC", "regime_at_entry": "BULL",
                      "close_reason": "ai_sell", "realized_pnl_pct": 5.0})
    digest = tl.build_lessons_digest()
    # The contract: lessons may only reduce conviction, never raise it.
    assert "more cautious" in digest.lower()
    assert "not justify a larger position" in digest.lower() or "may not" in digest.lower()


def test_thin_sample_is_low_confidence(tmp_path, monkeypatch):
    p = str(tmp_path / "t.db"); _seed_db(p)
    monkeypatch.setattr(tl, "DB_PATH", p)
    # One closed trade of this shape → sample_n=1 → must be low-confidence.
    conn = sqlite3.connect(p)
    conn.execute("INSERT INTO position_lifecycle (id,symbol,status,regime_at_entry)"
                 " VALUES (1,'ONE','closed','BULL')")
    conn.commit(); conn.close()
    tl.record_lesson({"id": 1, "symbol": "ONE", "regime_at_entry": "BULL",
                      "close_reason": "ai_sell", "realized_pnl_pct": 6.0})
    conn = sqlite3.connect(p); conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT confidence, lesson FROM trade_lessons").fetchone()
    conn.close()
    assert row["confidence"] == "low"
    assert "thin" in row["lesson"].lower() or "hypothesis" in row["lesson"].lower()


def test_empty_store_returns_empty_digest(tmp_path, monkeypatch):
    p = str(tmp_path / "t.db"); _seed_db(p)
    monkeypatch.setattr(tl, "DB_PATH", p)
    assert tl.build_lessons_digest() == ""
