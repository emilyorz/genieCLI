#!/usr/bin/env python3
"""
TGenie CLI — AI browser agent powered by Chrome CDP.

Usage:
    python main.py                          # interactive chat (default)
    python main.py query.sql               # send file, non-interactive
    cat query.sql | python main.py         # pipe stdin, non-interactive
    python main.py --skills --debug        # with tools + debug output
    python main.py -m gpt-4 -r medium      # custom model + reasoning
    python main.py sessions                # list saved conversations
    python main.py config                  # show current config
"""
import sys
import json
import os
import argparse
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import api
import config
import session as sess
import skill_runner
from api import new_msg

# ── Rich console ──────────────────────────────────────────────────────────────

try:
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.markup import escape
except ImportError:
    print("  [ERROR] rich not installed. Run: pip install rich")
    sys.exit(1)

console = Console(force_terminal=True, highlight=False)

# ── prompt_toolkit ────────────────────────────────────────────────────────────

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.key_binding import KeyBindings
except ImportError:
    print("  [ERROR] prompt_toolkit not installed. Run: pip install prompt_toolkit")
    sys.exit(1)

_history_path = Path.home() / ".tgenie_history"
_prompt_session: Optional["PromptSession"] = None


def _get_prompt_session() -> "PromptSession":
    global _prompt_session
    if _prompt_session is None:
        _prompt_session = PromptSession(history=FileHistory(str(_history_path)))
    return _prompt_session


# ── Constants ─────────────────────────────────────────────────────────────────

REASONING_LEVELS = ["disable", "low", "medium", "high"]

SUBCOMMANDS = {"sessions", "config", "renew", "tools"}


# ── argparse ──────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tgenie",
        description="TGenie CLI — AI browser agent powered by Chrome CDP",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "subcommands:\n"
            "  sessions    List saved conversations\n"
            "  config      Show current configuration\n"
            "  renew       Refresh TGenie auth token\n"
            "  tools       List available skill tools\n"
        ),
    )
    parser.add_argument(
        "-m", "--model",
        default=None,
        help="Model name (overrides config defaultModel)",
    )
    parser.add_argument(
        "-r", "--reasoning",
        default="disable",
        choices=REASONING_LEVELS,
        metavar="LEVEL",
        help="Reasoning effort: disable|low|medium|high (default: disable)",
    )
    parser.add_argument(
        "-s", "--skills",
        action="store_true",
        help="Enable browser/file skill tools",
    )
    parser.add_argument(
        "-d", "--debug",
        action="store_true",
        help="Print raw HTTP request/response for debugging",
    )
    parser.add_argument(
        "target",
        nargs="?",
        default=None,
        metavar="file.sql | subcommand",
        help="SQL file to send (non-interactive), or subcommand (sessions/config/renew/tools)",
    )
    return parser


# ── Input helpers ─────────────────────────────────────────────────────────────

def read_input() -> str:
    """Single-line input with history and bracketed-paste support."""
    try:
        return _get_prompt_session().prompt("  You > ")
    except EOFError:
        return "/exit"
    except KeyboardInterrupt:
        return ""


def read_paste_mode() -> str:
    """Multiline paste mode — Ctrl-D submits, Ctrl-C cancels."""
    kb = KeyBindings()

    @kb.add("c-d", eager=True)
    def _submit(event):
        event.current_buffer.validate_and_handle()

    console.print("  [dim](paste mode — Ctrl-D to send, Ctrl-C to cancel)[/dim]")
    try:
        return _get_prompt_session().prompt(
            "  ... ",
            multiline=True,
            key_bindings=kb,
        )
    except (KeyboardInterrupt, EOFError):
        return ""


