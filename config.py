import json
from pathlib import Path

CONFIG_PATH = Path.home() / "ai-agent-config.json"

DEFAULTS = {
    # ── TGenie backend (default) ──────────────────────────────────────────
    "endpoint":         "https://your-ai-gateway.internal.company.com",
    "frontendUrl":      "https://your-frontend.internal.company.com",
    "targetUrlKeyword": "ai-app",       # keyword to find your app tab in Chrome CDP
    "cookieDomain":     ".company.com",  # domain for cookie capture
    "authToken":        "",
    "customHeader":     "",
    "cookies":          [],

    # ── OpenAI / Anthropic-compatible interface ───────────────────────────
    # "tgenie"    : default, use internal TGenie backend
    # "openai"    : standard OpenAI /chat/completions format
    #               (OpenAI, Groq, Ollama, LM Studio, etc.)
    # "anthropic" : Anthropic message format — system prompt extracted to
    #               top-level field; used by Cline-style internal proxies
    "interface":        "tgenie",       # "tgenie" | "openai" | "anthropic"
    "openaiApiKey":     "",             # sk-... or local dummy key
    "openaiBaseUrl":    "https://api.openai.com/v1",

    # ── Shared ────────────────────────────────────────────────────────────
    "defaultModel":     "gemini-2.5-flash",
    "systemPrompt":     "You are a helpful AI assistant.",
}

def load():
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            return {**DEFAULTS, **data}   # DEFAULTS fills in any missing keys
        except Exception:
            pass
    return dict(DEFAULTS)

def save(cfg: dict):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
