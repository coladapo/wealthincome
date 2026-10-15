"""
LLM Router — provider-agnostic trading brain interface.

Swap between providers by setting config keys:
    llm_provider = anthropic_cli | anthropic_api | openai | gemini | grok | ollama
    llm_model    = sonnet | opus | haiku (evergreen aliases) | gpt-4o | gemini-2.5-pro | grok-3 | llama3.3 | ...

All providers receive the same system prompt and user prompt.
All providers return the same normalized dict — trading logic never changes.

Adding a new provider = add one _run_<name>() function + one entry in _PROVIDERS.
"""

import json
import logging
import os
import subprocess
import tempfile
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_PROVIDER = "anthropic_cli"
# Evergreen aliases only (Chris, 2026-10-14): `sonnet` / `opus` / `haiku` resolve to
# Anthropic's current model of that tier (verified from call metadata: `sonnet` ->
# claude-sonnet-5-5 on the pinned CLI). Never pin a versioned id here. Reasoning
# effort is set per task via the CLI's --effort flag; the RESOLVED model name from
# the response envelope is what gets written to the ledger, never the alias.
DEFAULT_MODEL    = "sonnet"
DEFAULT_EFFORT   = "high"      # the 5-minute decision brain (config key llm_effort)
DEFAULT_VALIDATOR_EFFORT = "medium"  # pre-trade validator (config key validator_effort)
VALID_EFFORTS = ("low", "medium", "high", "xhigh", "max")


# ── Prompt helpers (provider-agnostic) ────────────────────────────────────────

def _parse_trading_json(text: str) -> Dict[str, Any]:
    """Extract and parse JSON trading response from model output.
    Handles markdown fences, leading/trailing prose."""
    text = text.strip()
    if "```" in text:
        start = text.find("```")
        end   = text.rfind("```")
        if start != end:
            inner = text[start + 3:end].strip()
            if inner.startswith("json"):
                inner = inner[4:].strip()
            text = inner
    # If there's still prose, find the first { … }
    if not text.startswith("{"):
        brace = text.find("{")
        if brace != -1:
            text = text[brace:]
    return json.loads(text)


def _normalize_usage(raw: Dict[str, Any], provider: str) -> Dict[str, int]:
    """Normalize provider-specific usage keys to canonical shape."""
    if provider in ("anthropic_cli", "anthropic_api"):
        return {
            "input_tokens":        int(raw.get("input_tokens", 0) or 0),
            "output_tokens":       int(raw.get("output_tokens", 0) or 0),
            "cache_read_tokens":   int(raw.get("cache_read_input_tokens", 0) or 0),
            "cache_write_tokens":  int(raw.get("cache_creation_input_tokens", 0) or 0),
        }
    if provider == "openai":
        details = raw.get("prompt_tokens_details") or {}
        return {
            "input_tokens":        int(raw.get("prompt_tokens", 0) or 0),
            "output_tokens":       int(raw.get("completion_tokens", 0) or 0),
            "cache_read_tokens":   int(details.get("cached_tokens", 0) or 0),
            "cache_write_tokens":  0,
        }
    if provider in ("gemini",):
        return {
            "input_tokens":        int(raw.get("prompt_token_count", 0) or 0),
            "output_tokens":       int(raw.get("candidates_token_count", 0) or 0),
            "cache_read_tokens":   int(raw.get("cached_content_token_count", 0) or 0),
            "cache_write_tokens":  0,
        }
    if provider in ("grok",):
        # xAI uses OpenAI-compatible format
        return {
            "input_tokens":        int(raw.get("prompt_tokens", 0) or 0),
            "output_tokens":       int(raw.get("completion_tokens", 0) or 0),
            "cache_read_tokens":   0,
            "cache_write_tokens":  0,
        }
    if provider == "ollama":
        return {
            "input_tokens":        int(raw.get("prompt_eval_count", 0) or 0),
            "output_tokens":       int(raw.get("eval_count", 0) or 0),
            "cache_read_tokens":   0,
            "cache_write_tokens":  0,
        }
    # Unknown provider — return zeros so cost tracking doesn't crash
    return {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}


# ── Provider implementations ──────────────────────────────────────────────────

