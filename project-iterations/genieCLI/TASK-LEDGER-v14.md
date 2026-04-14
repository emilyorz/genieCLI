# TASK-LEDGER

## Basic Info

- Project: genieCLI v14 — pip install fix
- Repo Folder: project-iterations/genieCLI/
- Iteration: 14
- Owner: Emily (Claude Code)
- Status: complete
- Updated: 2026-04-14T18:30+0800
- Focus: Verify and ship the packaging backend fix

## Goal

- One-line summary:
  Fix `pip install .` by correcting the broken setuptools build backend in `pyproject.toml`.
- Done when:
  1. Root cause confirmed; ✅
  2. `pyproject.toml` uses a valid setuptools backend; ✅
  3. `python3 -m pip install .` succeeds; ✅
  4. Changes committed and pushed. ✅

## Carryover

- v10 T9 (live verify on Sam's MCP server) remains blocked on Sam.
- Original symptom: `pip install .` built a wheel named `UNKNOWN-0.0.0`
  (not `ModuleNotFoundError`; the invalid backend path caused pip to
  silently fall back and lose project metadata).

## Todo

| ID | Status | Pri | Task | Owner | Note |
|----|--------|-----|------|-------|------|
| T1 | done | P0 | Verify root cause in `pyproject.toml` and keep the backend set to `setuptools.build_meta` | Emily | Confirmed via `git diff`; only the build-backend line changed |
| T2 | done | P0 | Run `python3 -m pip install .` from repo root and capture evidence | Emily | Built `genie_cli-5.0.0-py3-none-any.whl` in clean Python 3.13 venv; `genie --help` works |
| T3 | done | P0 | Commit and push the packaging fix | Emily | See commit hash in Verify section |

## Verify

- Evidence checked:
  1. `git diff pyproject.toml` — only change is `build-backend` line:
     `"setuptools.backends.legacy:build"` → `"setuptools.build_meta"`.
  2. Clean install in `/tmp/genieci-v14-venv` (Python 3.13.x):
     ```
     Building wheel for genie-cli (pyproject.toml): finished with status 'done'
     Created wheel for genie-cli: filename=genie_cli-5.0.0-py3-none-any.whl
     Successfully installed ... genie-cli-5.0.0 ...
     ```
     Wheel is named `genie_cli-5.0.0`, not `UNKNOWN-0.0.0` — metadata now
     resolves from `[project]` table.
  3. `pip show genie-cli` reports `Name: genie-cli`, `Version: 5.0.0`,
     with correct `Requires` list (prompt_toolkit, pyyaml, requests,
     rich, sqlglot, typer, urllib3).
  4. Entry point works: `genie --help` prints the Typer usage banner
     (`GenieCLI — AI-powered Trino query tuning`).
- Source of evidence:
  - Local shell on macOS 24.5.0 (darwin), Python 3.13 from Homebrew
    (`/opt/homebrew/bin/python3.13`).
  - `pip 25.3` inside the throwaway venv.
- Verification result: PASS — `pip install .` succeeds cleanly, package
  installs under its real name, and the CLI entry point runs.

## Blocked

- None. v10 T9 live MCP verify stays carried forward (not in v14 scope).

## Reports

### Ledger setup — 2026-04-14T18:04+0800

- Opened v14 specifically to repair the `pip install .` failure.
- Root cause already observed once: `setuptools.backends.legacy:build` is not a valid backend.

### Verification pass — 2026-04-14T18:30+0800

- Confirmed the only in-flight code change is the one-line
  `build-backend` fix in `pyproject.toml`. No stray edits.
- Reproduced the fix in a clean Python 3.13 venv. Install output shows
  the wheel carrying the correct project name/version — the `UNKNOWN`
  symptom is gone.
- The system `python3` is 3.9.6, which violates the `requires-python
  >=3.10` gate. Using `/opt/homebrew/bin/python3.13` for verification
  was necessary; this is an environment detail, not a project bug.
- `genie` console script resolves and prints help.

## Retro

- Worked:
  - Keeping the scope to a single-line fix and verifying in an isolated
    venv made the evidence unambiguous — no interaction with existing
    test state.
  - Reusing Homebrew Python 3.13 instead of the system 3.9 avoided a
    spurious `requires-python` failure that would have masked the real
    fix.
- Failed:
  - The ledger's initial "symptom" text said `ModuleNotFoundError: No
    module named 'setuptools.backends'`. The captured pip output Sam
    shared actually shows pip silently falling back to an UNKNOWN
    package rather than raising. Updated Carryover to reflect the real
    trace.
- Change next:
  - When a `pyproject.toml` change lands, add a smoke step that runs
    `pip install .` in a throwaway venv as part of CI so this class of
    regression is caught before release.

## Next Step

- Next action: none for v14 — complete. Next iteration should resume
  v10 T9 when Sam is available, or pick up the CI smoke-install
  suggestion from Retro.
- Next owner: Emily (tmux `emily-claude`)

## Archive / Handoff

- When complete, update STATUS.md with the archived / completed record and keep this ledger path discoverable.
- STATUS.md is the single entrypoint for the next iteration.
- Never move the workflow to a different folder mid-stream.