def read_editor_mode() -> str:
    """Open $EDITOR (fallback vim/nano), return file contents after save+close."""
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
        console.print("  [red][ERROR] No editor found. Set $EDITOR, or install vim/nano.[/red]")
        return ""

    with tempfile.NamedTemporaryFile(suffix=".sql", mode="w", delete=False) as f:
        tmpfile = f.name

    try:
        result = subprocess.run(chosen_parts + [tmpfile])
        if result.returncode != 0:
            console.print(f"  [red][ERROR] Editor exited with code {result.returncode} — aborting.[/red]")
            return ""
        content = Path(tmpfile).read_text(encoding="utf-8", errors="replace").strip()
        return content
    except Exception as e:
        console.print(f"  [red][ERROR] Editor failed: {escape(str(e))}[/red]")
        return ""
    finally:
        try:
            os.unlink(tmpfile)
        except OSError:
            pass


# ── Display helpers ───────────────────────────────────────────────────────────

def print_ai_reply(reply: str):
    """Render AI reply as Markdown. Always print directly — pager causes ANSI escape code
    garbage when less/more doesn't support color (no -R flag)."""
    md = Markdown(reply)
    console.print(md)


def print_banner(session: dict, use_skills: bool, reasoning: str = "disable"):
    console.print()
    console.print("  [cyan]+======================================+[/cyan]")
    console.print("  [cyan]|        TGenie CLI  v3.0              |[/cyan]")
    console.print("  [cyan]+======================================+[/cyan]")
    console.print()
    print_session_info(session, use_skills, reasoning)


def print_session_info(session: dict, use_skills: bool = False, reasoning: str = "disable"):
    turns = sum(1 for m in session["history"] if m["role"] == "user")
    console.print(f"  [dim]Session : {escape(session['title'])}[/dim]")
    console.print(f"  [dim]Turns   : {turns}[/dim]")
    console.print(f"  [dim]Reasoning: {reasoning}[/dim]")
    if use_skills:
        from skills import ALL_SKILLS
        console.print(f"  [green]Skills  : {len(ALL_SKILLS)} enabled[/green]")
    console.print("  [dim]──────────────────────────────────────[/dim]")


def print_help():
    cmds = [
        ("/new",        "Start a new conversation"),
        ("/sessions",   "List saved conversations"),
        ("/load <n>",   "Load conversation by number"),
        ("/history",    "Show current conversation"),
        ("/skills",     "List available skills/tools"),
        ("/clear",      "Clear current conversation"),
        ("/paste",      "Multiline paste mode (Ctrl-D to send)"),
        ("/editor",     "Open editor for input (.sql), save & close to send"),
        ("/help",       "Show this help"),
        ("/reasoning",  "Toggle reasoning: disable/low/medium/high"),
        ("/renew",      "Refresh auth token"),
        ("/exit",       "Quit"),
    ]
    console.print()
    console.print("  [yellow]Commands:[/yellow]")
    for cmd, desc in cmds:
        console.print(f"  [cyan]{cmd:<22}[/cyan] {desc}")
    console.print()


def print_history(session: dict):
    console.print()
    console.print(f"  [yellow]== {escape(session['title'])} ==[/yellow]")
    visible = [m for m in session["history"] if m["role"] != "system"]
    if not visible:
        console.print("  [dim](empty)[/dim]")
    for msg in visible:
        text = msg["content"][0]["text"]
        role = msg["role"]
        if role == "user" and text.startswith("[Tool result:"):
            preview = text[:120] + ("..." if len(text) > 120 else "")
            console.print(f"  [yellow][Tool][/yellow]  {escape(preview)}")
        elif role == "user":
            console.print(f"  [cyan][You][/cyan]   {escape(text)}")
        elif role == "assistant":
            console.print(f"  [green][AI][/green]    {escape(text)}")
        console.print("  [dim]──────────────────────────────────────[/dim]")
    console.print()


def print_skills_list():
    from skills import ALL_SKILLS
    console.print()
    console.print("  [yellow]Available skills:[/yellow]")
    for s in ALL_SKILLS:
        console.print(f"  [cyan]{s.name:<30}[/cyan] {s.description}")
        if s.args:
            for arg in s.args:
                detail = arg.description
                if not arg.required:
                    detail += f" (default: {arg.default})"
                console.print(f"  {'':30} [dim]{arg.name}[/dim]: {detail}")
    console.print()


