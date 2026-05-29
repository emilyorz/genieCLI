# TASK-LEDGER

## Basic Info

- Project: genieCLI v15 — `/trino-research` must always use configured MCP
- Repo Folder: project-iterations/genieCLI/
- Iteration: 15
- Owner: Emily (Claude Code)
- Status: active (Round 1-2 complete, R3 pending)
- Updated: 2026-04-14T19:10+0800
- Focus: Make the `/trino-research` command load a real, configured MCP
  server every time — no optional path, no silent fallback, no guessing.

## Goal

- One-line summary:
  `/trino-research` must execute through the configured MCP server as
  its only backend. If MCP is not configured or not reachable, it must
  fail loudly instead of degrading to direct Trino.
- Done when:
  1. Root cause of "`/trino-research` ignores MCP" captured from code,
     not assumptions; ✅ (Round 1)
  2. `/trino-research` wiring routes through an MCP client built from
     `load_mcp_config()`, with no code path that silently falls back to
     direct Trino;
  3. Startup/precondition: if MCP can't be reached, the command exits
     with a clear, actionable error (no `UNKNOWN`-style surprises);
  4. Tests cover: happy path (MCP configured, research runs end-to-end
     with a mocked MCP), error path (no MCP → loud failure);
  5. SKILL.md / README / chat help text all match the new reality;
  6. Changes committed and pushed in a scope-minimal commit.

## Round 1 — Diagnose (active → done)

### Goal

Find, in the actual code, why `/trino-research` is not using MCP
today — without guessing, and without relying on memory.

### What actually exists (verified against the repo, not memory)

1. **CLI entry point `genie`.** Declared in `pyproject.toml`:

   ```toml
   [project.scripts]
   genie = "genie.cli:main"
   ```

   `main()` is at `genie/cli.py:376`. Confirmed exists.

2. **`genie setup mcp` command.** Declared in `genie/cli.py:301-313`:

   ```python
   @app.command()
   def setup(target: str = typer.Argument("llm", help="What to set up: llm, trino, mcp")) -> None:
       from genie.setup_wizard import setup_check, setup_llm, setup_mcp, setup_trino
       wizards = {"llm": setup_llm, "trino": setup_trino, "mcp": setup_mcp, "check": setup_check}
       wizard = wizards.get(target)
       ...
       wizard()
   ```

   `setup_mcp` lives in `genie/setup_wizard.py`. Confirmed exists.

3. **MCP config loader.** `genie/skills/mcp_trino/client.py:39-90`.
   `load_mcp_config()` merges, in priority order:
   - env vars `GENIE_MCP_TRINO_URL`, `GENIE_MCP_TRINO_ENABLED` (highest)
   - TOML `~/.genie/config.toml` `[mcp.trino]`
   - JSON `~/.config/genie/mcp.json`
   - defaults (`url=http://localhost:8811`, `enabled=True`)
     Returns an `McpConfig` dataclass.

4. **MCP client.** `genie/skills/mcp_trino/client.py:114` — `McpClient`
   wraps JSON-RPC 2.0 over HTTP with `Mcp-Session-Id`. Has
   `list_tools()` and `call_tool(name, arguments)`.

5. **Two separate "research" implementations exist.**
   - `genie/skills/trino_query/research.py` — uses direct Trino via
     `genie.skills.trino_query.connection.get_active_profile()`;
     never touches MCP.
   - `genie/skills/mcp_trino/research.py` — calls `McpClient.call_tool("query", {"sql": ...})`
     through `_execute_via_mcp` (line 489). Its entry function is
     `run_mcp_enhancement(client, sql, ...)` at line 905.

6. **The `/trino-research` slash command is wired to the DIRECT path.**
   `genie/chat.py:741-763`:

   ```python
   elif cmd == "/trino-research":
       from genie.skills.trino_query.research import run_trino_research
       ...
       run_trino_research(provider, cfg, model, current_reasoning, output, build_prompt, **kwargs)
   ```

   There is no branch that routes to `mcp_trino.research` at all.

7. **`run_mcp_enhancement` has zero production callers.** Grep result:
   ```
   genie/chat.py:742  from genie.skills.trino_query.research import run_trino_research
   genie/chat.py:763  run_trino_research(provider, cfg, model, ...)
   genie/skills/trino_query/research.py:469  def run_trino_research(...)
   genie/skills/mcp_trino/research.py:905     def run_mcp_enhancement(...)
   ```
   Only `tests/test_mcp_research.py` imports helpers from
   `mcp_trino.research`; it never constructs or drives `run_mcp_enhancement`
   end-to-end against a real or mocked flow.

### Root cause

`/trino-research` is wired in `chat.py` to the _direct-Trino_ research
module. The _MCP-aware_ research module exists, is complete enough to
ship, but is orphaned — no CLI surface, no registry hook, no slash
command. This is not an "MCP is optional" situation in the sense of a
runtime toggle; it is "MCP is orphaned" at the import level.

### Round 1 — changed files

- None (investigation only).

