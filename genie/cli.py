#!/usr/bin/env python3
"""
GenieCLI — AI-powered Trino query tuning CLI.

Usage:
    python -m genie                         # interactive chat (default)
    python -m genie setup                   # interactive setup wizard
    python -m genie setup trino             # configure Trino connection
    python -m genie query.sql               # file, non-interactive
    cat query.sql | python -m genie        # stdin pipe
    python -m genie sessions               # list saved conversations
    python -m genie config                 # show config
    python -m genie tools                  # list available tools
"""
from __future__ import annotations

import sys
from functools import partial
from pathlib import Path
from typing import Optional

import typer

from genie.core.config import load as load_config
from genie.core.registry import SkillRegistry
from genie.output.human import HumanSink
from genie.output.machine import MachineSink
from genie.session.manager import list_sessions, new_msg, new_session

__version__ = "5.0.0"

app = typer.Typer(
    name="genie",
    help="GenieCLI — AI-powered Trino query tuning",
    add_completion=False,
    invoke_without_command=True,
)

REASONING_LEVELS = ["disable", "low", "medium", "high"]


def _version_callback(value: bool) -> None:
    if value:
        print(f"genie {__version__}")
        raise typer.Exit()


# ── Provider factory ──────────────────────────────────────────────────────────

def _make_provider(cfg: dict, debug: bool = False):
    interface = cfg.get("interface", "tgenie")

    if interface == "openai":
        from genie.providers.openai import OpenAIProvider, set_debug
        set_debug(debug)
        return OpenAIProvider(cfg)
    elif interface == "anthropic":
        from genie.providers.anthropic import AnthropicProvider, set_debug
        set_debug(debug)
        return AnthropicProvider(cfg)
    else:
        from genie.providers.tgenie import TGenieProvider, set_debug
        set_debug(debug)
        return TGenieProvider(cfg)


# ── Skill discovery ───────────────────────────────────────────────────────────

_skills_discovered = False


def _reset_discovery_flag() -> None:
    """Reset the module-level discovery flag.

    Registered as a SkillRegistry clear hook so that SkillRegistry.clear()
    (used in tests) also resets this flag — otherwise _discover_skills() would
    return immediately on the next call without repopulating the registry.
    """
    global _skills_discovered
    _skills_discovered = False


SkillRegistry.register_clear_hook(_reset_discovery_flag)


def _discover_skills(skill_dirs: list[Path] | None = None, legacy: bool = False) -> None:
    global _skills_discovered
    if _skills_discovered:
        return
    bundled = Path(__file__).parent / "skills"
    paths = [bundled]
    if skill_dirs:
        paths.extend(skill_dirs)
    SkillRegistry.discover(paths)

    if legacy:
        SkillRegistry.discover_legacy("skills")
    _skills_discovered = True


# ── System prompt builder ─────────────────────────────────────────────────────

def _build_system_prompt(cfg: dict, use_skills: bool, model: str = "") -> str:
    base = cfg.get("systemPrompt", "")
    if not use_skills:
        return base

    # Filter skills by model capability tier
    from genie.core.model_profiles import get_profile
    profile = get_profile(model) if model else None
    tier = profile.skill_tier if profile else None
    skills = SkillRegistry.all(tier=tier)
    tool_lines = []
    for skill in skills:
        if skill.args:
            parts = []
            for arg in skill.args:
                part = f"{arg.name}: {arg.description}"
                if not arg.required:
                    part += f" (optional, default: {arg.default})"
                if arg.choices:
                    part += f" [{'/'.join(arg.choices)}]"
                parts.append(part)
            args_str = ", ".join(parts)
        else:
            args_str = "no args"
        tool_lines.append(f"- {skill.name}({args_str}): {skill.description}")

    tools_block = "\n".join(tool_lines)

    skill_prompt = f"""## HOW TO USE TOOLS

When you need a tool, output ONLY this JSON (no explanation, no markdown):
{{"memory": "1-2 sentence summary of progress.", "tool": "tool_name", "args": {{"key": "value"}}}}

When the task is complete and no more tools are needed, output:
{{"memory": "Task complete.", "tool": null, "args": {{}}}}

## AVAILABLE TOOLS

{tools_block}
"""
    return f"{base}\n\n{skill_prompt}".strip() if base else skill_prompt


# ── Typer commands ────────────────────────────────────────────────────────────