def print_sessions_list(sessions: list):
    console.print()
    if not sessions:
        console.print("  [dim]No saved sessions yet.[/dim]")
        console.print()
        return
    console.print("  [yellow]Saved sessions:[/yellow]")
    for i, s in enumerate(sessions, 1):
        turns = f"{s['turns']} turns"
        created = s.get("created", "")[:15]
        title = escape(s["title"][:40])
        console.print(f"  [cyan]{i}[/cyan]. [{created}] {title:<42} [dim]{turns}[/dim]")
    console.print()


# ── Core logic ────────────────────────────────────────────────────────────────

def build_sys_prompt(cfg: dict, use_skills: bool) -> str:
    base = cfg.get("systemPrompt", "")
    if use_skills:
        skill_prompt = skill_runner.build_system_prompt()
        # config systemPrompt always takes priority; skill tools appended after
        return f"{base}\n\n{skill_prompt}".strip() if base else skill_prompt
    return base


def cmd_new(cfg: dict, use_skills: bool, reasoning: str = "disable") -> dict:
    session = sess.new_session(build_sys_prompt(cfg, use_skills))
    console.print()
    console.print("  [green]New conversation started.[/green]")
    print_session_info(session, use_skills, reasoning)
    return session


def cmd_load(args: list, use_skills: bool) -> Optional[dict]:
    sessions = sess.list_sessions()
    print_sessions_list(sessions)
    if not sessions:
        return None
    if not args:
        try:
            raw = _get_prompt_session().prompt("  Load number > ").strip()
            n = int(raw)
        except (ValueError, EOFError, KeyboardInterrupt):
            return None
    else:
        try:
            n = int(args[0])
        except ValueError:
            console.print("  [red]Usage: /load <number>[/red]")
            return None
    if n < 1 or n > len(sessions):
        console.print(f"  [red]Invalid: {n}[/red]")
        return None
    loaded = sess.load_session(sessions[n - 1]["filename"])
    console.print(f"  [green]Loaded: {escape(loaded['title'])}[/green]")
    print_session_info(loaded, use_skills)
    return loaded


MAX_TOOL_LOOPS = 15


def _normalize_result(result) -> str:
    if result is None:
        return ""
    if not isinstance(result, str):
        return str(result)
    return result


def _normalize_tool_args(tool_call: dict) -> dict:
    args = tool_call.get("args")
    if isinstance(args, dict):
        return args
    return {}


