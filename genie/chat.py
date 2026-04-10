"""genie/chat.py — Chat loop and tool-execution pipeline.

Owns the interactive conversation loop and the send-with-tools machinery:

  _normalize_result   pure helper: any → str
  _run_tool_call      dispatch a single tool call through SkillRegistry
  _send_with_tools    tool loop (up to MAX_TOOL_LOOPS iterations)
  _do_send            single user-turn orchestration
  _chat_loop          REPL: reads input, dispatches commands, calls _do_send

Does NOT import from genie.cli — receives build_prompt as a callable to
avoid circular dependencies.
"""
from __future__ import annotations

import json
import subprocess
import sys
from typing import Callable

import requests

from genie.core.registry import SkillRegistry
from genie.core.tool_call import normalize_result, parse_tool_call
from genie.output.human import HumanSink
from genie.output.machine import MachineSink
from genie.session.manager import (
    list_sessions, load_session, new_msg, new_session,
    save_session, update_title,
)

MAX_TOOL_LOOPS = 15
REASONING_LEVELS = ["disable", "low", "medium", "high"]


# ── Model listing helper ────────────────────────────────────────────────────

def _is_ollama(cfg: dict) -> bool:
    return cfg.get("interface") == "openai" and "localhost:11434" in cfg.get("openaiBaseUrl", "")


def _get_ollama_models(cfg: dict) -> list[str] | None:
    """Return sorted list of Ollama model names, or None if not Ollama / unreachable."""
    if not _is_ollama(cfg):
        return None
    try:
        resp = requests.get("http://localhost:11434/api/tags", timeout=5)
        resp.raise_for_status()
        return sorted(m["name"] for m in resp.json().get("models", []))
    except Exception:
        return None


def _list_models(cfg: dict, output, current_model: str = "") -> None:
    """Print available models with active marker."""
    models = _get_ollama_models(cfg)
    if models is not None:
        output.print("  [cyan]Available Ollama models:[/cyan]")
        for name in models:
            marker = "[green]●[/green] " if name == current_model else "  "
            output.print(f"  {marker}{name}")
        if not models:
            output.print("  [yellow]No models found in Ollama.[/yellow]")
    else:
        default_model = cfg.get("defaultModel", "")
        if default_model:
            output.print(f"  [cyan]Configured model:[/cyan] {default_model}")
        output.print("  [dim]Model listing is supported for Ollama only.[/dim]")


def _validate_model(cfg: dict, model_name: str) -> tuple[bool, str]:
    """Check if model_name is available. Returns (valid, message)."""
    models = _get_ollama_models(cfg)
    if models is None:
        # Non-Ollama provider — can't validate, allow anything
        return True, ""
    if model_name in models:
        return True, ""
    return False, f"Model '{model_name}' not found. Run /model list to see available models."


# ── Pure helpers ──────────────────────────────────────────────────────────────

def _run_tool_call(tool_call: dict, ctx) -> str:
    name = tool_call.get("tool") or ""
    args = tool_call.get("args") or {}
    if not isinstance(args, dict):
        args = {}
    return SkillRegistry.run_tool(name, args, ctx)


# ── Tool loop ─────────────────────────────────────────────────────────────────

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

        result = normalize_result(_run_tool_call(tool_call, ctx))
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
                tool_args2 = tool_call2.get("args") or {}
                if not isinstance(tool_args2, dict):
                    tool_args2 = {}
                if isinstance(output, HumanSink):
                    output.tool_call(tool_name2, tool_args2)
                else:
                    output.progress(f"[Tool] {tool_name2}")
                result2 = normalize_result(_run_tool_call(tool_call2, ctx))
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


# ── Single turn ───────────────────────────────────────────────────────────────

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


# ── Interactive REPL ──────────────────────────────────────────────────────────

