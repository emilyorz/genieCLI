"""genie/runtime/autoresearch_cli.py — Interactive autoresearch workflow.

Guides the user through goal / scope / verify-command setup, then runs
the autonomous improvement loop via RunManager.  Receives a build_prompt
callable so it can construct its system prompt without importing cli.py
(avoids circular dependency).
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from genie.core.context import SkillContext
from genie.core.registry import SkillRegistry
from genie.core.tool_call import parse_tool_call
from genie.session.manager import new_msg, new_session


def _normalize_result(result) -> str:
    if result is None:
        return ""
    return result if isinstance(result, str) else str(result)


def _is_tool_failure(result: str) -> bool:
    failure_prefixes = (
        "ERROR",
        "Validation error",
        "Wrong args",
        "Tool error",
        "Unknown tool",
        "Patch failed",
        "Error applying patch",
    )
    return result.startswith(failure_prefixes)


def _run_autoresearch(
    provider,
    cfg: dict,
    model: str,
    reasoning: str,
    output,
    build_prompt: Callable[[bool], str],
) -> None:
    from genie.core.provider import CompletionRequest
    from genie.input import _read_input
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
        goal=goal,
        scope=scope,
        metric_direction=direction,
        verify_command=verify,
        guard_command=guard,
        max_iterations=max_iter,
    )

    try:
        from workflows.loader import WorkflowLoader
        loader = WorkflowLoader()
        workflow_body = loader.inject_prompt("autoresearch") or ""
    except Exception:
        workflow_body = ""

    skill_prompt = build_prompt(True)
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
            req = CompletionRequest(
                messages=ar_session["history"], model=model, reasoning=reasoning
            )
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
                req2 = CompletionRequest(
                    messages=ar_session["history"], model=model, reasoning=reasoning
                )
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

            ctx = SkillContext(provider=provider, output=output, config=cfg)
            patch_result = _normalize_result(SkillRegistry.run_tool(tool_name, tool_args, ctx))
            output.progress(f"[Result] {patch_result[:80]}")

            if _is_tool_failure(patch_result):
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