def send_with_tools(cfg: dict, session: dict, model: str, reasoning: str) -> Optional[str]:
    _last_memory = ""
    recent_actions: list = []

    for _loop in range(MAX_TOOL_LOOPS):
        reply = api.send(cfg, session["history"], model, reasoning)
        if not reply:
            return None

        tool_call = skill_runner.parse_tool_call(reply)
        if not tool_call:
            return reply

        tool_name = tool_call.get("tool", "?")
        tool_args = _normalize_tool_args(tool_call)

        action_key = tool_name + json.dumps(tool_args, sort_keys=True, default=str)
        recent_actions.append(action_key)
        if len(recent_actions) > 20:
            recent_actions.pop(0)
        if recent_actions.count(action_key) >= 5:
            return f"Loop detected: repeating '{tool_name}' too many times. Please try a different approach."

        if tool_args:
            args_str = ", ".join(f"{k}={repr(v)}" for k, v in tool_args.items())
            console.print(f"  [yellow][Tool] {escape(tool_name)}[/yellow] [dim]({escape(args_str)})[/dim]")
        else:
            console.print(f"  [yellow][Tool] {escape(tool_name)}[/yellow]")

        result = _normalize_result(skill_runner.run_tool(tool_call))
        session["history"].append(new_msg("assistant", reply))

        if result.startswith("__SCREENSHOT__:"):
            try:
                _, filename, b64_data = result.split(":", 2)
                import base64 as _b64
                img_bytes = _b64.b64decode(b64_data)
            except (ValueError, Exception) as e:
                console.print(f"  [red][ERROR] Invalid screenshot payload: {escape(str(e))}[/red]")
                err_msg = f"Screenshot capture failed: {e}"
                mem_prefix = f"[Previous step memory]: {_last_memory}\n\n" if _last_memory else ""
                session["history"].append(
                    new_msg("user", f"{mem_prefix}[Tool result: {tool_name}]\n{err_msg}\n\nPlease continue.")
                )
                sess.save_session(session)
                continue

            console.print(f"  [yellow][Screenshot] {escape(filename)} ({len(img_bytes)//1024}KB) — sending to AI[/yellow]")
            mem_prefix = f"[Previous step memory]: {_last_memory}\n\n" if _last_memory else ""
            session["history"].append(
                new_msg("user", f"{mem_prefix}I took a screenshot of the current page. Please analyze what you see in the image and continue.")
            )
            _last_memory = tool_call.get("memory", "")
            sess.save_session(session)
            try:
                img_reply = api.send(
                    cfg, session["history"], model, reasoning,
                    files=[{"filename": filename, "content_type": "image/png", "data": img_bytes}],
                )
            except Exception as e:
                console.print(f"  [red][ERROR] Screenshot send failed: {escape(str(e))}[/red]")
                img_reply = None

            if img_reply and img_reply.strip():
                tool_call2 = skill_runner.parse_tool_call(img_reply)
                if not tool_call2:
                    return img_reply
                session["history"].append(new_msg("assistant", img_reply))
                tool_name2 = tool_call2.get("tool", "?")
                tool_args2 = _normalize_tool_args(tool_call2)
                console.print(f"  [yellow][Tool] {escape(tool_name2)}[/yellow]")
                result2 = _normalize_result(skill_runner.run_tool(tool_call2))
                preview2 = result2[:100] + ("..." if len(result2) > 100 else "")
                console.print(f"  [dim][Result] {escape(preview2)}[/dim]")
                mem_prefix2 = f"[Previous step memory]: {_last_memory}\n\n" if _last_memory else ""
                session["history"].append(
                    new_msg("user", f"{mem_prefix2}[Tool result: {tool_name2}]\n{result2}\n\nPlease continue based on this result.")
                )
                _last_memory = tool_call2.get("memory", "")
                sess.save_session(session)
            else:
                console.print("  [red][ERROR] AI returned empty (model may lack vision support or image too large)[/red]")
            continue
        else:
            preview = result[:100] + ("..." if len(result) > 100 else "")
            console.print(f"  [dim][Result] {escape(preview)}[/dim]")
            mem_prefix = f"[Previous step memory]: {_last_memory}\n\n" if _last_memory else ""
            session["history"].append(
                new_msg("user", f"{mem_prefix}[Tool result: {tool_name}]\n{result}\n\nPlease continue based on this result.")
            )
            _last_memory = tool_call.get("memory", "")

        sess.save_session(session)

    console.print("  [red](max tool loops reached)[/red]")
    return None


def _do_send(cfg: dict, session: dict, model: str, reasoning: str, user_input: str):
    """Append user_input, call AI, display response. Mutates session in place."""
    session["history"].append(new_msg("user", user_input))
    if sum(1 for m in session["history"] if m["role"] == "user") == 1:
        sess.update_title(session, user_input)

    console.print("  [dim]AI thinking...[/dim]")
    try:
        reply = send_with_tools(cfg, session, model, reasoning)
    except Exception as e:
        console.print(f"  [red][ERROR] {escape(str(e))}[/red]")
        session["history"].pop()
        return

    if reply:
        session["history"].append(new_msg("assistant", reply))
        sess.save_session(session)
        console.print()
        console.print("  [green]AI >[/green]")
        print_ai_reply(reply)
    else:
        console.print("  [red][ERROR] Empty response[/red]")
        session["history"].pop()


