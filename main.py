#!/usr/bin/env python3
"""
TGenie CLI — AI browser agent powered by Chrome CDP.

Usage:
    python main.py                          # interactive chat (default)
    python main.py --skills --debug         # with tools + debug output
    python main.py --skills chat            # same (options before subcommand)
    python main.py sessions                 # list saved conversations
    python main.py config                   # show current config
"""
import sys
import json
from typing import Optional
from enum import Enum

try:
    import typer
    from typer import Option
except ImportError:
    print("  [ERROR] typer not installed. Run: pip install typer")
    sys.exit(1)

import api
import config
import session as sess
import skill_runner
from api import new_msg

# ── Colors ────────────────────────────────────────────────────────────────────

CYAN   = "\033[96m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
GRAY   = "\033[90m"
RED    = "\033[91m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def c(text, color):
    return f"{color}{text}{RESET}"


# ── Enums ─────────────────────────────────────────────────────────────────────

class ReasoningLevel(str, Enum):
    disable = "disable"
    low     = "low"
    medium  = "medium"
    high    = "high"


# ── Typer App ─────────────────────────────────────────────────────────────────

app = typer.Typer(
    name="tgenie",
    help="TGenie CLI — AI browser agent powered by Chrome CDP",
    add_completion=True,
    no_args_is_help=False,
)


# ── Display helpers ───────────────────────────────────────────────────────────

def read_input():
    try:
        line = input(c("  You > ", CYAN))
    except EOFError:
        return "/exit"
    if line.strip() == '"""':
        print(c('  (multiline - type """ to send)', GRAY))
        lines = []
        while True:
            try:
                l = input(c("  ... ", CYAN))
            except EOFError:
                break
            if l == '"""':
                break
            lines.append(l)
        return "\n".join(lines)
    return line


def print_banner(session, use_skills, reasoning="disable"):
    print()
    print(c("  +======================================+", CYAN))
    print(c("  |        TGenie CLI  v3.0              |", CYAN))
    print(c("  +======================================+", CYAN))
    print()
    print_session_info(session, use_skills, reasoning)


def print_session_info(session, use_skills=False, reasoning="disable"):
    turns = sum(1 for m in session["history"] if m["role"] == "user")
    print(c(f"  Session : {session['title']}", GRAY))
    print(c(f"  Turns   : {turns}", GRAY))
    print(c(f"  Reasoning: {reasoning}", GRAY))
    if use_skills:
        from skills import ALL_SKILLS
        print(c(f"  Skills  : {len(ALL_SKILLS)} enabled", GREEN))
    print(c("  ──────────────────────────────────────", GRAY))


def print_help():
    cmds = [
        ("/new",         "Start a new conversation"),
        ("/sessions",    "List saved conversations"),
        ("/load <n>",    "Load conversation by number"),
        ("/history",     "Show current conversation"),
        ("/skills",      "List available skills/tools"),
        ("/clear",       "Clear current conversation"),
        ('"""',          "Multiline input mode"),
        ("/help",        "Show this help"),
        ("/reasoning",   "Toggle reasoning: disable/low/medium/high"),
        ("/renew",       "Refresh auth token"),
        ("/exit",        "Quit"),
    ]
    print()
    print(c("  Commands:", YELLOW))
    for cmd, desc in cmds:
        print(f"  {c(cmd, CYAN):<30} {desc}")
    print()


def print_history(session):
    print()
    print(c(f"  == {session['title']} ==", YELLOW))
    visible = [m for m in session["history"] if m["role"] != "system"]
    if not visible:
        print(c("  (empty)", GRAY))
    for msg in visible:
        text = msg["content"][0]["text"]
        role = msg["role"]
        # FIX M1: check tool result BEFORE generic user check
        if role == "user" and text.startswith("[Tool result:"):
            print(c("  [Tool] ", YELLOW) + text[:120] + ("..." if len(text) > 120 else ""))
        elif role == "user":
            print(c("  [You]  ", CYAN) + text)
        elif role == "assistant":
            print(c("  [AI]   ", GREEN) + text)
        print(c("  ──────────────────────────────────────", GRAY))
    print()


def print_skills_list():
    from skills import ALL_SKILLS
    print()
    print(c("  Available skills:", YELLOW))
    for s in ALL_SKILLS:
        print(f"  {c(s.name, CYAN):<35} {s.description}")
        if s.args:
            for arg in s.args:
                detail = f"{arg.description}"
                if not arg.required:
                    detail += f" (default: {arg.default})"
                print(f"  {'':35} {c(arg.name, GRAY)}: {detail}")
    print()