# ── Isolated brain call (2026-10-14) ─────────────────────────────────────────
# Every decision used to run `claude` with the operator's full interactive
# setup: personal settings.json (effort "high"), ~22 hook scripts, plugins, MCP
# servers and a 45 KB global CLAUDE.md. On a loaded machine that boot stalled
# past the 240 s timeout on 50–85% of calls (Sep–Oct 2026), and on Oct 5 a
# SessionEnd hook failure killed every call. The trader's brain must be a bare
# model call that still uses the subscription login, so:
#   * `--setting-sources ""`     → no user/project/local settings, hence no
#                                   hooks, no plugins, no CLAUDE.md
#   * `--strict-mcp-config`      → no MCP servers
#   * `--disable-slash-commands`, `--no-session-persistence`
#   * run from an empty temp cwd  → nothing project-level can be picked up
#   * pinned binary via WEALTHINCOME_CLAUDE_BIN (default <project>/bin/claude,
#     falling back to `claude` on PATH) and auto-update disabled.
# NOT `--bare`: that mode refuses the OAuth/keychain login and wants an API key.
_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI_ISOLATION_FLAGS = [
    "--setting-sources", "",
    "--strict-mcp-config",
    "--disable-slash-commands",
    "--no-session-persistence",
]


def _claude_binary() -> str:
    env_bin = os.environ.get("WEALTHINCOME_CLAUDE_BIN")
    if env_bin:
        return env_bin
    pinned = os.path.join(_PROJECT_DIR, "bin", "claude")
    return pinned if os.path.exists(pinned) else "claude"


def build_cli_argv(model: str, system_file: str, effort: Optional[str] = None) -> list:
    """The exact argv the brain runs — kept separate so tests can pin it."""
    argv = [
        _claude_binary(), "-p",
        "--output-format", "json",
        "--model", model,
        "--system-prompt-file", system_file,
        *CLI_ISOLATION_FLAGS,
    ]
    if effort:
        if effort not in VALID_EFFORTS:
            raise ValueError(f"effort must be one of {VALID_EFFORTS}, got {effort!r}")
        argv += ["--effort", effort]
    return argv