def chat_loop(cfg: dict, model: str, reasoning: str, use_skills: bool):
    current_reasoning = reasoning
    session = sess.new_session(build_sys_prompt(cfg, use_skills))
    print_banner(session, use_skills, reasoning)
    print_help()

    while True:
        console.print()
        user_input = read_input()
        if not user_input.strip():
            continue

        parts = user_input.strip().split()
        cmd = parts[0].lower()
        args = parts[1:]

        if cmd == "/exit":
            if any(m["role"] == "user" for m in session["history"]):
                sess.save_session(session)
                console.print(f"  [dim]Saved: {escape(session['title'])}[/dim]")
            console.print("  [yellow]Goodbye![/yellow]")
            try:
                from skills._cdp import close_shared_cdp
                close_shared_cdp()
            except Exception:
                pass
            break

        elif cmd == "/new":
            if any(m["role"] == "user" for m in session["history"]):
                sess.save_session(session)
                console.print(f"  [dim]Saved: {escape(session['title'])}[/dim]")
            session = cmd_new(cfg, use_skills, reasoning=current_reasoning)

        elif cmd == "/sessions":
            print_sessions_list(sess.list_sessions())

        elif cmd == "/load":
            loaded = cmd_load(args, use_skills)
            if loaded:
                session = loaded

        elif cmd == "/history":
            print_history(session)

        elif cmd == "/skills":
            if use_skills:
                print_skills_list()
            else:
                console.print("  [yellow]Skills not enabled. Restart with --skills[/yellow]")

        elif cmd == "/clear":
            session["history"] = []
            sys_p = build_sys_prompt(cfg, use_skills)
            if sys_p:
                session["history"].append(new_msg("system", sys_p))
            console.print("  [green]Cleared.[/green]")

        elif cmd == "/reasoning":
            if args and args[0] in REASONING_LEVELS:
                current_reasoning = args[0]
                console.print(f"  [green]Reasoning set to: {current_reasoning}[/green]")
            else:
                idx = REASONING_LEVELS.index(current_reasoning) if current_reasoning in REASONING_LEVELS else 0
                nxt = REASONING_LEVELS[(idx + 1) % len(REASONING_LEVELS)]
                current_reasoning = nxt
                console.print(f"  [green]Reasoning: {REASONING_LEVELS[idx]} -> {nxt}[/green]")

        elif cmd == "/renew":
            console.print("  [yellow]Refreshing token...[/yellow]")
            result = subprocess.run([sys.executable, "grab_auth.py"])
            if result.returncode == 0:
                new_cfg = config.load()
                cfg.update(new_cfg)
                console.print("  [green][OK] Token refreshed.[/green]")
            else:
                console.print("  [red][ERROR] Token refresh failed.[/red]")

        elif cmd == "/paste":
            pasted = read_paste_mode()
            if pasted.strip():
                _do_send(cfg, session, model, current_reasoning, pasted)

        elif cmd == "/editor":
            edited = read_editor_mode()
            if edited.strip():
                _do_send(cfg, session, model, current_reasoning, edited)

        elif cmd == "/help":
            print_help()

        elif cmd.startswith("/"):
            console.print(f"  [red]Unknown: {escape(cmd)}. Type /help.[/red]")

        else:
            _do_send(cfg, session, model, current_reasoning, user_input)


def run_single_shot(cfg: dict, model: str, reasoning: str, use_skills: bool, query: str):
    """Send query non-interactively, render response, then exit."""
    session = sess.new_session(build_sys_prompt(cfg, use_skills))
    session["history"].append(new_msg("user", query))

    console.print("  [dim]AI thinking...[/dim]")
    try:
        reply = send_with_tools(cfg, session, model, reasoning)
    except Exception as e:
        console.print(f"  [red][ERROR] {escape(str(e))}[/red]")
        sys.exit(1)

    if reply:
        console.print()
        print_ai_reply(reply)
    else:
        console.print("  [red][ERROR] Empty response[/red]")
        sys.exit(1)