def print_sessions_list(sessions):
    print()
    if not sessions:
        print(c("  No saved sessions yet.", GRAY))
        print()
        return
    print(c("  Saved sessions:", YELLOW))
    for i, s in enumerate(sessions, 1):
        turns = f"{s['turns']} turns"
        created = s.get('created', '')[:15]
        print(f"  {c(str(i), CYAN)}. [{created}] {s['title'][:40]:<42} {c(turns, GRAY)}")
    print()


# ── Core logic ────────────────────────────────────────────────────────────────

def build_sys_prompt(cfg, use_skills):
    if use_skills:
        base = cfg.get("systemPrompt", "")
        skill_prompt = skill_runner.build_system_prompt()
        return f"{base}\n\n{skill_prompt}".strip() if base else skill_prompt
    return cfg.get("systemPrompt", "")


def cmd_new(cfg, use_skills, reasoning="disable"):
    session = sess.new_session(build_sys_prompt(cfg, use_skills))
    print()
    print(c("  New conversation started.", GREEN))
    print_session_info(session, use_skills, reasoning)
    return session


# FIX L1: remove unused `cfg` parameter
def cmd_load(args, use_skills):
    sessions = sess.list_sessions()
    print_sessions_list(sessions)
    if not sessions:
        return None
    if not args:
        try:
            raw = input(c("  Load number > ", CYAN)).strip()
            n = int(raw)
        except (ValueError, EOFError):
            return None
    else:
        try:
            n = int(args[0])
        except ValueError:
            print(c("  Usage: /load <number>", RED))
            return None
    if n < 1 or n > len(sessions):
        print(c(f"  Invalid: {n}", RED))
        return None
    loaded = sess.load_session(sessions[n - 1]["filename"])
    print(c(f"  Loaded: {loaded['title']}", GREEN))
    print_session_info(loaded, use_skills)
    return loaded


MAX_TOOL_LOOPS = 15


def _normalize_result(result) -> str:
    """FIX H1: normalize run_tool() return value to str."""
    if result is None:
        return ""
    if not isinstance(result, str):
        return str(result)
    return result


def _normalize_tool_args(tool_call: dict) -> dict:
    """FIX H2: ensure tool args is always a dict."""
    args = tool_call.get("args")
    if isinstance(args, dict):
        return args
    return {}


def send_with_tools(cfg, session, model, reasoning):
    _last_memory = ""
    recent_actions = []

    for loop in range(MAX_TOOL_LOOPS):
        reply = api.send(cfg, session["history"], model, reasoning)
        if not reply:
            return None

        tool_call = skill_runner.parse_tool_call(reply)
        if not tool_call:
            return reply

        tool_name = tool_call.get("tool", "?")
        tool_args = _normalize_tool_args(tool_call)  # FIX H2

        # Loop detection — FIX H2: use json.dumps for canonical key
        action_key = tool_name + json.dumps(tool_args, sort_keys=True, default=str)
        recent_actions.append(action_key)
        if len(recent_actions) > 20:
            recent_actions.pop(0)
        if recent_actions.count(action_key) >= 5:
            return f"Loop detected: repeating '{tool_name}' too many times. Please try a different approach."

        print(c(f"  [Tool] {tool_name}", YELLOW), end="")
        if tool_args:
            args_str = ", ".join(f"{k}={repr(v)}" for k, v in tool_args.items())
            print(c(f"({args_str})", GRAY))
        else:
            print()

        result = _normalize_result(skill_runner.run_tool(tool_call))  # FIX H1
        session["history"].append(new_msg("assistant", reply))

        # Screenshot: send as files attachment on next request
        if result.startswith("__SCREENSHOT__:"):
            # FIX M2: wrap screenshot parsing in try/except
            try:
                _, filename, b64_data = result.split(":", 2)
                import base64 as _b64
                img_bytes = _b64.b64decode(b64_data)
            except (ValueError, Exception) as e:
                print(c(f"  [ERROR] Invalid screenshot payload: {e}", RED))
                err_msg = f"Screenshot capture failed: {e}"
                mem_prefix = f"[Previous step memory]: {_last_memory}\n\n" if _last_memory else ""
                session["history"].append(
                    new_msg("user", f"{mem_prefix}[Tool result: {tool_name}]\n{err_msg}\n\nPlease continue.")
                )
                sess.save_session(session)
                continue

            print(c(f"  [Screenshot] {filename} ({len(img_bytes)//1024}KB) — sending to AI", YELLOW))
            mem_prefix = f"[Previous step memory]: {_last_memory}\n\n" if _last_memory else ""
            session["history"].append(
                new_msg("user", f"{mem_prefix}I took a screenshot of the current page. Please analyze what you see in the image and continue.")
            )
            _last_memory = tool_call.get("memory", "")
            sess.save_session(session)
            try:
                img_reply = api.send(
                    cfg, session["history"], model, reasoning,
                    files=[{"filename": filename, "content_type": "image/png", "data": img_bytes}]
                )
            except Exception as e:
                print(c(f"  [ERROR] Screenshot send failed: {e}", RED))
                img_reply = None

            if img_reply and img_reply.strip():
                tool_call2 = skill_runner.parse_tool_call(img_reply)
                if not tool_call2:
                    return img_reply
                # FIX C2: actually execute tool_call2, don't just parse and discard
                session["history"].append(new_msg("assistant", img_reply))
                tool_name2 = tool_call2.get("tool", "?")
                tool_args2 = _normalize_tool_args(tool_call2)
                print(c(f"  [Tool] {tool_name2}", YELLOW))
                result2 = _normalize_result(skill_runner.run_tool(tool_call2))
                preview2 = result2[:100] + ("..." if len(result2) > 100 else "")
                print(c(f"  [Result] {preview2}", GRAY))
                mem_prefix2 = f"[Previous step memory]: {_last_memory}\n\n" if _last_memory else ""
                session["history"].append(
                    new_msg("user", f"{mem_prefix2}[Tool result: {tool_name2}]\n{result2}\n\nPlease continue based on this result.")
                )
                _last_memory = tool_call2.get("memory", "")
                sess.save_session(session)
            else:
                print(c(f"  [ERROR] AI returned empty (model may lack vision support or image too large)", RED))
            continue
        else:
            preview = result[:100] + ("..." if len(result) > 100 else "")
            print(c(f"  [Result] {preview}", GRAY))
            mem_prefix = f"[Previous step memory]: {_last_memory}\n\n" if _last_memory else ""
            session["history"].append(
                new_msg("user", f"{mem_prefix}[Tool result: {tool_name}]\n{result}\n\nPlease continue based on this result.")
            )
            _last_memory = tool_call.get("memory", "")

        sess.save_session(session)

    return c("(max tool loops reached)", RED)


