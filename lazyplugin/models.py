"""The model registry — bring your own model, wherever it runs.

LazyPlugin talks to anything that speaks the OpenAI chat-completions protocol,
which today is nearly everything: the big hosted APIs, the fast inference
providers, the aggregators, and every local runner (Ollama, LM Studio, vLLM,
llama.cpp). A provider is therefore just a base URL plus the environment
variable holding its key, and adding one is a five-line entry below.

Pick a model three ways, in order of precedence:

    lazyplugin "..." --provider ollama --model qwen2.5-coder:32b
    lazyplugin "..." --model openai/gpt-4o          # provider/model shorthand
    LAZYPLUGIN_MODEL=deepseek/deepseek-v4-flash lazyplugin "..."

Nothing here is a whitelist: any model string is passed through to the
provider untouched, so a model released this morning works this morning.
The entries below are a curated starting point, not a gate.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

# Prices are USD per 1M tokens, indicative and provider-published as of the
# date below. They drive only the cost line in the run report — never a
# billing decision. Override per run with LAZYPLUGIN_RATE_IN /
# LAZYPLUGIN_RATE_OUT; `None` means "unknown: report tokens, skip the dollars".
PRICES_AS_OF = "2026-09"


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    base_url: str
    env_key: str | None          # None = local runner, no key needed
    docs: str
    local: bool = False

    @property
    def key(self) -> str | None:
        if self.env_key is None:
            return "local"        # OpenAI clients demand a non-empty string
        return os.environ.get(self.env_key) or None

    @property
    def configured(self) -> bool:
        return self.local or bool(self.key)


@dataclass(frozen=True)
class Model:
    id: str                      # passed to the provider verbatim
    provider: str
    label: str
    price_in: float | None = None
    price_out: float | None = None
    notes: str = ""


PROVIDERS: dict[str, Provider] = {p.id: p for p in (
    Provider("deepseek",   "DeepSeek",    "https://api.deepseek.com",
             "DEEPSEEK_API_KEY",   "https://platform.deepseek.com"),
    Provider("openai",     "OpenAI",      "https://api.openai.com/v1",
             "OPENAI_API_KEY",     "https://platform.openai.com"),
    Provider("anthropic",  "Anthropic",   "https://api.anthropic.com/v1",
             "ANTHROPIC_API_KEY",  "https://docs.anthropic.com"),
    Provider("google",     "Google Gemini",
             "https://generativelanguage.googleapis.com/v1beta/openai/",
             "GEMINI_API_KEY",     "https://ai.google.dev"),
    Provider("groq",       "Groq",        "https://api.groq.com/openai/v1",
             "GROQ_API_KEY",       "https://console.groq.com"),
    Provider("openrouter", "OpenRouter",  "https://openrouter.ai/api/v1",
             "OPENROUTER_API_KEY", "https://openrouter.ai/models"),
    Provider("together",   "Together",    "https://api.together.xyz/v1",
             "TOGETHER_API_KEY",   "https://together.ai"),
    Provider("fireworks",  "Fireworks",   "https://api.fireworks.ai/inference/v1",
             "FIREWORKS_API_KEY",  "https://fireworks.ai"),
    Provider("mistral",    "Mistral",     "https://api.mistral.ai/v1",
             "MISTRAL_API_KEY",    "https://mistral.ai"),
    Provider("xai",        "xAI",         "https://api.x.ai/v1",
             "XAI_API_KEY",        "https://x.ai/api"),
    # --- local: free forever, your GPU, your rules -------------------------
    Provider("ollama",     "Ollama (local)",    "http://localhost:11434/v1",
             None, "https://ollama.com", local=True),
    Provider("lmstudio",   "LM Studio (local)", "http://localhost:1234/v1",
             None, "https://lmstudio.ai", local=True),
    Provider("vllm",       "vLLM (local)",      "http://localhost:8000/v1",
             None, "https://docs.vllm.ai", local=True),
)}

# A starting point, not a whitelist — see the module docstring.
MODELS: list[Model] = [
    Model("deepseek-v4-flash", "deepseek", "DeepSeek V4 Flash", 0.28, 1.14,
          "the LazySteve default: cheapest thing that reliably compiles"),
    Model("deepseek-v4-pro", "deepseek", "DeepSeek V4 Pro", None, None,
          "stronger reasoning, higher price"),

    Model("claude-sonnet-5", "anthropic", "Claude Sonnet 5", None, None,
          "strong Java; a good default if you have an Anthropic key"),
    Model("claude-opus-5", "anthropic", "Claude Opus 5", None, None,
          "most capable, most expensive"),
    Model("claude-haiku-4-5-20251001", "anthropic", "Claude Haiku 4.5", None, None,
          "fast and cheap"),

    Model("gpt-4o", "openai", "GPT-4o", 2.50, 10.00),
    Model("gpt-4o-mini", "openai", "GPT-4o mini", 0.15, 0.60,
          "budget option; expect more fix rounds"),

    Model("gemini-2.5-pro", "google", "Gemini 2.5 Pro", None, None),
    Model("gemini-2.5-flash", "google", "Gemini 2.5 Flash", None, None,
          "very large context, cheap"),

    Model("llama-3.3-70b-versatile", "groq", "Llama 3.3 70B", None, None,
          "Groq is absurdly fast; good for iterating"),
    Model("mistral-large-latest", "mistral", "Mistral Large", None, None),
    Model("grok-4", "xai", "Grok 4", None, None),

    # OpenRouter proxies most of the above under a single key — handy if you
    # would rather not collect ten different API keys.
    Model("anthropic/claude-sonnet-5", "openrouter", "Claude Sonnet 5 (via OpenRouter)"),
    Model("deepseek/deepseek-chat", "openrouter", "DeepSeek (via OpenRouter)"),

    # --- local -------------------------------------------------------------
    Model("qwen2.5-coder:32b", "ollama", "Qwen2.5 Coder 32B", 0.0, 0.0,
          "best local pick if you have ~20GB of VRAM"),
    Model("qwen2.5-coder:7b", "ollama", "Qwen2.5 Coder 7B", 0.0, 0.0,
          "runs on 8GB; expect more fix rounds"),
    Model("codellama:13b", "ollama", "Code Llama 13B", 0.0, 0.0),
    Model("deepseek-coder-v2:16b", "ollama", "DeepSeek Coder V2 16B", 0.0, 0.0),
]

BY_ID: dict[tuple[str, str], Model] = {(m.provider, m.id): m for m in MODELS}
DEFAULT = "deepseek/deepseek-v4-flash"


@dataclass
class Resolved:
    """Everything the client needs, plus the pricing used for the report."""
    provider: Provider
    model: str
    price_in: float | None = None
    price_out: float | None = None
    known: bool = False


def resolve(model: str | None = None, provider: str | None = None,
            api_key: str | None = None, base_url: str | None = None) -> Resolved:
    """Turn CLI/env choices into a concrete endpoint.

    Accepts "provider/model", a bare model alongside --provider, or an explicit
    --base-url for anything not in the table (a private gateway, a box on your
    LAN, a provider that launched yesterday).
    """
    model = model or os.environ.get("LAZYPLUGIN_MODEL") or DEFAULT
    if provider is None and "/" in model:
        head, tail = model.split("/", 1)
        if head in PROVIDERS:           # else it is a model id containing a slash
            provider, model = head, tail
    provider = provider or os.environ.get("LAZYPLUGIN_PROVIDER") or "deepseek"

    if provider not in PROVIDERS:
        known = ", ".join(sorted(PROVIDERS))
        raise ValueError(f"unknown provider {provider!r}. Known: {known}. "
                         f"For anything else pass --base-url and --key directly.")
    p = PROVIDERS[provider]

    # An explicit key or url always wins, so nothing here can trap a user.
    if api_key or base_url:
        p = Provider(p.id, p.label, base_url or p.base_url, p.env_key, p.docs, p.local)

    entry = BY_ID.get((provider, model))
    r = Resolved(p, model,
                 price_in=entry.price_in if entry else None,
                 price_out=entry.price_out if entry else None,
                 known=entry is not None)
    if p.local:                          # local inference costs no dollars
        r.price_in = r.price_out = 0.0
    return r


def credentials(r: Resolved, api_key: str | None = None) -> tuple[str, str]:
    """(key, base_url), with a message that says exactly what to do next."""
    key = api_key or r.provider.key
    if not key:
        raise RuntimeError(
            f"No API key for {r.provider.label}. Set {r.provider.env_key}, "
            f"pass --key, or use a local provider (--provider ollama) that needs "
            f"none. Get a key at {r.provider.docs}")
    return key, r.provider.base_url


def check(timeout: float = 6.0) -> list[dict]:
    """Probe every provider that looks configured and report what answers.

    This is the honest version of a compatibility table: rather than us
    claiming a provider works, your own machine says so. Local runners are
    probed too, so this also tells you whether Ollama is actually up.
    """
    import json
    import urllib.error
    import urllib.request

    rows = []
    for p in PROVIDERS.values():
        row = {"provider": p.id, "label": p.label, "local": p.local,
               "configured": p.configured, "status": "", "models": 0}
        if not p.configured:
            row["status"] = f"no key ({p.env_key})"
            rows.append(row)
            continue
        req = urllib.request.Request(p.base_url.rstrip("/") + "/models")
        if not p.local:
            req.add_header("Authorization", f"Bearer {p.key}")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
                row["models"] = len(data.get("data") or [])
                row["status"] = "ok"
        except urllib.error.HTTPError as e:
            # A 404 on /models is common and harmless: chat still works.
            row["status"] = "ok (no /models endpoint)" if e.code == 404 else f"HTTP {e.code}"
        except urllib.error.URLError as e:
            # Match on the exception, never on the message: OS error strings
            # are localised, so "connection refused" is not there to be found
            # on a Spanish or German machine.
            row["status"] = "not running" if p.local else str(e.reason)[:60]
        except Exception as e:                                    # noqa: BLE001
            row["status"] = str(e)[:60]
        rows.append(row)
    return rows
