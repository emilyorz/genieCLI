#!/usr/bin/env python3
"""
GenieCLI — plugin-based AI agent CLI.

Usage:
    python -m genie                         # interactive chat (default)
    python -m genie chat                    # same as above
    python -m genie query.sql               # file, non-interactive
    cat query.sql | python -m genie        # stdin pipe
    python -m genie --json tools           # machine-readable tool list
    python -m genie --skills --debug       # tools + debug output
    python -m genie sessions               # list saved conversations
    python -m genie config                 # show config
    python -m genie tools                  # list available tools
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

import typer

from genie.core.config import load as load_config, save as save_config
from genie.core.registry import SkillRegistry
from genie.core.tool_call import parse_tool_call
from genie.output.human import HumanSink
from genie.output.machine import MachineSink
from genie.session.manager import (
    list_sessions, load_session, new_msg, new_session,
    save_session, update_title,
)

app = typer.Typer(
    name="genie",
    help="GenieCLI — plugin-based AI agent",
    add_completion=False,
    invoke_without_command=True,
)

REASONING_LEVELS = ["disable", "low", "medium", "high"]
MAX_TOOL_LOOPS = 15


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

def _discover_skills(skill_dirs: list[Path] | None = None, legacy: bool = False) -> None:
    bundled = Path(__file__).parent / "skills"
    paths = [bundled]
    if skill_dirs:
        paths.extend(skill_dirs)
    SkillRegistry.discover(paths)

    if legacy:
        # Also register skills from the old skills/ directory
        SkillRegistry.discover_legacy("skills")


# ── System prompt builder ─────────────────────────────────────────────────────

def _build_system_prompt(cfg: dict, use_skills: bool) -> str:
    base = cfg.get("systemPrompt", "")
    if not use_skills:
        return base

    skills = SkillRegistry.all()
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


# ── Tool call execution ───────────────────────────────────────────────────────

def _run_tool_call(tool_call: dict, ctx) -> str:
    name = tool_call.get("tool") or ""
    args = tool_call.get("args") or {}
    if not isinstance(args, dict):
        args = {}
    return SkillRegistry.run_tool(name, args, ctx)


def _normalize_result(result) -> str:
    if result is None:
        return ""
    return result if isinstance(result, str) else str(result)


# ── Chat send (tool loop) ─────────────────────────────────────────────────────

def _send_with_tools(
    provider,
    session: dict,
    model: str,
    reasoning: str,
    output: HumanSink | MachineSink,
    ctx,
) -> str | None:
    from genie.core.provider import CompletionRequest

    _last_memory = ""
    recent_actions: list[str] = []

    for _loop in range(MAX_TOOL_LOOPS):
        req = CompletionRequest(messages=session["history"], model=model, reasoning=reasoning)
        reply = provider.complete_text(req)
        if not reply:
            return None

        tool_call = parse_tool_call(reply)
        if not tool_call:
            return reply

        tool_name = tool_call.get("tool", "?")
        tool_args = tool_call.get("args") or {}
        if not isinstance(tool_args, dict):
            tool_args = {}

        action_key = tool_name + json.dumps(tool_args, sort_keys=True, default=str)
        recent_actions.append(action_key)
        if len(recent_actions) > 20:
            recent_actions.pop(0)
        if recent_actions.count(action_key) >= 5:
            return f"Loop detected: repeating '{tool_name}' too many times."

        if isinstance(output, HumanSink):
            output.tool_call(tool_name, tool_args)
        else:
            output.progress(f"[Tool] {tool_name}")

        result = _normalize_result(_run_tool_call(tool_call, ctx))
        session["history"].append(new_msg("assistant", reply))

        # Handle screenshot results
        if result.startswith("__SCREENSHOT__:"):
            try:
                _, filename, b64_data = result.split(":", 2)
                import base64
                img_bytes = base64.b64decode(b64_data)
            except Exception as exc:
                err_msg = f"Screenshot capture failed: {exc}"
                mem_prefix = f"[Previous step memory]: {_last_memory}\n\n" if _last_memory else ""
                session["history"].append(
                    new_msg("user", f"{mem_prefix}[Tool result: {tool_name}]\n{err_msg}\n\nPlease continue.")
                )
                save_session(session)
                continue

            output.progress(f"[Screenshot] {filename} ({len(img_bytes)//1024}KB) — sending to AI")
            mem_prefix = f"[Previous step memory]: {_last_memory}\n\n" if _last_memory else ""
            session["history"].append(
                new_msg("user", f"{mem_prefix}Screenshot taken. Analyze and continue.")
            )
            _last_memory = tool_call.get("memory", "")
            save_session(session)

            try:
                req2 = CompletionRequest(
                    messages=session["history"],
                    model=model,
                    reasoning=reasoning,
                    files=[{"filename": filename, "content_type": "image/png", "data": img_bytes}],
                )
                img_reply = provider.complete_text(req2)
            except Exception as exc:
                output.error(f"Screenshot send failed: {exc}")
                img_reply = None

            if img_reply and img_reply.strip():
                tool_call2 = parse_tool_call(img_reply)
                if not tool_call2:
                    return img_reply
                session["history"].append(new_msg("assistant", img_reply))
                tool_name2 = tool_call2.get("tool", "?")
                result2 = _normalize_result(_run_tool_call(tool_call2, ctx))
                if isinstance(output, HumanSink):
                    output.tool_result(result2)
                mem_prefix2 = f"[Previous step memory]: {_last_memory}\n\n" if _last_memory else ""
                session["history"].append(
                    new_msg("user", f"{mem_prefix2}[Tool result: {tool_name2}]\n{result2}\n\nPlease continue.")
                )
                _last_memory = tool_call2.get("memory", "")
                save_session(session)
            else:
                output.error("AI returned empty after screenshot (model may lack vision support)")
            continue

        if isinstance(output, HumanSink):
            output.tool_result(result)
        mem_prefix = f"[Previous step memory]: {_last_memory}\n\n" if _last_memory else ""
        session["history"].append(
            new_msg("user", f"{mem_prefix}[Tool result: {tool_name}]\n{result}\n\nPlease continue.")
        )
        _last_memory = tool_call.get("memory", "")
        save_session(session)

    output.error("(max tool loops reached)")
    return None


# ── Single send (no tool loop) ────────────────────────────────────────────────

def _do_send(
    provider,
    session: dict,
    model: str,
    reasoning: str,
    user_input: str,
    output: HumanSink | MachineSink,
    ctx,
) -> None:
    session["history"].append(new_msg("user", user_input))
    if sum(1 for m in session["history"] if m["role"] == "user") == 1:
        update_title(session, user_input)

    output.progress("AI thinking...")
    try:
        reply = _send_with_tools(provider, session, model, reasoning, output, ctx)
    except Exception as exc:
        output.error(str(exc))
        session["history"].pop()
        return

    if reply:
        session["history"].append(new_msg("assistant", reply))
        save_session(session)
        output.markdown(reply)
    else:
        output.error("Empty response from AI")
        session["history"].pop()


# ── Prompt helpers ────────────────────────────────────────────────────────────

def _get_prompt_session():
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory
    _hist = Path.home() / ".tgenie_history"
    return PromptSession(history=FileHistory(str(_hist)))


_ps = None


def _read_input(prompt_str: str = "  You > ") -> str:
    global _ps
    if _ps is None:
        _ps = _get_prompt_session()
    try:
        return _ps.prompt(prompt_str)
    except EOFError:
        return "/exit"
    except KeyboardInterrupt:
        return ""


def _read_paste_mode() -> str:
    from prompt_toolkit.key_binding import KeyBindings
    from rich.console import Console
    kb = KeyBindings()
    _con = Console(force_terminal=True, highlight=False)

    @kb.add("c-d", eager=True)
    def _submit(event):
        event.current_buffer.validate_and_handle()

    _con.print("  [dim](paste mode — Ctrl-D to send, Ctrl-C to cancel)[/dim]")
    try:
        global _ps
        if _ps is None:
            _ps = _get_prompt_session()
        return _ps.prompt("  ... ", multiline=True, key_bindings=kb)
    except (KeyboardInterrupt, EOFError):
        return ""


def _read_editor_mode() -> str:
    from rich.console import Console
    from rich.markup import escape
    _con = Console(force_terminal=True, highlight=False)
    editor_env = os.environ.get("EDITOR", "")
    editor_parts = shlex.split(editor_env) if editor_env else []
    chosen_parts: list[str] = []
    for candidate_parts in [editor_parts, ["vim"], ["nano"]]:
        if candidate_parts and subprocess.run(
            ["which", candidate_parts[0]], capture_output=True
        ).returncode == 0:
            chosen_parts = candidate_parts
            break
    if not chosen_parts:
        _con.print("  [red][ERROR] No editor found. Set $EDITOR, or install vim/nano.[/red]")
        return ""
    with tempfile.NamedTemporaryFile(suffix=".sql", mode="w", delete=False) as f:
        tmpfile = f.name
    try:
        result = subprocess.run(chosen_parts + [tmpfile])
        if result.returncode != 0:
            return ""
        return Path(tmpfile).read_text(encoding="utf-8", errors="replace").strip()
    except Exception as exc:
        _con.print(f"  [red][ERROR] Editor failed: {escape(str(exc))}[/red]")
        return ""
    finally:
        try:
            os.unlink(tmpfile)
        except OSError:
            pass


# ── Autoresearch ──────────────────────────────────────────────────────────────

def _run_autoresearch(provider, cfg: dict, model: str, reasoning: str, output) -> None:
    from genie.core.provider import CompletionRequest
    from genie.runtime.eval_loop import RunConfig, RunManager

    output.progress("== Autoresearch Setup ==")
    output.progress("Press Enter to accept defaults shown in brackets.")

    def _ask(question: str, default: str = "") -> str:
        suffix = f" [{default}]" if default else ""
        try:
            val = _read_input(f"  {question}{suffix}: ").strip()
            return val if val else default
        except (EOFError, KeyboardInterrupt):
            return default

    goal = _ask("1. Goal — what to improve")
    if not goal:
        output.error("Goal is required.")
        return

    scope_raw = _ask("2. Scope — file globs (space-separated)", "**/*.py")
    scope = scope_raw.split() if scope_raw else ["**/*.py"]
    verify = _ask("3. Verify command — shell command whose stdout contains the metric")
    if not verify:
        output.error("Verify command is required.")
        return

    direction = ""
    while direction not in ("higher", "lower"):
        direction = _ask("4. Direction — which is better", "higher").lower()

    guard = _ask("5. Guard command — must exit 0 to keep change (optional)", "") or None
    iterations_raw = _ask("6. Max iterations", "10")
    try:
        max_iter = max(1, int(iterations_raw))
    except ValueError:
        max_iter = 10

    run_cfg = RunConfig(
        goal=goal, scope=scope, metric_direction=direction,
        verify_command=verify, guard_command=guard, max_iterations=max_iter,
    )

    try:
        from workflows.loader import WorkflowLoader
        loader = WorkflowLoader()
        workflow_body = loader.inject_prompt("autoresearch") or ""
    except Exception:
        workflow_body = ""

    skill_prompt = _build_system_prompt(cfg, use_skills=True)
    sys_prompt = f"{workflow_body}\n\n{skill_prompt}".strip()
    ar_session = new_session(sys_prompt)
    cwd = str(Path.cwd())
    manager = RunManager()

    output.progress("Measuring baseline metric...")
    state = manager.start(run_cfg, cwd)
    if state.status == "failed":
        output.error("Run initialisation failed. Check git repo and verify command.")
        return

    output.progress(f"Baseline: {state.baseline_metric}")
    if state.journal_path:
        output.progress(f"Journal : {state.journal_path}")

    from rich.markup import escape as _esc

    try:
        while manager.should_continue(state):
            iteration = state.iteration + 1
            output.progress(f"── Iteration {iteration}/{max_iter} ──")

            last = state.history[-1] if state.history else None
            if last:
                delta_str = f"{last.delta:+.4f}" if last.delta is not None else "N/A"
                last_iter_str = f"{last.status} (metric={last.metric}, delta={delta_str})"
            else:
                last_iter_str = "N/A (first iteration)"

            try:
                git_result = subprocess.run(
                    ["git", "log", "--oneline", "-5"],
                    capture_output=True, text=True, cwd=cwd,
                )
                git_log = git_result.stdout.strip() or "(no commits)"
            except Exception:
                git_log = "(git log unavailable)"

            context = (
                f"[Autoresearch Iteration {iteration}]\n"
                f"Goal: {state.config.goal}\n"
                f"Baseline metric: {state.baseline_metric}\n"
                f"Current best: {state.current_best}\n"
                f"Last iteration: {last_iter_str}\n"
                f"Direction: {state.config.metric_direction} is better\n"
                f"\nRecent git log:\n{git_log}\n"
                f"\nWhat would you like to try next? ONE focused change."
            )
            ar_session["history"].append(new_msg("user", context))

            output.progress("AI thinking...")
            req = CompletionRequest(messages=ar_session["history"], model=model, reasoning=reasoning)
            reply = provider.complete_text(req)
            if not reply:
                output.error("Empty response from AI — stopping.")
                break

            ar_session["history"].append(new_msg("assistant", reply))

            tool_call = parse_tool_call(reply)
            if not tool_call:
                reminder = (
                    "Please make exactly ONE file_patch tool call now. "
                    'Use the JSON format: {"memory": "hypothesis", "tool": "file_patch", "args": {...}}'
                )
                ar_session["history"].append(new_msg("user", reminder))
                req2 = CompletionRequest(messages=ar_session["history"], model=model, reasoning=reasoning)
                reply2 = provider.complete_text(req2)
                if reply2:
                    ar_session["history"].append(new_msg("assistant", reply2))
                    tool_call = parse_tool_call(reply2)

            if not tool_call:
                output.progress("[WARN] No tool call received — skipping iteration.")
                continue

            tool_name = tool_call.get("tool", "?")
            hypothesis = tool_call.get("memory", "No hypothesis")
            tool_args = tool_call.get("args") or {}
            output.progress(f"[Tool] {tool_name}  | Hypothesis: {hypothesis[:80]}")

            # Execute the tool
            from genie.core.context import SkillContext
            ctx = SkillContext(provider=provider, output=output, config=cfg)
            patch_result = _normalize_result(_run_tool_call(tool_call, ctx))
            output.progress(f"[Result] {patch_result[:80]}")

            if patch_result.startswith("ERROR"):
                ar_session["history"].append(new_msg(
                    "user",
                    f"[Tool result: {tool_name}]\n{patch_result}\n\nPatch failed. Try a different approach.",
                ))
                continue

            state = manager.step(state, hypothesis, [])
            last = state.history[-1] if state.history else None

            if last:
                delta_str = f"{last.delta:+.4f}" if last.delta is not None else "N/A"
                kept = last.status == "improved"
                output.progress(
                    f"[{last.status.upper()}] metric={last.metric}  delta={delta_str}  "
                    f"{'KEPT' if kept else 'REVERTED'}"
                )
                result_msg = (
                    f"[Tool result: {tool_name}]\n{patch_result}\n\n"
                    f"[Iteration {state.iteration} result]\n"
                    f"Status : {last.status}\nMetric : {last.metric}\nDelta  : {delta_str}\n"
                    f"{'Change KEPT.' if kept else 'Change REVERTED.'}"
                )
                ar_session["history"].append(new_msg("user", result_msg))

            if state.status == "failed":
                output.error("Run manager failed — stopping.")
                break

    except KeyboardInterrupt:
        output.progress("[INTERRUPTED] Stopping autoresearch loop...")
        state.status = "stopped"

    output.markdown(manager.summary(state))
    if state.journal_path:
        output.progress(f"Journal saved: {state.journal_path}")


# ── Chat loop ─────────────────────────────────────────────────────────────────

def _chat_loop(
    provider,
    cfg: dict,
    model: str,
    reasoning: str,
    use_skills: bool,
    output: HumanSink,
) -> None:
    from genie.core.context import SkillContext
    from rich.markup import escape

    ctx = SkillContext(provider=provider, output=output, config=cfg)
    current_reasoning = reasoning
    session = new_session(_build_system_prompt(cfg, use_skills))

    output.print("\n  [cyan]+======================================+[/cyan]")
    output.print("  [cyan]|        GenieCLI  v4.0               |[/cyan]")
    output.print("  [cyan]+======================================+[/cyan]\n")
    output.print(f"  [dim]Model   : {model}[/dim]")
    output.print(f"  [dim]Skills  : {'enabled' if use_skills else 'disabled'}[/dim]")
    output.print("  [dim]Type /help for commands[/dim]\n")

    while True:
        user_input = _read_input()
        if not user_input.strip():
            continue

        parts = user_input.strip().split()
        cmd = parts[0].lower()
        args = parts[1:]

        match cmd:
            case "/exit":
                if any(m["role"] == "user" for m in session["history"]):
                    save_session(session)
                    output.print(f"  [dim]Saved: {escape(session['title'])}[/dim]")
                output.print("  [yellow]Goodbye![/yellow]")
                try:
                    from skills._cdp import close_shared_cdp  # type: ignore[import]
                    close_shared_cdp()
                except Exception:
                    pass
                break

            case "/new":
                if any(m["role"] == "user" for m in session["history"]):
                    save_session(session)
                    output.print(f"  [dim]Saved: {escape(session['title'])}[/dim]")
                session = new_session(_build_system_prompt(cfg, use_skills))
                output.print("  [green]New conversation started.[/green]")

            case "/sessions":
                sessions = list_sessions()
                if not sessions:
                    output.print("  [dim]No saved sessions yet.[/dim]")
                else:
                    for i, s in enumerate(sessions, 1):
                        output.print(
                            f"  [cyan]{i}[/cyan]. [{s.get('created','')[:15]}] "
                            f"{escape(s['title'][:40]):<42} [dim]{s['turns']} turns[/dim]"
                        )

            case "/load":
                sessions = list_sessions()
                if not sessions:
                    output.print("  [dim]No sessions.[/dim]")
                else:
                    for i, s in enumerate(sessions, 1):
                        output.print(f"  [cyan]{i}[/cyan]. {escape(s['title'][:50])}")
                    try:
                        raw = _read_input("  Load number > ").strip()
                        n = int(raw)
                        if 1 <= n <= len(sessions):
                            session = load_session(sessions[n - 1]["filename"])
                            output.print(f"  [green]Loaded: {escape(session['title'])}[/green]")
                    except (ValueError, EOFError, KeyboardInterrupt):
                        pass

            case "/history":
                visible = [m for m in session["history"] if m["role"] != "system"]
                if not visible:
                    output.print("  [dim](empty)[/dim]")
                for msg in visible:
                    text = msg["content"][0]["text"]
                    role = msg["role"]
                    if role == "user" and text.startswith("[Tool result:"):
                        output.print(f"  [yellow][Tool][/yellow]  {escape(text[:120])}")
                    elif role == "user":
                        output.print(f"  [cyan][You][/cyan]   {escape(text[:120])}")
                    elif role == "assistant":
                        output.print(f"  [green][AI][/green]    {escape(text[:120])}")

            case "/skills" | "/tools":
                skills = SkillRegistry.all()
                if not skills:
                    output.print("  [dim]No skills registered.[/dim]")
                else:
                    for s in skills:
                        output.print(f"  [cyan]{s.name:<30}[/cyan] {s.description}")

            case "/clear":
                session["history"] = []
                sys_p = _build_system_prompt(cfg, use_skills)
                if sys_p:
                    session["history"].append(new_msg("system", sys_p))
                output.print("  [green]Cleared.[/green]")

            case "/reasoning":
                if args and args[0] in REASONING_LEVELS:
                    current_reasoning = args[0]
                else:
                    idx = REASONING_LEVELS.index(current_reasoning) if current_reasoning in REASONING_LEVELS else 0
                    current_reasoning = REASONING_LEVELS[(idx + 1) % len(REASONING_LEVELS)]
                output.print(f"  [green]Reasoning: {current_reasoning}[/green]")

            case "/renew":
                output.print("  [yellow]Refreshing token...[/yellow]")
                result = subprocess.run([sys.executable, "grab_auth.py"])
                if result.returncode == 0:
                    cfg.update(load_config())
                    output.print("  [green][OK] Token refreshed.[/green]")
                else:
                    output.error("Token refresh failed.")

            case "/autoresearch":
                if not use_skills:
                    output.print("  [yellow]Skills must be enabled. Restart with --skills[/yellow]")
                else:
                    _run_autoresearch(provider, cfg, model, current_reasoning, output)

            case "/paste":
                pasted = _read_paste_mode()
                if pasted.strip():
                    _do_send(provider, session, model, current_reasoning, pasted, output, ctx)

            case "/editor":
                edited = _read_editor_mode()
                if edited.strip():
                    _do_send(provider, session, model, current_reasoning, edited, output, ctx)

            case "/help":
                cmds = [
                    ("/new",          "Start a new conversation"),
                    ("/sessions",     "List saved conversations"),
                    ("/load <n>",     "Load conversation by number"),
                    ("/history",      "Show current conversation"),
                    ("/skills",       "List available skills/tools"),
                    ("/clear",        "Clear current conversation"),
                    ("/paste",        "Multiline paste mode (Ctrl-D to send)"),
                    ("/editor",       "Open editor for input"),
                    ("/autoresearch", "Start autonomous iteration loop"),
                    ("/reasoning",    "Toggle reasoning: disable/low/medium/high"),
                    ("/renew",        "Refresh auth token"),
                    ("/exit",         "Quit"),
                ]
                for c, d in cmds:
                    output.print(f"  [cyan]{c:<22}[/cyan] {d}")

            case _ if cmd.startswith("/"):
                output.print(f"  [red]Unknown: {escape(cmd)}. Type /help.[/red]")

            case _:
                _do_send(provider, session, model, current_reasoning, user_input, output, ctx)


# ── Typer commands ────────────────────────────────────────────────────────────

@app.callback()
def callback(
    ctx: typer.Context,
    json_output: bool = typer.Option(False, "--json", help="Machine-readable JSON output"),
    no_color: bool = typer.Option(False, "--no-color", help="Disable color output"),
    model: Optional[str] = typer.Option(None, "-m", "--model", help="Model name"),
    provider_name: Optional[str] = typer.Option(None, "--provider", help="Provider: tgenie/openai/anthropic"),
    skill_dir: Optional[str] = typer.Option(None, "--skill-dir", help="Additional skill directory"),
    debug: bool = typer.Option(False, "-d", "--debug", help="Debug HTTP output"),
    skills: bool = typer.Option(False, "-s", "--skills", help="Enable skill tools"),
    reasoning: str = typer.Option("disable", "-r", "--reasoning", help="Reasoning level"),
    target: Optional[str] = typer.Argument(None),
) -> None:
    """GenieCLI — plugin-based AI agent."""
    if ctx.invoked_subcommand is not None:
        return

    # Determine output sink
    is_tty = sys.stdout.isatty()
    output: HumanSink | MachineSink = MachineSink() if (json_output or not is_tty) else HumanSink()

    # Load config + apply overrides
    overrides: dict = {}
    if provider_name:
        overrides["interface"] = provider_name
    cfg = load_config(overrides)

    resolved_model = model or cfg.get("defaultModel", "gemini-2.5-flash")

    # Discover skills (needed for tools/config subcommands too)
    extra_dirs = [Path(skill_dir)] if skill_dir else None
    _discover_skills(extra_dirs, legacy=skills)

    # Handle subcommands passed as positional arg (no auth needed)
    if target == "sessions":
        _cmd_sessions(output)
        return
    if target == "config":
        _cmd_config(cfg, output)
        return
    if target == "tools":
        _cmd_tools(output, skills)
        return

    # Validate auth (required for AI calls)
    if cfg.get("interface", "tgenie") == "tgenie" and not cfg.get("authToken"):
        output.error("No auth token. Run: python grab_auth.py")
        raise typer.Exit(1)

    provider = _make_provider(cfg, debug=debug)

    # Non-interactive: file argument
    if target and target not in ("chat",):
        path = Path(target)
        if not path.exists():
            output.error(f"File not found: {target}")
            raise typer.Exit(1)
        query = path.read_text(encoding="utf-8", errors="replace")
        session = new_session(_build_system_prompt(cfg, skills))
        from genie.core.context import SkillContext
        ctx_obj = SkillContext(provider=provider, output=output, config=cfg)
        _do_send(provider, session, resolved_model, reasoning, query, output, ctx_obj)
        return

    # Non-interactive: stdin pipe
    if not sys.stdin.isatty():
        query = sys.stdin.read()
        if not query.strip():
            output.error("Empty stdin")
            raise typer.Exit(1)
        session = new_session(_build_system_prompt(cfg, skills))
        from genie.core.context import SkillContext
        ctx_obj = SkillContext(provider=provider, output=output, config=cfg)
        _do_send(provider, session, resolved_model, reasoning, query, output, ctx_obj)
        return

    # Interactive chat
    if not isinstance(output, HumanSink):
        output = HumanSink()
    _chat_loop(provider, cfg, resolved_model, reasoning, use_skills=skills, output=output)


@app.command("sessions")
def cmd_sessions_sub() -> None:
    """List saved conversations."""
    _cmd_sessions(HumanSink())


@app.command("config")
def cmd_config_sub() -> None:
    """Show current configuration."""
    cfg = load_config()
    _cmd_config(cfg, HumanSink())


@app.command("tools")
def cmd_tools_sub(
    json_output: bool = typer.Option(False, "--json"),
    skills: bool = typer.Option(True, "--skills/--no-skills"),
) -> None:
    """List available skill tools."""
    output: HumanSink | MachineSink = MachineSink() if json_output else HumanSink()
    _discover_skills(legacy=skills)
    _cmd_tools(output, skills)


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
    output.print("\n  [yellow]Current config:[/yellow]")
    safe_keys = ["interface", "endpoint", "frontendUrl", "defaultModel",
                 "openaiBaseUrl", "openaiContentArray", "systemPrompt"]
    secret_keys = ["authToken", "openaiApiKey", "customHeader"]
    for k in safe_keys:
        if k in cfg and cfg[k]:
            val = str(cfg[k])
            if len(val) > 60:
                val = val[:57] + "..."
            output.print(f"  [cyan]{k:<28}[/cyan] {escape(val)}")
    for k in secret_keys:
        if k in cfg and cfg[k]:
            v = str(cfg[k])
            masked = v[:4] + "****" + v[-4:] if len(v) > 12 else "****"
            output.print(f"  [cyan]{k:<28}[/cyan] {masked}")


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
    output.print("\n  [yellow]Available skills:[/yellow]")
    for s in skills:
        output.print(f"  [cyan]{s.name:<30}[/cyan] {escape(s.description)}")
        if s.args:
            for arg in s.args:
                detail = arg.description
                if not arg.required:
                    detail += f" (default: {arg.default})"
                output.print(f"  {'':30} [dim]{arg.name}[/dim]: {detail}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    app()