@app.callback()
def callback(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", callback=_version_callback, is_eager=True, help="Show version"),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
    no_color: bool = typer.Option(False, "--no-color", help="Disable color output"),
    model: Optional[str] = typer.Option(None, "-m", "--model", help="Model name"),
    provider_name: Optional[str] = typer.Option(None, "--provider", help="Provider: tgenie/openai/anthropic"),
    skill_dir: Optional[str] = typer.Option(None, "--skill-dir", help="Additional skill directory"),
    debug: bool = typer.Option(False, "-d", "--debug", help="Debug HTTP output"),
    skills: bool = typer.Option(True, "-s", "--skills/--no-skills", help="Enable skill tools (default: on)"),
    reasoning: str = typer.Option("disable", "-r", "--reasoning", help="Reasoning level"),
    target: Optional[str] = typer.Argument(None),
) -> None:
    """GenieCLI — AI-powered Trino query tuning."""
    if ctx.invoked_subcommand is not None:
        return

    is_tty = sys.stdout.isatty()
    output: HumanSink | MachineSink = MachineSink() if (json_output or not is_tty) else HumanSink()

    overrides: dict = {}
    if provider_name:
        overrides["interface"] = provider_name
    cfg = load_config(overrides)

    resolved_model = model or cfg.get("defaultModel", "gemini-2.5-flash")

    extra_dirs = [Path(skill_dir)] if skill_dir else None
    _discover_skills(extra_dirs, legacy=skills)

    if target == "sessions":
        _cmd_sessions(output)
        return
    if target == "config":
        _cmd_config(cfg, output)
        return
    if target == "tools":
        _cmd_tools(output, skills)
        return
    # Shim: Typer binds subcommand names to this callback's `target` positional
    # when `invoke_without_command=True`. Route known subcommands explicitly so
    # `genie doctor` / `genie verify` work without landing in the file-path
    # branch below.
    if target in ("doctor", "verify"):
        doctor()
        return

    if cfg.get("interface", "tgenie") == "tgenie" and not cfg.get("authToken"):
        output.error("No auth token. Run: python grab_auth.py")
        raise typer.Exit(1)

    provider = _make_provider(cfg, debug=debug)

    if target and target not in ("chat",):
        path = Path(target)
        if not path.exists():
            output.error(f"File not found: {target}")
            raise typer.Exit(1)
        query = path.read_text(encoding="utf-8", errors="replace")
        session = new_session(_build_system_prompt(cfg, skills, resolved_model))
        from genie.chat import _do_send
        from genie.core.context import SkillContext
        ctx_obj = SkillContext(provider=provider, output=output, config=cfg)
        _do_send(provider, session, resolved_model, reasoning, query, output, ctx_obj)
        return

    if not sys.stdin.isatty():
        query = sys.stdin.read()
        if not query.strip():
            output.error("Empty stdin")
            raise typer.Exit(1)
        session = new_session(_build_system_prompt(cfg, skills, resolved_model))
        from genie.chat import _do_send
        from genie.core.context import SkillContext
        ctx_obj = SkillContext(provider=provider, output=output, config=cfg)
        _do_send(provider, session, resolved_model, reasoning, query, output, ctx_obj)
        return

    if not isinstance(output, HumanSink):
        output = HumanSink()
    from genie.chat import _chat_loop
    build_prompt = partial(_build_system_prompt, cfg)
    _chat_loop(provider, cfg, resolved_model, reasoning, use_skills=skills, output=output, build_prompt=build_prompt)


# ── Subcommand implementations ────────────────────────────────────────────────

def _cmd_sessions(output: HumanSink | MachineSink) -> None:
    sessions = list_sessions()
    if isinstance(output, MachineSink):
        output.result(sessions)
        return
    if not sessions:
        output.print("  [dim]No saved sessions yet.[/dim]")
        return
    for i, s in enumerate(sessions, 1):
        output.print(
            f"  [cyan]{i}[/cyan]. [{s.get('created', '')[:15]}] "
            f"{s['title'][:40]:<42} [dim]{s['turns']} turns[/dim]"
        )


def _cmd_config(cfg: dict, output: HumanSink | MachineSink) -> None:
    if isinstance(output, MachineSink):
        safe = {k: v for k, v in cfg.items() if k not in ("authToken", "openaiApiKey", "cookies")}
        output.result(safe)
        return
    from rich.markup import escape
    output.print("")
    output.rule("config")
    safe_keys = ["interface", "endpoint", "frontendUrl", "defaultModel",
                 "openaiBaseUrl", "openaiContentArray", "systemPrompt"]
    secret_keys = ["authToken", "openaiApiKey", "customHeader"]
    for k in safe_keys:
        if k in cfg and cfg[k]:
            val = str(cfg[k])
            if len(val) > 60:
                val = val[:57] + "..."
            output.print(f"  [dim]{k:<20}[/dim]  {escape(val)}")
    for k in secret_keys:
        if k in cfg and cfg[k]:
            v = str(cfg[k])
            masked = v[:4] + "****" + v[-4:] if len(v) > 12 else "****"
            output.print(f"  [dim]{k:<20}[/dim]  {masked}")
    output.print("")