def _run_anthropic_cli(system_prompt: str, user_prompt: str, model: str, timeout: int,
                       effort: Optional[str] = None) -> Dict[str, Any]:
    """Uses `claude -p` CLI (no API key — requires Claude Max subscription).
    Runs isolated from the operator's interactive settings; see note above."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write(system_prompt)
        system_file = f.name
    empty_cwd = tempfile.mkdtemp(prefix="wi-brain-")
    env = dict(os.environ)
    env["DISABLE_AUTOUPDATER"] = "1"
    env["DISABLE_UPDATES"] = "1"

    try:
        proc = subprocess.run(
            build_cli_argv(model, system_file, effort),
            input=user_prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=empty_cwd,
            env=env,
        )
    finally:
        try:
            os.unlink(system_file)
            os.rmdir(empty_cwd)
        except OSError:
            pass

    if proc.returncode != 0:
        raise RuntimeError(f"Claude CLI exited {proc.returncode}: {proc.stderr[:300]}")

    # The CLI occasionally prints extra output after the JSON envelope
    # (e.g. self-update notices) — take the FIRST JSON document instead of
    # requiring the whole stdout to be one ("Extra data" crash, 2026-06-11).
    envelope, _ = json.JSONDecoder().raw_decode(proc.stdout.strip())
    text_output = envelope.get("result", "").strip()
    raw_usage   = dict(envelope.get("usage", {}) or {})
    # Model identity comes from call metadata, never from the alias we asked for.
    used = envelope.get("modelUsage") or {}
    if isinstance(used, dict) and used:
        raw_usage["resolved_model"] = next(iter(used))
    return text_output, raw_usage


def _run_anthropic_api(system_prompt: str, user_prompt: str, model: str, timeout: int) -> tuple:
    """Uses Anthropic Python SDK directly (requires ANTHROPIC_API_KEY env var)."""
    import anthropic  # deferred — only installed when this provider is active

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=[{
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},   # cache the large system prompt
        }],
        messages=[{"role": "user", "content": user_prompt}],
    )
    text_output = response.content[0].text
    raw_usage = {
        "input_tokens":                  response.usage.input_tokens,
        "output_tokens":                 response.usage.output_tokens,
        "cache_read_input_tokens":       getattr(response.usage, "cache_read_input_tokens", 0),
        "cache_creation_input_tokens":   getattr(response.usage, "cache_creation_input_tokens", 0),
    }
    return text_output, raw_usage


def _run_openai(system_prompt: str, user_prompt: str, model: str, timeout: int) -> tuple:
    """Uses OpenAI Python SDK (requires OPENAI_API_KEY env var)."""
    import openai  # deferred

    client = openai.OpenAI(timeout=timeout)
    response = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        max_tokens=4096,
    )
    text_output = response.choices[0].message.content or ""
    raw_usage = {
        "prompt_tokens":     response.usage.prompt_tokens,
        "completion_tokens": response.usage.completion_tokens,
        "prompt_tokens_details": {
            "cached_tokens": getattr(response.usage, "prompt_tokens_details", None)
                             and response.usage.prompt_tokens_details.cached_tokens or 0,
        },
    }
    return text_output, raw_usage


def _run_gemini(system_prompt: str, user_prompt: str, model: str, timeout: int) -> tuple:
    """Uses Google Generative AI SDK (requires GOOGLE_API_KEY env var)."""
    import google.generativeai as genai  # deferred
    import os

    genai.configure(api_key=os.environ["GOOGLE_API_KEY"])
    safety = [
        {"category": c, "threshold": "BLOCK_NONE"}
        for c in ["HARM_CATEGORY_HATE_SPEECH", "HARM_CATEGORY_HARASSMENT",
                  "HARM_CATEGORY_DANGEROUS_CONTENT", "HARM_CATEGORY_SEXUALLY_EXPLICIT"]
    ]
    gmodel = genai.GenerativeModel(
        model_name=model,
        system_instruction=system_prompt,
        generation_config={"response_mime_type": "application/json", "max_output_tokens": 4096},
        safety_settings=safety,
    )
    response = gmodel.generate_content(user_prompt)
    text_output = response.text or ""
    usage_meta = response.usage_metadata
    raw_usage = {
        "prompt_token_count":     getattr(usage_meta, "prompt_token_count", 0),
        "candidates_token_count": getattr(usage_meta, "candidates_token_count", 0),
        "cached_content_token_count": getattr(usage_meta, "cached_content_token_count", 0),
    }
    return text_output, raw_usage


def _run_grok(system_prompt: str, user_prompt: str, model: str, timeout: int) -> tuple:
    """Uses xAI Grok via OpenAI-compatible API (requires XAI_API_KEY env var)."""
    import openai  # deferred — Grok uses the OpenAI client pointed at xAI's base URL
    import os

    client = openai.OpenAI(
        api_key=os.environ["XAI_API_KEY"],
        base_url="https://api.x.ai/v1",
        timeout=timeout,
    )
    response = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        max_tokens=4096,
    )
    text_output = response.choices[0].message.content or ""
    raw_usage = {
        "prompt_tokens":     response.usage.prompt_tokens,
        "completion_tokens": response.usage.completion_tokens,
    }
    return text_output, raw_usage


def _run_ollama(system_prompt: str, user_prompt: str, model: str, timeout: int) -> tuple:
    """Uses local Ollama instance (no API key required, free).
    Base URL configurable via llm_ollama_base_url config key."""
    import requests as req
    from backend.db import get_config
    cfg = get_config()
    base_url = cfg.get("llm_ollama_base_url", "http://localhost:11434")

    payload = {
        "model":    model,
        "format":   "json",
        "stream":   False,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
    }
    resp = req.post(f"{base_url}/api/chat", json=payload, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    text_output = data.get("message", {}).get("content", "")
    raw_usage = {
        "prompt_eval_count": data.get("prompt_eval_count", 0),
        "eval_count":        data.get("eval_count", 0),
    }
    return text_output, raw_usage


# ── Provider dispatch map ─────────────────────────────────────────────────────

_PROVIDERS = {
    "anthropic_cli": _run_anthropic_cli,
    "anthropic_api": _run_anthropic_api,
    "openai":        _run_openai,
    "gemini":        _run_gemini,
    "grok":          _run_grok,
    "ollama":        _run_ollama,
}


# ── Public interface ──────────────────────────────────────────────────────────

def run_decision(
    watchlist: List[str],
    market_data: Dict[str, Any],
    portfolio: Dict[str, Any],
    account: Dict[str, Any],
    regime_summary: str = "",
    performance_feedback: str = "",
    news_context: str = "",
    portfolio_risk_context: str = "",
    calendar_context: str = "",
    timeout: int = 240,  # successful calls range 30-102s; 120s executed the slow
                         # tail (cold-cache calls after restarts). Cycle budget is
                         # 300s — 240s keeps one cycle's call inside it.
    provider_override: Optional[str] = None,
    model_override: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Provider-agnostic trading decision interface.

    Reads llm_provider and llm_model from DB config at call time.
    Override params let callers force a specific provider/model (e.g. for A/B tests).

    Returns:
        {
            "decisions":      [...],
            "market_summary": "...",
            "cycle_notes":    "...",
            "_usage":         {input_tokens, output_tokens, cache_read_tokens, cache_write_tokens},
            "_duration_ms":   int,
            "_provider":      str,
            "_model":         str,
            "_raw_response":  str,
            "_user_prompt":   str,
        }
    Returns None on any failure — caller must treat None as a skipped cycle.
    """
    from backend.db import get_config
    from core.claude_trader import _build_prompt

    # ── Resolve provider + model from DB config (or override) ─────────────────
    cfg      = get_config()
    provider = provider_override or cfg.get("llm_provider", DEFAULT_PROVIDER)
    model    = model_override    or cfg.get("llm_model",    DEFAULT_MODEL)
    effort   = cfg.get("llm_effort", DEFAULT_EFFORT)

    if provider not in _PROVIDERS:
        logger.error(f"Unknown LLM provider '{provider}'. Valid: {list(_PROVIDERS)}")
        return None

    # ── Build the prompt (same logic regardless of provider) ──────────────────
    user_prompt = _build_prompt(
        watchlist, market_data, portfolio, account,
        regime_summary=regime_summary,
        performance_feedback=performance_feedback,
        news_context=news_context,
        portfolio_risk_context=portfolio_risk_context,
        calendar_context=calendar_context,
    )

    from core.claude_trader import SYSTEM_PROMPT
    system_prompt = SYSTEM_PROMPT

    # ── Call the provider ──────────────────────────────────────────────────────
    t0 = time.time()
    try:
        fn = _PROVIDERS[provider]
        extra = {"effort": effort} if provider == "anthropic_cli" else {}
        text_output, raw_usage = fn(system_prompt, user_prompt, model, timeout, **extra)
        duration_ms = int((time.time() - t0) * 1000)
        if isinstance(raw_usage, dict) and raw_usage.get("resolved_model"):
            model = raw_usage.pop("resolved_model")  # ledger gets the real model id
    except subprocess.TimeoutExpired:
        logger.error(f"LLM provider '{provider}' timed out after {timeout}s")
        return None
    except Exception as e:
        logger.error(f"LLM provider '{provider}' failed: {e}")
        return None

    # ── Normalize usage ────────────────────────────────────────────────────────
    usage = _normalize_usage(raw_usage, provider)
    total_tokens = usage["input_tokens"] + usage["output_tokens"]
    logger.info(
        f"[{provider}/{model}] tokens: in={usage['input_tokens']:,} "
        f"out={usage['output_tokens']:,} cr={usage['cache_read_tokens']:,} "
        f"cw={usage['cache_write_tokens']:,} total={total_tokens:,} "
        f"duration={duration_ms}ms"
    )

    # ── Parse the JSON trading response ───────────────────────────────────────
    try:
        result = _parse_trading_json(text_output)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse {provider} JSON output: {e}\nRaw: {text_output[:400]}")
        return None

    # ── Attach metadata ────────────────────────────────────────────────────────
    result["_usage"]        = usage
    result["_duration_ms"]  = duration_ms
    result["_provider"]     = provider
    result["_model"]        = model
    result["_raw_response"] = text_output
    result["_user_prompt"]  = user_prompt

    logger.info(
        f"Decisions: {len(result.get('decisions', []))} | "
        f"summary: {result.get('market_summary', '')[:80]}"
    )
    return result
