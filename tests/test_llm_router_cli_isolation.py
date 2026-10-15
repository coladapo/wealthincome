"""The brain call must run isolated from the operator's interactive Claude
setup (no hooks, plugins, MCP, CLAUDE.md) and from a pinned binary.
Regression guard for the Sep–Oct 2026 timeout outage."""
import json
import subprocess

import core.llm_router as lr


class _Proc:
    returncode = 0
    stderr = ""
    stdout = json.dumps({"result": '{"decisions": []}', "usage": {"input_tokens": 1, "output_tokens": 1}})


def test_cli_argv_is_isolated(monkeypatch):
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"] = argv
        seen["kw"] = kw
        return _Proc()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setenv("WEALTHINCOME_CLAUDE_BIN", "/pinned/claude")
    text, usage = lr._run_anthropic_cli("sys", "user", "claude-sonnet-4-6", timeout=10)

    argv = seen["argv"]
    assert argv[0] == "/pinned/claude"
    assert argv[1] == "-p"
    assert "--setting-sources" in argv and argv[argv.index("--setting-sources") + 1] == ""
    for flag in ("--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"):
        assert flag in argv
    assert "--bare" not in argv
    assert seen["kw"]["timeout"] == 10
    assert "wi-brain-" in seen["kw"]["cwd"]
    assert seen["kw"]["env"]["DISABLE_AUTOUPDATER"] == "1"
    assert text == '{"decisions": []}'
    assert usage["input_tokens"] == 1


def test_pinned_binary_default(monkeypatch, tmp_path):
    monkeypatch.delenv("WEALTHINCOME_CLAUDE_BIN", raising=False)
    monkeypatch.setattr(lr, "_PROJECT_DIR", str(tmp_path))
    assert lr._claude_binary() == "claude"
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "claude").write_text("")
    assert lr._claude_binary() == str(tmp_path / "bin" / "claude")


def test_effort_flag_and_resolved_model(monkeypatch):
    import backend.db as db
    seen = {}

    class _P:
        returncode = 0
        stderr = ""
        stdout = json.dumps({"result": '{"decisions": []}', "usage": {"input_tokens": 1},
                             "modelUsage": {"claude-sonnet-5-5": {"inputTokens": 1}}})

    def fake_run(argv, **kw):
        seen["argv"] = argv
        return _P()

    monkeypatch.setattr(subprocess, "run", fake_run)
    text, usage = lr._run_anthropic_cli("sys", "user", "sonnet", timeout=10, effort="medium")
    argv = seen["argv"]
    assert argv[argv.index("--model") + 1] == "sonnet"          # alias, never a versioned id
    assert argv[argv.index("--effort") + 1] == "medium"
    assert usage["resolved_model"] == "claude-sonnet-5-5"       # identity from call metadata
    # no effort → no flag; bad effort → refused
    assert "--effort" not in lr.build_cli_argv("sonnet", "<sys>")
    try:
        lr.build_cli_argv("sonnet", "<sys>", effort="ultra")
        assert False, "bad effort accepted"
    except ValueError:
        pass
    # defaults stay evergreen and in sync with the DB defaults
    assert lr.DEFAULT_MODEL == "sonnet" == db.DEFAULT_CONFIG["llm_model"]
    assert db.DEFAULT_CONFIG["llm_effort"] == lr.DEFAULT_EFFORT == "high"
    assert db.DEFAULT_CONFIG["validator_effort"] == lr.DEFAULT_VALIDATOR_EFFORT == "medium"
    assert "claude-sonnet-4-6" not in open(lr.__file__).read()