def chat_loop(cfg, model, reasoning, use_skills):
    current_reasoning = reasoning
    session = sess.new_session(build_sys_prompt(cfg, use_skills))
    print_banner(session, use_skills, reasoning)
    print_help()

    while True:
        print()
        user_input = read_input()
        if not user_input.strip():
            continue

        parts = user_input.strip().split()
        cmd   = parts[0].lower()
        args  = parts[1:]

        if cmd == "/exit":
            if any(m["role"] == "user" for m in session["history"]):
                sess.save_session(session)
                print(c(f"  Saved: {session['title']}", GRAY))
            print(c("  Goodbye!", YELLOW))
            try:
                from skills._cdp import close_shared_cdp
                close_shared_cdp()
            except Exception:
                pass
            break

        elif cmd == "/new":
            if any(m["role"] == "user" for m in session["history"]):
                sess.save_session(session)
                print(c(f"  Saved: {session['title']}", GRAY))
            session = cmd_new(cfg, use_skills, reasoning=current_reasoning)

        elif cmd == "/sessions":
            print_sessions_list(sess.list_sessions())

        elif cmd == "/load":
            loaded = cmd_load(args, use_skills)  # FIX L1: removed cfg
            if loaded:
                session = loaded

        elif cmd == "/history":
            print_history(session)

        elif cmd == "/skills":
            if use_skills:
                print_skills_list()
            else:
                print(c("  Skills not enabled. Restart with --skills", YELLOW))

        elif cmd == "/clear":
            session["history"] = []
            sys_p = build_sys_prompt(cfg, use_skills)
            if sys_p:
                session["history"].append(new_msg("system", sys_p))
            print(c("  Cleared.", GREEN))

        elif cmd == "/reasoning":
            levels = ["disable", "low", "medium", "high"]
            if args and args[0] in levels:
                current_reasoning = args[0]
                print(c(f"  Reasoning set to: {current_reasoning}", GREEN))
            else:
                idx = levels.index(current_reasoning) if current_reasoning in levels else 0
                nxt = levels[(idx + 1) % len(levels)]
                current_reasoning = nxt
                print(c(f"  Reasoning: {levels[idx]} -> {nxt}", GREEN))

        elif cmd == "/renew":
            print(c("  Refreshing token...", YELLOW))
            import subprocess, sys as _sys
            result = subprocess.run([_sys.executable, "grab_auth.py"])
            if result.returncode == 0:
                new_cfg = config.load()
                cfg.update(new_cfg)
                print(c("  [OK] Token refreshed.", GREEN))
            else:
                print(c("  [ERROR] Token refresh failed.", RED))

        elif cmd == "/help":
            print_help()

        elif cmd.startswith("/"):
            print(c(f"  Unknown: {cmd}. Type /help.", RED))

        else:
            session["history"].append(new_msg("user", user_input))
            if sum(1 for m in session["history"] if m["role"] == "user") == 1:
                sess.update_title(session, user_input)

            print(c("  AI thinking...", GRAY))
            try:
                reply = send_with_tools(cfg, session, model, current_reasoning)
            except Exception as e:
                print(c(f"  [ERROR] {e}", RED))
                session["history"].pop()
                continue

            if reply:
                session["history"].append(new_msg("assistant", reply))
                sess.save_session(session)
                print()
                print(c("  AI > ", GREEN) + reply)
            else:
                print(c("  [ERROR] Empty response", RED))
                session["history"].pop()


