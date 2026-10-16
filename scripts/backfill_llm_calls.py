#!/usr/bin/env python3
"""Seed the llm_calls ledger from cycles + research_sessions history. Idempotent; safe to rerun."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.db import backfill_llm_calls, init_db  # noqa: E402

if __name__ == "__main__":
    init_db()
    print(backfill_llm_calls())
