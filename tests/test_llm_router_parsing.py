"""LLM router robustness — the 2026-06-11 'Extra data' regression."""

import json


def _first_json_doc(stdout: str):
    """Mirror of the envelope parse in core/llm_router._run_anthropic_cli."""
    envelope, _ = json.JSONDecoder().raw_decode(stdout.strip())
    return envelope


def test_clean_envelope_parses():
    out = json.dumps({"result": "{\"decisions\": []}", "usage": {"input_tokens": 5}})
    env = _first_json_doc(out)
    assert env["usage"]["input_tokens"] == 5


def test_trailing_junk_after_envelope_is_ignored():
    out = json.dumps({"result": "ok", "usage": {}}) + "\nAuto-update installed v2.1.174\n"
    env = _first_json_doc(out)
    assert env["result"] == "ok"


def test_second_json_document_is_ignored():
    out = json.dumps({"result": "ok", "usage": {}}) + "\n" + json.dumps({"event": "telemetry"})
    env = _first_json_doc(out)
    assert env["result"] == "ok"


def test_router_uses_raw_decode():
    import os
    path = os.path.join(os.path.dirname(__file__), "..", "core", "llm_router.py")
    src = open(path).read()
    assert "raw_decode" in src
    assert "envelope = json.loads(proc.stdout)" not in src