### Round 1 — verification evidence

- Commands run:
  - `grep -rn "trino-research" genie/` → 5 hits, all in
    `trino_query/research.py`, `input.py`, `chat.py` — none in
    `mcp_trino/`.
  - `grep -rn "run_mcp_enhancement\|run_trino_research" .` → only
    `chat.py:742,763` call into `trino_query.research`;
    `run_mcp_enhancement` is defined but not called anywhere in the
    non-test tree.
  - Read `genie/chat.py:741-763` directly to confirm wiring; no
    conditional on MCP config.
  - Read `genie/skills/trino_query/research.py:24,37-50,469-601` —
    confirms `get_active_profile().connect()` is the only execution
    path.
  - Read `genie/skills/mcp_trino/research.py:489-527, 530-564, 905-980`
    — confirms MCP client is required at the function boundary.
  - Read `pyproject.toml` and `genie/cli.py:301-313,376-377` — confirms
    `genie` and `genie setup mcp` both exist.

### Round 1 — remaining risks

- `setup_wizard.setup_mcp()` behavior not yet read; need to confirm
  that a successful wizard run produces config that
  `load_mcp_config()` reads on the next process.
- `run_mcp_enhancement` has never been live-verified against Sam's
  MCP server (v10 T9 carryover). The plumbing exists but the wire has
  not carried a packet end-to-end.
- Rewiring `/trino-research` to MCP will change the observable
  behavior of an existing command. If any CI / Skill registry / doc
  references the old direct-Trino semantics, those will need updates
  in Round 3.

## Round 2 — Rewire (done — shipped in v17 PRs #35/#36/#37/#38)

### Planned change

- `genie/chat.py`:
  replace the `elif cmd == "/trino-research":` block so it:
  1. Calls `load_mcp_config()`.
  2. Requires `enabled=True` and a reachable endpoint — otherwise
     print a single-line, actionable error (`MCP not configured — run
'genie setup mcp'`) and return.
  3. Instantiates `McpClient(config)` and calls
     `run_mcp_enhancement(client, sql, metric_key, max_iterations,
verify_runs, provider, model, reasoning, output, build_prompt)`
     — with the flag parsing (`--file`, `--metric`, `--iterations`,
     `--runs`) adapted to the MCP entry's parameter names.
  4. Writes the returned `EnhancementReport` through
     `mcp_trino.research.generate_report` (already present in that
     module) and prints/saves it.
- Do NOT modify `genie/skills/trino_query/research.py` in this round.
  We keep it in the tree until Round 3 decides whether to delete it
  or rename it (e.g., to `/trino-research-local`).

### Non-goals for Round 2

- Do not change `genie setup mcp` or the MCP client.
- Do not add fallback paths; the whole point of v15 is removing the
  implicit fallback.

## Round 3 — Tests + docs + cleanup (pending)

- Add/adjust tests in `tests/test_mcp_research.py` to cover:
  - `/trino-research` wiring with a mocked `McpClient` (happy path)
  - `/trino-research` when `load_mcp_config()` returns `enabled=False`
    or the endpoint is unreachable — must exit with an error, not a
    traceback.
- Update `genie/skills/trino_query/SKILL.md` and/or
  `genie/skills/mcp_trino/SKILL.md` so it's clear `/trino-research`
  requires MCP.
- Update `README.md` / `genie/input.py` help text if they still imply
  MCP is optional for research.
- Decide whether to delete `genie/skills/trino_query/research.py` or
  keep it behind a renamed slash command. Ship the decision in this
  round's commit.

## Blocked

- Live verify of MCP server (v10 T9) still blocked on Sam. v15 must
  be demonstrable with a mocked MCP to avoid coupling to that
  blocker.

## Reports

### Round 1 report — 2026-04-14T19:10+0800

- **Goal:** find the actual reason `/trino-research` doesn't use MCP,
  and verify that the CLI surface Sam referenced (`genie`, `genie
setup mcp`) truly exists.
- **Changed files:** none (read-only investigation).
- **Verification evidence:** see "Round 1 — verification evidence"
  above; every claim ties back to a specific file + line range in the
  repo.
- **Remaining risks:** `setup_mcp` wizard not yet read; Sam's MCP
  server still unreachable for live end-to-end proof; Round 2 will
  change a user-visible command's behavior.

## Retro

- Worked:
  - Resisted the urge to infer wiring from doc strings. Reading the
    `chat.py` dispatcher directly surfaced the orphaned-module
    situation within a few greps.
- Failed:
  - (nothing yet — Round 1 was scoped as investigation only)
- Change next:
  - (deferred to Round 2/3 retros)

## Next Step

- Next action: start Round 2 — rewire `/trino-research` in `chat.py`
  to `run_mcp_enhancement`, with a hard precondition on a reachable
  MCP server.
- Next owner: Emily (tmux `emily-claude`)

## Archive / Handoff

- STATUS.md is the single entrypoint for the next iteration.
- Never move the workflow to a different folder mid-stream.
