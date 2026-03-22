#!/usr/bin/env python3
import sys
import argparse
import api
import config
import session as sess
import skill_runner
from api import new_msg

CYAN   = "\033[96m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
GRAY   = "\033[90m"
RED    = "\033[91m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def c(text, color):
    return f"{color}{text}{RESET}"

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
    print(c("  |        TGenie CLI  v2.0              |", CYAN))
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
        if role == "user":
            print(c("  [You]  ", CYAN) + text)
        elif role == "assistant":
            print(c("  [AI]   ", GREEN) + text)
        elif role == "user" and text.startswith("[Tool result:"):
            print(c("  [Tool] ", YELLOW) + text[:120] + ("..." if len(text) > 120 else ""))
        print(c("  ──────────────────────────────────────", GRAY))
    print()

def print_skills():
    from skills import ALL_SKILLS
    print()
    print(c("  Available skills:", YELLOW))
    for s in ALL_SKILLS:
        args = ", ".join(s.args_schema.keys()) or "—"
        print(f"  {c(s.name, CYAN):<35} {s.description}")
        if s.args_schema:
            for k, v in s.args_schema.items():
                print(f"  {'':35} {c(k, GRAY)}: {v}")
    print()

def print_sessions(sessions):
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

def cmd_load(args, cfg, use_skills):
    sessions = sess.list_sessions()
    print_sessions(sessions)
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



def send_with_tools(cfg, session, model, reasoning):
    _last_memory: str = ""
    recent_actions: list = []

    for loop in range(MAX_TOOL_LOOPS):
        reply = api.send(cfg, session["history"], model, reasoning)
        if not reply:
            return None

        tool_call = skill_runner.parse_tool_call(reply)
        if not tool_call:
            return reply

        tool_name = tool_call.get("tool", "?")
        tool_args = tool_call.get("args", {})

        # Loop detection
        action_key = tool_name + str(sorted(tool_args.items()))
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

        result = skill_runner.run_tool(tool_call)

        session["history"].append(new_msg("assistant", reply))

        # Screenshot: send as files attachment on next request
        if result.startswith("__SCREENSHOT__:"):
            _, filename, b64_data = result.split(":", 2)
            img_bytes = __import__("base64").b64decode(b64_data)
            print(c(f"  [Screenshot] {filename} ({len(img_bytes)//1024}KB) — sending to AI", YELLOW))
            mem_prefix = f"[Previous step memory]: {_last_memory}\n\n" if _last_memory else ""
            session["history"].append(
                new_msg("user", f"{mem_prefix}I took a screenshot of the current page. Please analyze what you see in the image and continue.")
            )
            _last_memory = tool_call.get("memory", "")
            sess.save_session(session)
            # Send with image attached
            img_reply = api.send(
                cfg, session["history"], model, reasoning,
                files=[{"filename": filename, "content_type": "image/png", "data": img_bytes}]
            )
            if img_reply:
                tool_call2 = skill_runner.parse_tool_call(img_reply)
                if not tool_call2:
                    # AI gave final answer after seeing image
                    return img_reply
                # AI wants more tools — add to history and continue loop
                session["history"].append(new_msg("assistant", img_reply))
                reply  = img_reply
                result = img_reply
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
    current_reasoning = [reasoning]  # mutable so nested funcs can update
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
            session = cmd_new(cfg, use_skills, reasoning=current_reasoning[0])

        elif cmd == "/sessions":
            print_sessions(sess.list_sessions())

        elif cmd == "/load":
            loaded = cmd_load(args, cfg, use_skills)
            if loaded:
                session = loaded

        elif cmd == "/history":
            print_history(session)

        elif cmd == "/skills":
            if use_skills:
                print_skills()
            else:
                print(c("  Skills not enabled. Run with --skills", YELLOW))

        elif cmd == "/clear":
            session["history"] = []
            sys_p = build_sys_prompt(cfg, use_skills)
            if sys_p:
                session["history"].append(new_msg("system", sys_p))
            print(c("  Cleared.", GREEN))

        elif cmd == "/reasoning":
            levels = ["disable", "low", "medium", "high"]
            if args and args[0] in levels:
                current_reasoning[0] = args[0]
                print(c(f"  Reasoning set to: {current_reasoning[0]}", GREEN))
            else:
                cur = current_reasoning[0]
                idx = levels.index(cur) if cur in levels else 0
                nxt = levels[(idx + 1) % len(levels)]
                current_reasoning[0] = nxt
                print(c(f"  Reasoning: {cur} -> {nxt}", GREEN))

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
                reply = send_with_tools(cfg, session, model, current_reasoning[0])
            except Exception as e:
                print(c(f"  [ERROR] {e}", RED))
                # Roll back user message
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


def main():
    parser = argparse.ArgumentParser(description="TGenie CLI")
    parser.add_argument("--model",     "-m", help="Model name")
    parser.add_argument("--reasoning", "-r",
                        choices=["disable", "low", "medium", "high"],
                        default="disable")
    parser.add_argument("--skills",    "-s", action="store_true",
                        help="Enable browser/file skills")
    args = parser.parse_args()

    cfg = config.load()
    if not cfg.get("authToken"):
        print(c("  [ERROR] No auth token. Run: python grab_auth.py", RED))
        sys.exit(1)

    model     = args.model or cfg.get("defaultModel", "gemini-2.5-flash")
    reasoning = args.reasoning

    chat_loop(cfg, model, reasoning, use_skills=args.skills)


if __name__ == "__main__":
    main()
