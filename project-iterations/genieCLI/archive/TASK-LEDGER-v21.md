# TASK-LEDGER

## Basic Info

- Project: genieCLI v21 — post-release bug fixes from Sam's first-run testing
- Repo Folder: project-iterations/genieCLI/
- Iteration: 21 (4 rounds, 4 PRs)
- Owner: Emily (Claude Code)
- Status: done
- Updated: 2026-04-16T18:15+0800
- Focus: Fix real-run issues surfaced when Sam exercised the
  /trino-research + chat flows end-to-end for the first time.

## Context

v15–v20 got the MCP research loop shipped, gated with a preflight, and
polished the report. v21 cleans up the bugs that surfaced once Sam
actually ran it against his Trino 467 MCP server.

## Round 1 — Paste-mode session leak (PR #42)

### Symptom

"跑完 /trino-research 後任何動作變得只能 Ctrl-D，而且沒法退出 genieCLI."

### Root cause

`_read_paste_mode` reused the shared global `_ps` PromptSession with
`multiline=True` + a custom Ctrl-D submit key binding. Although these
are per-call params in prompt_toolkit, state leaked back to the
session — Enter stopped submitting, only Ctrl-D (bound to submit)
worked, and on an empty line Ctrl-D raises EOFError which
`_read_input` catches as "/exit".

### Fix

`_read_paste_mode` now creates its own fresh `PromptSession`. Nothing
leaks back to the chat loop prompt.

### Changed

- `genie/input.py` — isolated paste-mode session

## Round 2 — Debug traceback aid (PR #43, transient)

### Symptom

Sam wanted a full traceback for a short "ERROR unsupported operand..."
message. The chat-mode exception handler was calling `output.error(str(exc))`
and swallowing the trace.

### Fix (temporary)

Added `traceback.format_exc()` dump inside the exception handler in
`_do_send`. Shipped as a debug aid so Sam could reproduce the error
and get a real stack trace.

### Changed

- `genie/chat.py` — temporary traceback dump (reverted in Round 3)

## Round 3 — Null tool signals task-done + revert debug (PR #44)

### Symptom (real root cause from R2's traceback)

```
File "chat.py", line 131, in _send_with_tools
  action_key = tool_name + json.dumps(tool_args, sort_keys=True, default=str)
TypeError: unsupported operand type(s) for +: 'NoneType' and 'str'
```

### Root cause

Our system prompt tells the AI to signal task completion with
`{"tool": null, "args": {}}`. The AI complied. But:

```python
tool_name = tool_call.get("tool", "?")  # default "?" only applies when key is MISSING
```

When the key is present with value `None`, `dict.get()` returns `None`,
not the default. The next line did `None + json.dumps(...)` — boom.

### Fix

Treat a missing OR `None` tool name as the end-of-tools signal — return
the reply immediately instead of attempting tool dispatch.

Reverted the temporary traceback dump from PR #43.

### Changed

- `genie/chat.py` — null-tool guard; revert debug dump

## Round 4 — EXPLAIN parser us/ns units + first-match-wins (PR #45)

### Symptom

"CPU, Memory, Input Rows, Output Rows 等都是 0 不確定是不是有甚麼問題"

### Root cause (two bugs)

Sam's Trino 467 EXPLAIN ANALYZE output:

```
Fragment 1 [SINGLE]
    CPU: 52.94us, Scheduled: 54.05us, ...
    Peak Memory: 132B
    Input: 1 row (5B); ..., Output: 1 row (5B)
    Values[]
        CPU: 0.00ns, Scheduled: 0.00ns, ...
```

Bug A — Time unit regex was `([\d.]+)(ms|s)`. Short queries use `us`
(microseconds) and `ns` (nanoseconds). Nothing matched → all 0.

Bug B — Even if A were fixed, the inner operator (`Values[]`) block
writes `CPU: 0.00ns` on a later line, overwriting the fragment-level
aggregate.

### Fix

- Expanded unit table: `ns / us / µs / ms / s / min / h` with correct
  ms conversion
- Memory units extended: B / KB / MB / GB / TB
- First-match-wins guard: each metric is only set if the key isn't
  already in `current_stage`. Fragment-level numbers survive inner
  operator lines.
- 3 regression tests using Sam's real Trino 467 output as fixture.

### Changed

- `genie/skills/mcp_trino/research.py` — `_parse_explain_stages`
- `tests/test_mcp_research.py` — 3 new tests

## Verification

- 620 tests pass (up from 617; +3 new)
- Each round merged individually so the repo stayed shippable

## Retro

- **Worked:** Getting Sam to paste the raw EXPLAIN ANALYZE output was
  the unlock. Regex-fix without the real format would have been
  guesswork. Also: shipping a transient debug traceback dump got us
  the null-tool stack trace in one round-trip.
- **Failed:** The null-tool bug was a self-inflicted footgun — we
  told the AI to emit `"tool": null` in our system prompt but didn't
  handle that case in the parser. That should have been caught during
  v17 or earlier when we wrote the prompt.
- **Change next:**
  - Add a defensive test for the null-tool signal path in
    test_send_with_tools (pending)
  - Consider a json-format EXPLAIN parser as an alternative: some
    Trino MCP servers return EXPLAIN as structured JSON rather than
    text. Current parser is text-only. Non-blocker for Sam's server.
  - Still open: v15 R3 (MCP docs update + SKILL.md refresh).