# ── Commands ──────────────────────────────────────────────────────────────────

# FIX C1: chat command no longer declares its own options.
# All global options (--model, --reasoning, --skills, --debug) are declared
# ONLY on the root callback, stored in ctx.obj, and read here.
# Usage: python main.py [OPTIONS] [SUBCOMMAND]
#   python main.py --skills          → chat with skills
#   python main.py --skills chat     → same
#   python main.py chat              → chat with defaults
@app.command()
def chat(ctx: typer.Context):
    """Start an interactive AI chat session (default command)."""
    ctx.ensure_object(dict)
    model      = ctx.obj.get("model")
    reasoning  = ctx.obj.get("reasoning", ReasoningLevel.disable)
    use_skills = ctx.obj.get("skills", False)
    debug      = ctx.obj.get("debug", False)

    if debug:
        api.DEBUG = True
        print(c("  [DEBUG MODE] HTTP request/response will be printed", YELLOW))

    cfg = config.load()

    if cfg.get("interface", "tgenie") == "tgenie" and not cfg.get("authToken"):
        print(c("  [ERROR] No auth token. Run: python grab_auth.py", RED))
        raise typer.Exit(1)

    resolved_model = model or cfg.get("defaultModel", "gemini-2.5-flash")
    reasoning_val  = reasoning.value if isinstance(reasoning, ReasoningLevel) else reasoning
    chat_loop(cfg, resolved_model, reasoning_val, use_skills=use_skills)


@app.command()
def sessions():
    """List all saved conversations."""
    saved = sess.list_sessions()
    print_sessions_list(saved)


@app.command(name="config")
def show_config():
    """Show current configuration (without sensitive values)."""
    cfg = config.load()
    print()
    print(c("  Current config:", YELLOW))
    print(c("  ──────────────────────────────────────", GRAY))
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
            print(f"  {c(k, CYAN):<30} {val}")
    for k in secret_keys:
        if k in cfg and cfg[k]:
            val = cfg[k]
            masked = val[:4] + "****" + val[-4:] if len(val) > 12 else "****"
            print(f"  {c(k, CYAN):<30} {masked}")
    print()


@app.command()
def renew():
    """Refresh TGenie auth token (runs grab_auth.py)."""
    import subprocess
    print(c("  Refreshing token...", YELLOW))
    result = subprocess.run([sys.executable, "grab_auth.py"])
    if result.returncode == 0:
        print(c("  [OK] Token refreshed.", GREEN))
        raise typer.Exit(0)  # FIX L2: explicit exit code
    else:
        print(c("  [ERROR] Token refresh failed.", RED))
        raise typer.Exit(1)


# FIX M3: guard skills import in tools command
@app.command()
def tools():
    """List all available skill tools."""
    try:
        print_skills_list()
    except ImportError as e:
        print(c(f"  [ERROR] Skills not available: {e}", RED))
        raise typer.Exit(1)


# FIX C1: root callback is the single source of truth for global options.
# Stores values in ctx.obj so subcommands (chat) can read them.
@app.callback(invoke_without_command=True)
def default(
    ctx: typer.Context,
    model: Optional[str] = Option(
        None, "-m", "--model",
        help="Model name (overrides config defaultModel)"),
    reasoning: ReasoningLevel = Option(
        ReasoningLevel.disable, "-r", "--reasoning",
        help="Reasoning effort level"),
    skills: bool = Option(
        False, "-s", "--skills",
        help="Enable browser/file skill tools"),
    debug: bool = Option(
        False, "-d", "--debug",
        help="Print raw HTTP request/response for debugging"),
):
    """TGenie CLI — global options apply to all subcommands."""
    ctx.ensure_object(dict)
    ctx.obj["model"]     = model
    ctx.obj["reasoning"] = reasoning
    ctx.obj["skills"]    = skills
    ctx.obj["debug"]     = debug
    if ctx.invoked_subcommand is None:
        ctx.invoke(chat)


if __name__ == "__main__":
    app()
