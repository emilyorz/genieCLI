# TASK-LEDGER

## Basic Info

- Project: genieCLI v16 — CLI subcommand routing audit & fix
- Repo Folder: project-iterations/genieCLI/
- Iteration: 16
- Owner: Emily (Claude Code)
- Status: done
- Updated: 2026-04-16T09:35+0800
- Focus: Audit all cli.py subcommands, fix broken routing caused by
  callback positional arg eating subcommand names.

## Goal

- One-line summary:
  All `genie <subcommand>` invocations must dispatch correctly — no
  "File not found" or "No such command" errors for valid subcommands.
- Done when:
  1. All @app.command() and callback-handled targets enumerated; ✅
  2. Missing routes identified; ✅ (setup was missing)
  3. Fix applied and verified for all subcommands; ✅
  4. `genie setup [llm|trino|mcp|check]` all work; ✅
  5. Existing tests pass (587 passed, 0 failed); ✅

## Audit Results

### Subcommand inventory

| Target        | Type                 |        Pre-fix status        | Post-fix |
| ------------- | -------------------- | :--------------------------: | :------: |
| `setup`       | @app.command         |  ❌ "File not found: setup"  |    ✅    |
| `setup trino` | @app.command + arg   | ❌ "No such command 'trino'" |    ✅    |
| `setup mcp`   | @app.command + arg   |           ❌ same            |    ✅    |
| `setup check` | @app.command + arg   |           ❌ same            |    ✅    |
| `doctor`      | @app.command + shim  |              ✅              |    ✅    |
| `verify`      | @app.command + shim  |              ✅              |    ✅    |
| `sessions`    | callback target      |              ✅              |    ✅    |
| `config`      | callback target      |              ✅              |    ✅    |
| `tools`       | callback target      |              ✅              |    ✅    |
| `<file>.sql`  | callback file-path   |              ✅              |    ✅    |
| `chat` (bare) | callback fallthrough |              ✅              |    ✅    |
| `--version`   | eager option         |              ✅              |    ✅    |

### Root cause

The callback has `invoke_without_command=True` + a positional `target`
argument. Click/Typer binds the first positional to `target` before
attempting subcommand dispatch. This means:

1. `genie setup` → target="setup", falls to file-path branch → error
2. `genie setup trino` → target="setup", "trino" orphaned → Click
   tries "trino" as subcommand → error

The old shim handled doctor/verify (no args) but missed setup (has arg).

### Fix

Changed `target: Optional[str] = typer.Argument(None)` to
`args: Optional[list[str]] = typer.Argument(None)` (variadic).

This makes Click consume ALL positional tokens into the list, so
"setup trino" becomes `args=["setup", "trino"]`. No orphan tokens,
no subcommand resolution errors.

The shim then reads `args[0]` as target and `args[1]` as sub-target
for setup.

### Changed file

- `genie/cli.py` — callback signature + shim routing (3 edits, ~10 lines net)

## Test Results

- 587 passed, 10 skipped, 0 failed (pytest, 0.78s)
- Manual verification: all 12 invocation patterns confirmed working

## Retro

- **Worked:** Variadic arg is the cleanest fix — eliminates the
  positional-vs-subcommand conflict entirely, no fragile sys.argv
  parsing needed.
- **Failed:** First two attempts (allow_extra_args alone, then
  allow_extra_args + ignore_unknown_options) broke other paths
  because Click's subcommand resolution is separate from extra-args
  handling.
- **Change next:** Any future @app.command() additions should be
  added to the shim in the callback. Consider refactoring
  sessions/config/tools into @app.command() to reduce shim size.
