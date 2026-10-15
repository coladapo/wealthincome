"""Digest shadow line: formatting, empty store, and the digest's try/except fallback. Offline."""

import importlib.util
import os
import re
import sqlite3
import sys

import numpy as np
import pandas as pd
import pytest

from research import bars as rb
from research import shadow, shadow_report
from research import strategy as reg
from research.strategies.spy_hold import SpyHoldCapped
from research.strategy import Strategy

DATES = pd.bdate_range("2026-09-01", periods=13)
DIGEST = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "daily_digest.py")


class Ch(Strategy):
    """Test challenger."""
    id = "report_challenger"
    default_params = {"k": 1}

    def target_weights(self, as_of, data):
        return {}


def _write(conn, sid, ver, eq):
    conn.executemany("INSERT INTO shadow_equity VALUES (?,?,?,?,?,?,?,?)",
                     [(sid, ver, d.strftime("%Y-%m-%d"), float(v), 0.0, 0.5, 0.0, 0.0) for d, v in eq.items()])
    conn.commit()


@pytest.fixture()
def db(tmp_path):
    path = str(tmp_path / "research.db")
    c = rb.connect(path)
    shadow.ensure_schema(c)
    champ = pd.Series(100_000 * 1.001 ** np.arange(len(DATES)), index=DATES)
    c.executemany("INSERT INTO benchmark_equity VALUES ('champion',?,?)",
                  [(d.strftime("%Y-%m-%d"), float(v)) for d, v in champ.items()])
    for k, edge in ((1, 0.0012), (2, 0.0016)):                     # two challengers, best = +0.06%/day
        s = Ch({"k": k})
        reg.register(s, conn=c)
        reg.set_status(s.id, s.version, "shadow", conn=c)
        _write(c, s.id, s.version, pd.Series(100_000 * (1.001 + edge - 0.001) ** np.arange(len(DATES)), index=DATES))
    cap = SpyHoldCapped()
    reg.register(cap, conn=c)
    _write(c, cap.id, cap.version, pd.Series(100_000 * 0.9999 ** np.arange(len(DATES)), index=DATES))
    c.close()
    return path


def test_line_format(db):
    line = shadow_report.format_shadow_line(db_path=db)
    assert re.fullmatch(r"Shadow: 2 challengers · best \+1\.3%/mo vs champion \(n=12d\) · SPY capped −2\.3%/mo",
                        line), line


def test_not_started_when_no_tables(tmp_path):
    path = str(tmp_path / "empty.db")
    sqlite3.connect(path).close()
    assert shadow_report.format_shadow_line(db_path=path) == "Shadow: not started"


def test_one_challenger_without_history(tmp_path):
    path = str(tmp_path / "r.db")
    c = rb.connect(path)
    shadow.ensure_schema(c)
    s = Ch()
    reg.register(s, conn=c)
    reg.set_status(s.id, s.version, "shadow", conn=c)
    c.close()
    assert shadow_report.format_shadow_line(db_path=path) == "Shadow: 1 challenger · best n/a vs champion (n=0d)"


def test_never_raises(db, monkeypatch):
    def boom(conn):
        raise RuntimeError("broken")
    monkeypatch.setattr(shadow_report, "shadow_summary", boom)
    assert shadow_report.format_shadow_line(db_path=db) is None
    assert shadow_report.format_shadow_line(db_path="/nonexistent/dir/research.db") is None


def _digest(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("digest_under_test", DIGEST)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    live = str(tmp_path / "live.db")
    c = sqlite3.connect(live)
    c.executescript("CREATE TABLE trades(executed_at TEXT); CREATE TABLE errors(occurred_at TEXT);")
    c.close()
    import backend.db
    import core.scorecard
    monkeypatch.setattr(backend.db, "DB_PATH", live)
    monkeypatch.setattr(core.scorecard, "compute_scorecard", lambda: {})
    monkeypatch.setattr(core.scorecard, "format_digest", lambda card, **kw: "DIGEST BODY")
    monkeypatch.setattr(mod, "_brain_and_gate", lambda: (None, None))
    posted = []
    monkeypatch.setattr(mod, "_post_via_webhook", lambda text: posted.append(text) or True)
    monkeypatch.setattr(mod, "_heartbeat", lambda outcome: None)
    return mod, posted


def test_digest_appends_shadow_line(monkeypatch, tmp_path):
    mod, posted = _digest(monkeypatch, tmp_path)
    monkeypatch.setattr(shadow_report, "format_shadow_line", lambda: "Shadow: 0 challengers")
    assert mod.main() == 0
    assert posted == ["DIGEST BODY\nShadow: 0 challengers"]


def test_digest_still_posts_when_research_import_fails(monkeypatch, tmp_path):
    mod, posted = _digest(monkeypatch, tmp_path)
    monkeypatch.setitem(sys.modules, "research.shadow_report", None)    # import raises ImportError
    assert mod._shadow_line() is None
    assert mod.main() == 0
    assert posted == ["DIGEST BODY"]