def _cmd_tools(output: HumanSink | MachineSink, use_skills: bool = True) -> None:
    if use_skills:
        _discover_skills(legacy=True)
    skills = SkillRegistry.all()
    if isinstance(output, MachineSink):
        output.result([s.spec() for s in skills])
        return
    if not skills:
        output.print("  [dim]No skills registered.[/dim]")
        return
    from rich.markup import escape
    output.print("")
    output.rule("skills")
    # Group by skill.group for hierarchy — scan order preserved within group.
    groups: dict[str, list] = {}
    for s in skills:
        groups.setdefault(s.group or "generic", []).append(s)
    for group_name in sorted(groups):
        output.print(f"\n  [bold cyan]{escape(group_name)}[/bold cyan]")
        for s in groups[group_name]:
            output.print(f"    [bold]{s.name:<28}[/bold] [dim]{escape(s.description)}[/dim]")
            if s.args:
                for arg in s.args:
                    detail = arg.description
                    if not arg.required:
                        detail += f" (default: {arg.default})"
                    output.print(f"    {'':28} [dim]· {arg.name}: {detail}[/dim]")
    output.print("")


# ── Setup wizard ─────────────────────────────────────────────────────────────

@app.command()
def setup(
    target: str = typer.Argument("llm", help="What to set up: llm, trino, mcp"),
) -> None:
    """Interactive setup wizard for LLM backend, Trino, or MCP."""
    from genie.setup_wizard import setup_check, setup_llm, setup_mcp, setup_trino

    wizards = {"llm": setup_llm, "trino": setup_trino, "mcp": setup_mcp, "check": setup_check}
    wizard = wizards.get(target)
    if wizard is None:
        print(f"Unknown target: {target}. Choose from: {', '.join(wizards)}")
        raise typer.Exit(1)
    wizard()


# ── Doctor / verify ──────────────────────────────────────────────────────────

@app.command()
def doctor() -> None:
    """Preflight check: Python version, genie on PATH, deps, LLM, Trino, MCP."""
    import platform
    import shutil
    from genie.core.config import load as load_config
    from rich.console import Console

    console = Console()
    cfg = load_config()
    checks: list[tuple[str, str, str]] = []  # (name, status, detail)

    # 1. Python version (≥ 3.9)
    py_ver = platform.python_version()
    py_ok = sys.version_info >= (3, 9)
    checks.append(("Python version", "OK" if py_ok else "FAIL",
                   f"{py_ver}" + ("" if py_ok else " (need ≥ 3.9)")))

    # 2. `genie` on PATH
    genie_path = shutil.which("genie")
    checks.append(("genie on PATH", "OK" if genie_path else "WARN",
                   genie_path or "not found — try `source .venv/bin/activate` or reinstall"))

    # 3. trino Python driver
    try:
        import trino  # noqa: F401
        checks.append(("trino driver", "OK", f"imported (v{getattr(trino, '__version__', 'unknown')})"))
    except ImportError:
        checks.append(("trino driver", "SKIP", "not installed — `pip install trino` (only needed for direct queries)"))

    # 4. sqlglot (used by linter + MCP research)
    try:
        import sqlglot  # noqa: F401
        checks.append(("sqlglot", "OK", "imported"))
    except ImportError:
        checks.append(("sqlglot", "SKIP", "not installed — linter + MCP research degrade"))

    # 5. LLM provider
    interface = cfg.get("interface", "tgenie")
    try:
        provider = _make_provider(cfg)
        checks.append(("LLM provider", "OK", f"{interface} ({provider.name})"))
    except Exception as exc:
        checks.append(("LLM provider", "FAIL", f"{interface}: {exc}"))

    # 6. Trino connection
    try:
        from genie.skills.trino_query.connection import get_active_profile, status_line
        profile = get_active_profile()
        conn = profile.connect()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.fetchall()
        conn.close()
        checks.append(("Trino connection", "OK", status_line()))
    except ImportError:
        checks.append(("Trino connection", "SKIP", "driver missing (see above)"))
    except Exception as exc:
        checks.append(("Trino connection", "FAIL", str(exc)))

    # 7. MCP Trino reachable
    try:
        from genie.skills.mcp_trino.client import load_mcp_config
        mcp_cfg = load_mcp_config()
        if mcp_cfg and mcp_cfg.enabled and mcp_cfg.url:
            import urllib.request
            urllib.request.urlopen(mcp_cfg.url, timeout=3)
            checks.append(("MCP Trino", "OK", mcp_cfg.url))
        else:
            checks.append(("MCP Trino", "SKIP", "not enabled (genie setup mcp)"))
    except Exception as exc:
        checks.append(("MCP Trino", "FAIL", str(exc)))

    # Render
    color_map = {"OK": "green", "WARN": "yellow", "SKIP": "yellow", "FAIL": "red"}
    console.print("")
    for name, status, detail in checks:
        color = color_map.get(status, "white")
        console.print(f"  [{color}]{status:<4}[/{color}]  [bold]{name:<20}[/bold]  [dim]{detail}[/dim]")

    ok_count = sum(1 for _, s, _ in checks if s == "OK")
    fail_count = sum(1 for _, s, _ in checks if s == "FAIL")
    console.print(f"\n  [bold]{ok_count}/{len(checks)}[/bold] OK"
                  + (f", [red]{fail_count} FAIL[/red]" if fail_count else "") + "\n")

    if fail_count:
        raise typer.Exit(1)


@app.command()
def verify() -> None:
    """Alias for `genie doctor` (kept for backwards compatibility)."""
    doctor()


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    app()