def _chat_loop(
    provider,
    cfg: dict,
    model: str,
    reasoning: str,
    use_skills: bool,
    output: HumanSink,
    build_prompt: Callable[[bool], str],
) -> None:
    """Interactive chat REPL.

    build_prompt(use_skills) → system prompt string; passed in from cli.py
    to keep this module free of cli imports.
    """
    from genie.core.context import SkillContext
    from genie.input import _read_editor_mode, _read_input, _read_paste_mode
    from genie.runtime.autoresearch_cli import _run_autoresearch
    from rich.markup import escape

    ctx = SkillContext(provider=provider, output=output, config=cfg)
    current_reasoning = reasoning
    session = new_session(build_prompt(use_skills))

    output.print("")
    output.print("  [bold cyan]genie[/bold cyan] [dim]— plugin-based AI agent[/dim]")
    output.print("")
    output.kv("model", model)
    output.kv("skills", "enabled" if use_skills else "disabled")
    try:
        from genie.skills.trino_query.connection import status_line
        output.kv("trino", status_line())
    except Exception:
        pass
    output.print("")
    output.print("  [dim]/help for commands · /exit to quit[/dim]")
    output.print("")

    while True:
        user_input = _read_input()
        if not user_input.strip():
            continue

        parts = user_input.strip().split()
        cmd = parts[0].lower()
        args = parts[1:]

        if cmd == "/exit":
            if any(m["role"] == "user" for m in session["history"]):

                save_session(session)

                output.print(f"  [dim]Saved: {escape(session['title'])}[/dim]")

            output.print("  [yellow]Goodbye![/yellow]")

            try:

                from genie.skills.browser.cdp import close_shared_cdp

                close_shared_cdp()

            except Exception:

                pass

            break


        elif cmd == "/new":
            if any(m["role"] == "user" for m in session["history"]):

                save_session(session)

                output.print(f"  [dim]Saved: {escape(session['title'])}[/dim]")

            session = new_session(build_prompt(use_skills))

            output.print("  [green]New conversation started.[/green]")


        elif cmd == "/sessions":
            sessions = list_sessions()

            if not sessions:

                output.print("  [dim]No saved sessions yet.[/dim]")

            else:

                for i, s in enumerate(sessions, 1):

                    output.print(

                        f"  [cyan]{i}[/cyan]. [{s.get('created','')[:15]}] "

                        f"{escape(s['title'][:40]):<42} [dim]{s['turns']} turns[/dim]"

                    )


        elif cmd == "/load":
            sessions = list_sessions()

            if not sessions:

                output.print("  [dim]No sessions.[/dim]")

            elif args:

                # Direct: /load <number> — no interactive prompt needed.
                try:

                    n = int(args[0])

                    if 1 <= n <= len(sessions):

                        session = load_session(sessions[n - 1]["filename"])

                        output.print(f"  [green]Loaded: {escape(session['title'])}[/green]")

                    else:

                        output.print(f"  [red]Session {n} not found (1–{len(sessions)} available).[/red]")

                except ValueError:

                    output.print("  [dim]Usage: /load <number>  (use /sessions to list)[/dim]")

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


        elif cmd == "/history":
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


        elif cmd in ("/skills", "/tools"):
            skills = SkillRegistry.all()

            if not skills:

                output.print("  [dim]No skills registered.[/dim]")

            else:

                for s in skills:

                    output.print(f"  [cyan]{s.name:<30}[/cyan] {s.description}")


        elif cmd == "/clear":
            session["history"] = []

            sys_p = build_prompt(use_skills)

            if sys_p:

                session["history"].append(new_msg("system", sys_p))

            output.print("  [green]Cleared.[/green]")


        elif cmd == "/undo":
            # Remove the last user+assistant exchange (skip system messages).
            history = session["history"]
            user_indices = [i for i, m in enumerate(history) if m["role"] == "user"]
            if not user_indices:
                output.print("  [dim]Nothing to undo.[/dim]")
            else:
                last_user = user_indices[-1]
                # Drop everything from the last user message onward.
                session["history"] = history[:last_user]
                output.print("  [green]Last exchange removed.[/green]")


        elif cmd == "/reasoning":
            if args and args[0] in REASONING_LEVELS:

                current_reasoning = args[0]

            else:

                idx = (

                    REASONING_LEVELS.index(current_reasoning)

                    if current_reasoning in REASONING_LEVELS

                    else 0

                )

                current_reasoning = REASONING_LEVELS[(idx + 1) % len(REASONING_LEVELS)]

            output.print(f"  [green]Reasoning: {current_reasoning}[/green]")


        elif cmd == "/renew":
            output.print("  [yellow]Refreshing token...[/yellow]")

            result = subprocess.run([sys.executable, "grab_auth.py"])

            if result.returncode == 0:

                from genie.core.config import load as load_config

                cfg.update(load_config())

                output.print("  [green][OK] Token refreshed.[/green]")

            else:

                output.error("Token refresh failed.")


        elif cmd == "/trino":
            from genie.skills.trino_query.connection import (

                list_profiles, get_active_name, set_active,

                add_profile, remove_profile, status_line, TrinoProfile,

            )

            if not args:

                # Show current status + all profiles

                active = get_active_name()

                profiles = list_profiles()

                output.print(f"\n  [yellow]{status_line()}[/yellow]")

                output.print("")

                for name, p in profiles.items():

                    marker = "[green]●[/green]" if name == active else " "

                    output.print(f"  {marker} [cyan]{name:<15}[/cyan] {p.display_name()}")

                output.print("")

                output.print("  [dim]/trino use <name>     switch profile[/dim]")

                output.print("  [dim]/trino add <name>     add new profile (interactive)[/dim]")

                output.print("  [dim]/trino remove <name>  remove profile[/dim]")

                output.print("  [dim]/trino test           test current connection[/dim]")

            elif args[0] == "use" and len(args) > 1:

                if set_active(args[1]):

                    output.print(f"  [green]Switched to: {args[1]}[/green]")

                    output.print(f"  {status_line()}")

                else:

                    output.print(f"  [red]Profile '{args[1]}' not found[/red]")

            elif args[0] == "add" and len(args) > 1:

                name = args[1]

                output.print(f"  [yellow]Adding profile: {name}[/yellow]")

                try:

                    host = _read_input(f"  Host [localhost] > ").strip() or "localhost"

                    port_s = _read_input(f"  Port [8085] > ").strip() or "8085"

                    user = _read_input(f"  User [trino] > ").strip() or "trino"

                    scheme = _read_input(f"  Scheme [http] > ").strip() or "http"

                    catalog = _read_input(f"  Catalog [iceberg] > ").strip() or "iceberg"

                    schema_name = _read_input(f"  Schema [warehouse] > ").strip() or "warehouse"

                    label = _read_input(f"  Label (optional) > ").strip()

                    add_profile(name, TrinoProfile(

                        host=host, port=int(port_s), user=user,

                        scheme=scheme, catalog=catalog, schema=schema_name, label=label,

                    ))

                    output.print(f"  [green]Profile '{name}' added.[/green]")

                except (EOFError, KeyboardInterrupt):

                    output.print("  [dim]Cancelled.[/dim]")

            elif args[0] == "remove" and len(args) > 1:

                if remove_profile(args[1]):

                    output.print(f"  [green]Removed: {args[1]}[/green]")

                else:

                    output.print(f"  [red]Cannot remove (active or not found)[/red]")

            elif args[0] == "test":

                from genie.skills.trino_query.connection import get_active_profile

                try:

                    p = get_active_profile()

                    conn = p.connect()

                    cur = conn.cursor()

                    cur.execute("SELECT 1")

                    cur.fetchall()

                    conn.close()

                    output.print(f"  [green]✓ Connected to {p.display_name()}[/green]")

                except Exception as exc:

                    output.print(f"  [red]✗ Connection failed: {exc}[/red]")

            else:

                output.print("  [dim]Usage: /trino [use|add|remove|test] [name][/dim]")


        elif cmd == "/trino-research":
            from genie.skills.trino_query.research import run_trino_research

            # Parse optional flags: --file, --metric, --iterations, --runs
            kwargs = {}
            i = 0
            while i < len(args):
                if args[i] == "--file" and i + 1 < len(args):
                    kwargs["sql_file"] = args[i + 1]
                    i += 2
                elif args[i] == "--metric" and i + 1 < len(args):
                    kwargs["metric"] = args[i + 1]
                    i += 2
                elif args[i] == "--iterations" and i + 1 < len(args):
                    kwargs["iterations"] = int(args[i + 1])
                    i += 2
                elif args[i] == "--runs" and i + 1 < len(args):
                    kwargs["runs"] = int(args[i + 1])
                    i += 2
                else:
                    i += 1

            run_trino_research(provider, cfg, model, current_reasoning, output, build_prompt, **kwargs)


        elif cmd == "/autoresearch":
            if not use_skills:

                output.print("  [yellow]Skills must be enabled. Restart with --skills[/yellow]")

            else:

                _run_autoresearch(provider, cfg, model, current_reasoning, output, build_prompt)


        elif cmd == "/paste":
            pasted = _read_paste_mode()

            if pasted.strip():

                _do_send(provider, session, model, current_reasoning, pasted, output, ctx)


        elif cmd == "/editor":
            edited = _read_editor_mode()

            if edited.strip():

                _do_send(provider, session, model, current_reasoning, edited, output, ctx)


        elif cmd == "/model":
            if not args:
                output.print(f"  [cyan]Model:[/cyan] {model}")
            elif args[0] == "list":
                _list_models(cfg, output, current_model=model)
            else:
                new_model = args[0]
                valid, err = _validate_model(cfg, new_model)
                if not valid:
                    output.print(f"  [red]{err}[/red]")
                else:
                    model = new_model
                    output.print(f"  [green]Switched model →[/green] {model}")


        elif cmd == "/stats":
            visible = [m for m in session["history"] if m["role"] != "system"]
            user_count = sum(1 for m in visible if m["role"] == "user")
            ai_count = sum(1 for m in visible if m["role"] == "assistant")
            total_chars = sum(
                len(m["content"][0]["text"])
                for m in session["history"]
                if m.get("content") and m["content"]
            )
            approx_tokens = total_chars // 4
            output.print("")
            output.rule("session stats")
            output.kv("title",   session["title"])
            output.kv("turns",   str(user_count))
            output.kv("ai msgs", str(ai_count))
            output.kv("~tokens", f"{approx_tokens:,}")
            output.kv("model",   model)
            output.kv("reason",  current_reasoning)
            output.print("")


        elif cmd == "/export":
            visible = [m for m in session["history"] if m["role"] != "system"]
            if not visible:
                output.print("  [dim]Nothing to export.[/dim]")
            else:
                import time as _time
                from genie.session.manager import SESSIONS_DIR
                export_dir = SESSIONS_DIR.parent / "exports"
                export_dir.mkdir(exist_ok=True)
                ts = _time.strftime("%Y%m%d_%H%M%S")
                from genie.session.manager import slug as _slug
                fname = f"{ts}_{_slug(session['title'])}.md"
                out_path = export_dir / fname
                lines: list[str] = [f"# {session['title']}\n"]
                for m in visible:
                    role_label = "**You**" if m["role"] == "user" else "**Genie**"
                    text = m["content"][0]["text"] if m.get("content") else ""
                    # Skip internal tool-result messages from export
                    if m["role"] == "user" and text.startswith("[Tool result:"):
                        continue
                    lines.append(f"\n---\n\n{role_label}\n\n{text}\n")
                out_path.write_text("\n".join(lines), encoding="utf-8")
                output.print(f"  [green]Exported:[/green] {out_path}")


        elif cmd == "/help":
            cmds = [

                ("/new",          "Start a new conversation"),

                ("/sessions",     "List saved conversations"),

                ("/load [n]",     "Load conversation (direct: /load 2)"),

                ("/history",      "Show current conversation"),

                ("/skills",       "List available skills/tools"),

                ("/clear",        "Clear current conversation"),

                ("/undo",         "Remove last exchange from history"),

                ("/stats",        "Show session stats (turns, ~tokens, model)"),

                ("/export",       "Export conversation to markdown file"),

                ("/paste",        "Multiline paste mode (Ctrl-D to send)"),

                ("/editor",       "Open editor for input"),

                ("/autoresearch", "Start autonomous iteration loop"),

                ("/trino",        "Trino connection manager"),

                ("/trino-research","Optimize SQL via autoresearch loop"),

                ("/model",        "Show current model"),

                ("/model <name>", "Switch to a different model"),

                ("/model list",   "List available models"),

                ("/reasoning",    "Toggle reasoning: disable/low/medium/high"),

                ("/renew",        "Refresh auth token"),

                ("/exit",         "Quit"),

            ]

            for c, d in cmds:

                output.print(f"  [cyan]{c:<22}[/cyan] {d}")


        elif cmd.startswith("/"):
            output.print(f"  [red]Unknown: {escape(cmd)}. Type /help.[/red]")


        else:
            _do_send(provider, session, model, current_reasoning, user_input, output, ctx)