# ── Subcommand handlers ───────────────────────────────────────────────────────

def cmd_sessions():
    print_sessions_list(sess.list_sessions())


def cmd_config():
    cfg = config.load()
    console.print()
    console.print("  [yellow]Current config:[/yellow]")
    console.print("  [dim]──────────────────────────────────────[/dim]")
    safe_keys = [
        "interface", "endpoint", "frontendUrl", "defaultModel",
        "openaiBaseUrl", "openaiContentArray", "systemPrompt",
    ]
    secret_keys = ["authToken", "openaiApiKey", "customHeader"]
    for k in safe_keys:
        if k in cfg and cfg[k]:
            val = cfg[k]
            if isinstance(val, str) and len(val) > 60:
                val = val[:57] + "..."
            console.print(f"  [cyan]{k:<28}[/cyan] {escape(str(val))}")
    for k in secret_keys:
        if k in cfg and cfg[k]:
            val = cfg[k]
            masked = val[:4] + "****" + val[-4:] if len(val) > 12 else "****"
            console.print(f"  [cyan]{k:<28}[/cyan] {masked}")
    console.print()


def cmd_renew():
    console.print("  [yellow]Refreshing token...[/yellow]")
    result = subprocess.run([sys.executable, "grab_auth.py"])
    if result.returncode == 0:
        console.print("  [green][OK] Token refreshed.[/green]")
        sys.exit(0)
    else:
        console.print("  [red][ERROR] Token refresh failed.[/red]")
        sys.exit(1)


def cmd_tools():
    try:
        print_skills_list()
    except ImportError as e:
        console.print(f"  [red][ERROR] Skills not available: {escape(str(e))}[/red]")
        sys.exit(1)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = build_parser()
    args = parser.parse_args()

    # 'chat' is an explicit alias for interactive mode — treat same as no target
    if args.target == "chat":
        args.target = None

    # Handle subcommands that don't need AI/config
    if args.target in SUBCOMMANDS:
        if args.target == "sessions":
            cmd_sessions()
        elif args.target == "config":
            cmd_config()
        elif args.target == "renew":
            cmd_renew()
        elif args.target == "tools":
            cmd_tools()
        return

    if args.debug:
        api.DEBUG = True
        console.print("  [yellow][DEBUG MODE] HTTP request/response will be printed[/yellow]")

    cfg = config.load()

    if cfg.get("interface", "tgenie") == "tgenie" and not cfg.get("authToken"):
        console.print("  [red][ERROR] No auth token. Run: python grab_auth.py[/red]")
        sys.exit(1)

    resolved_model = args.model or cfg.get("defaultModel", "gemini-2.5-flash")
    reasoning_val = args.reasoning

    # Non-interactive: file argument
    if args.target is not None:
        path = Path(args.target)
        if not path.exists():
            console.print(f"  [red][ERROR] File not found: {escape(args.target)}[/red]")
            sys.exit(1)
        try:
            query = path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            console.print(f"  [red][ERROR] Cannot read file: {escape(str(e))}[/red]")
            sys.exit(1)
        run_single_shot(cfg, resolved_model, reasoning_val, args.skills, query)
        return

    # Non-interactive: stdin pipe
    if not sys.stdin.isatty():
        query = sys.stdin.read()
        if not query.strip():
            console.print("  [red][ERROR] Empty stdin[/red]")
            sys.exit(1)
        run_single_shot(cfg, resolved_model, reasoning_val, args.skills, query)
        return

    # Interactive mode
    chat_loop(cfg, resolved_model, reasoning_val, use_skills=args.skills)


if __name__ == "__main__":
    main()
