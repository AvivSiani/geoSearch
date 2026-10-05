"""Live smoke test for the configured model. NOT run by pytest — it needs a
real Ollama server and a pulled model. Run it by hand:

    uv run python scripts/smoke_model.py

It checks the five things Stage 2 §15 step 1 requires:
  1. Ollama >= 0.20.2 (carries the Gemma 4 tool-call fix).
  2. A plain chat call returns text.
  3. A tool call is actually emitted when a tool is bound.
  4. The configured num_ctx is in effect (via /api/ps `context_length`).
  5. The prompt-cache caveat: whether reported prompt tokens drop on a repeat
     call, which would make the ledger's truncation flag fire falsely.

Exits non-zero if any hard check fails.
"""

import json
import sys
import urllib.request

from langchain_core.tools import tool

from geosearch.agent.model import build_chat_model
from geosearch.config import GeoConfig

MIN_OLLAMA = (0, 20, 2)


def _parse_version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("-")[0].split(".")[:3])


def check_version(base_url: str) -> bool:
    with urllib.request.urlopen(f"{base_url}/api/version", timeout=10) as resp:
        version = json.load(resp)["version"]
    ok = _parse_version(version) >= MIN_OLLAMA
    print(f"[{'ok' if ok else 'FAIL'}] ollama version {version} (need >= 0.20.2)")
    return ok


def check_ps_context(base_url: str, expected_ctx: int) -> bool:
    with urllib.request.urlopen(f"{base_url}/api/ps", timeout=10) as resp:
        models = json.load(resp).get("models", [])
    if not models:
        print("[WARN] /api/ps lists no loaded model; can't confirm num_ctx")
        return True
    actual = models[0].get("context_length")
    ok = actual == expected_ctx
    print(f"[{'ok' if ok else 'FAIL'}] /api/ps context_length={actual} (want {expected_ctx})")
    return ok


def main() -> int:
    cfg = GeoConfig()
    model = build_chat_model(cfg.llm, cfg.budget)
    base_url = cfg.llm.base_url.rstrip("/")

    ok = check_version(base_url)

    plain = model.invoke("Reply with exactly the word: ready")
    plain_ok = bool(plain.content.strip())
    print(f"[{'ok' if plain_ok else 'FAIL'}] plain call -> {plain.content!r}")
    usage1 = plain.usage_metadata
    print(f"      usage_metadata: {usage1}")

    @tool
    def add(a: int, b: int) -> int:
        """Add two integers."""
        return a + b

    tool_resp = model.bind_tools([add]).invoke("Use the add tool to compute 2 + 3.")
    tool_ok = bool(tool_resp.tool_calls)
    print(f"[{'ok' if tool_ok else 'FAIL'}] tool call -> {tool_resp.tool_calls}")

    # num_ctx is only observable after the model is loaded, i.e. after a call.
    ctx_ok = check_ps_context(base_url, cfg.budget.context_window)

    # Prompt-cache caveat: same prompt twice, compare reported input tokens.
    r1 = model.invoke("Count to three.")
    r2 = model.invoke("Count to three.")
    in1 = (r1.usage_metadata or {}).get("input_tokens")
    in2 = (r2.usage_metadata or {}).get("input_tokens")
    print(f"[info] repeat-call input_tokens: first={in1} second={in2}")
    if in1 is not None and in2 is not None and in2 < in1:
        print(
            "      CAVEAT CONFIRMED: Ollama reports fewer prompt tokens on repeat "
            "(prompt cache). The ledger truncation check must stay conservative."
        )

    hard = [ok, plain_ok, tool_ok, ctx_ok]
    print("\nSMOKE PASS" if all(hard) else "\nSMOKE FAIL")
    return 0 if all(hard) else 1


if __name__ == "__main__":
    sys.exit(main())
