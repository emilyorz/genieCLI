# TASK-LEDGER

## Basic Info

- Project: genieCLI v14 — pip install fix
- Repo Folder: project-iterations/genieCLI/
- Iteration: 14
- Owner: Emily (Claude Code)
- Status: complete (project-side; user-side pip upgrade recommended)
- Updated: 2026-04-14T18:55+0800
- Focus: Identify the *real* cause of `UNKNOWN-0.0.0` and decide whether a
  project-side compat shim is worth the cost.

## Goal

- One-line summary:
  Make `pip install .` produce a correctly named `genie-cli` wheel with
  the `genie` console script, without silently dropping PEP 621 metadata.
- Done when:
  1. Root cause of both the backend error and the subsequent
     `UNKNOWN-0.0.0` symptom is confirmed; ✅
  2. `pyproject.toml` uses a valid setuptools backend; ✅
  3. Minimal compat options evaluated against the cost of each; ✅
  4. `pip install .` verified end-to-end on a supported environment; ✅
  5. Changes committed and pushed. ✅

## Carryover

- v10 T9 (live verify on Sam's MCP server) remains blocked on Sam.
- The "still builds as `UNKNOWN-0.0.0`" report is reproducible ONLY on
  pip 21.2.4 (the system's `/usr/bin/python3 -m pip`). Under modern pip
  (≥22), the PEP 621 metadata resolves correctly.

## Todo

| ID | Status | Pri | Task | Owner | Note |
|----|--------|-----|------|-------|------|
| T1 | done | P0 | Reproduce `UNKNOWN-0.0.0` on the actual failing environment | Emily | Repro'd on `/usr/bin/python3` pip 21.2.4 + system setuptools 58.0.4 |
| T2 | done | P0 | Pinpoint why setuptools emits `UNKNOWN.egg-info` before the modern path runs | Emily | pip 21.2.4 legacy `get_requires_for_build_wheel` subprocess runs `egg_info` with a setuptools that ignores pyproject.toml, writing `UNKNOWN.egg-info/` into the source tempdir; `bdist_wheel` later reuses it |
| T3 | done | P0 | Evaluate minimal compat shims (`setup.cfg`, `setup.py`) without losing PEP 621 metadata | Emily | Every legacy shim that suppresses `UNKNOWN` also silently strips `Requires-Python`, `Requires-Dist`, and the project summary — net worse than the bug |
| T4 | done | P0 | Pick the cheapest correct fix | Emily | Project fix = the existing build-backend correction; user-side = upgrade pip (one command) |
| T5 | done | P0 | End-to-end verify: named wheel + working entry point | Emily | Homebrew Python 3.13 venv: `genie_cli-5.0.0-py3-none-any.whl`, full `Requires-Dist`, `genie --help` runs |
| T6 | done | P0 | Uninstall the residual `UNKNOWN-0.0.0` from the user site | Emily | `pip uninstall -y UNKNOWN`; directory gone |
| T7 | done | P1 | Ledger update + commit | Emily | This file + commit in v14 fix hash chain |

## Verify

- Evidence checked:

  1. **Symptom reproduction on pip 21.2.4.** With the build-backend fix
     already committed, running the exact command Sam ran:
     ```
     /usr/bin/python3 -m pip install --no-cache-dir .
     ```
     produces
     ```
     Building wheels for collected packages: UNKNOWN
     Created wheel for UNKNOWN: filename=UNKNOWN-0.0.0-py3-none-any.whl size=964
     Successfully installed UNKNOWN-0.0.0
     ```
     so Sam's report is correct for that environment.

  2. **Residue of prior install.** Before the reproduction, the old
     `UNKNOWN-0.0.0` install was still sitting in
     `/Users/leeabc/Library/Python/3.9/lib/python/site-packages/UNKNOWN-0.0.0.dist-info`
     (direct_url.json pointed to `file:///Users/leeabc/work/emilyorz/genieCLI`).
     That's why `pip show` / PATH lookups still saw UNKNOWN — the
     pre-fix install had never been uninstalled. Removed in T6.

  3. **Drill-down on the build tempdir (pip `--no-clean`).**
     `/tmp/pip-nocleanup/pip-req-build-*/` ended up containing BOTH
     `UNKNOWN.egg-info/` (`Metadata-Version: 2.1`, `Name: UNKNOWN`)
     and `genie_cli.egg-info/` (`Metadata-Version: 2.4`, `Name: genie-cli`,
     full `Requires-Dist`, `Requires-Python: >=3.10`).
     The older-version `UNKNOWN.egg-info` is written FIRST by pip
     21.2.4's legacy `get_requires_for_build_wheel` path. `bdist_wheel`
     later reuses the first egg-info it finds for the wheel filename.

  4. **Direct backend call with the same setuptools is clean.**
     Invoking `setuptools.build_meta.prepare_metadata_for_build_wheel`
     directly under Python 3.9 with overlay `setuptools 82.0.1` reads
     pyproject correctly and produces `genie_cli-5.0.0.dist-info`.
     So the backend itself is fine — pip 21.2.4's internal sequencing
     is the bug.

  5. **Upgrading pip removes the symptom.** After
     `/usr/bin/python3 -m pip install --user --upgrade pip`
     (pip 21.2.4 → 26.0.1) the install no longer produces UNKNOWN;
     pip correctly reports
     `ERROR: Package 'genie-cli' requires a different Python: 3.9.6 not in '>=3.10'`
     — which is the right answer because system Python is 3.9.6 and
     the project requires ≥3.10.

  6. **End-to-end on a supported Python.** Clean venv via
     `/opt/homebrew/bin/python3.13 -m venv /tmp/genie-final` followed by
     `pip install .` produces `genie_cli-5.0.0-py3-none-any.whl` with
     full `Requires-Dist`, and `genie --help` runs.

  7. **Compat-shim evaluation.** Tried three minimal shims against a
     pip 21.2.4 reproduction on Python 3.9:

     | Shim | Wheel name | Code content | Deps / requires-python |
     |---|---|---|---|
     | none (pyproject only) | `UNKNOWN-0.0.0` | empty (964 B) | lost |
     | `setup.py: setup()` (empty) | `UNKNOWN-0.0.0` | empty | lost |
     | `setup.py: setup(name=..., version=...)` | `genie_cli-5.0.0` | **empty (994 B)** | **lost** |
     | `setup.cfg` with `[metadata]` + `[options] packages = find:` + `[options.entry_points]` + `[options.packages.find] include = genie*` | `genie_cli-5.0.0` | full (97 kB) | **still lost** — the installed `METADATA` drops to `Metadata-Version: 2.1` with `Summary: UNKNOWN` and no `Requires-Dist` / `Requires-Python`, because the legacy config wins over pyproject on old pip |

     **Conclusion:** every legacy shim that fixes the name also silently
     strips PEP 621 fields. That is worse than the original bug, not
     minimal. Skipped.

- Source of evidence:
  - macOS 24.5.0 (darwin), `/usr/bin/python3` = Python 3.9.6, system
    pip 21.2.4 (later upgraded to 26.0.1), system setuptools 58.0.4.
  - Homebrew `/opt/homebrew/bin/python3.13` for the supported-Python venv.
  - Build tempdirs preserved via `pip install --no-clean` under a
    custom `TMPDIR=/tmp/pip-nocleanup`.

- Verification result: **PASS on project scope.**
  - `pyproject.toml` is correct; no compat shim is worth adding.
  - `pip install .` produces a correctly-named wheel and working
    `genie` entry point on any pip ≥22 with Python ≥3.10.
  - The user-side requirement: run pip ≥22 (or use Homebrew
    python3.13 / a venv), because pip 21.2.4 is older than PEP 621
    stabilized support.

## Blocked

- None. v10 T9 live MCP verify stays carried forward (not in v14 scope).

## Reports

### Ledger setup — 2026-04-14T18:04+0800

- Opened v14 specifically to repair the `pip install .` failure.
- First root cause found: `setuptools.backends.legacy:build` is not a
  valid backend. Fixed to `setuptools.build_meta`.

### Second-pass investigation — 2026-04-14T18:55+0800

- Sam pushed back: install still produced `UNKNOWN-0.0.0` and no `genie`
  on PATH even after the backend fix. Re-opened.
- Confirmed the reproduction on pip 21.2.4. Drilled into the build
  tempdir and found both `UNKNOWN.egg-info/` and `genie_cli.egg-info/`
  side-by-side — diagnostic proof that pip 21.2.4's legacy flow is the
  source of `UNKNOWN`, not setuptools' pyproject reader.
- Evaluated `setup.py` and `setup.cfg` compat shims. Each one that
  suppresses the `UNKNOWN` filename also silently strips
  `Requires-Python` and `Requires-Dist` from the installed metadata
  (verified by reading the resulting `METADATA` file). Net effect is
  worse than the bug — the package would install on 3.9 against the
  stated `requires-python`, and downstream `pip install` would not
  pull in the runtime dependencies. Rejected.
- Confirmed the same command on pip 26.0.1 behaves correctly: either a
  proper install, or a clear "requires a different Python" error — no
  silent `UNKNOWN` wheel.
- Verified end-to-end on Python 3.13: named wheel, full metadata,
  working entry point.
- Cleaned up the residual `UNKNOWN-0.0.0` left in user site-packages
  from the pre-fix install.

## Retro

- Worked:
  - Preserving pip's build tempdir with `--no-clean` was the key move.
    Seeing `UNKNOWN.egg-info/` and `genie_cli.egg-info/` in the same
    directory made the pip-21.2.4-specific race obvious.
  - Testing every candidate shim against *both* the filename AND the
    installed METADATA caught the silent-metadata-loss in the setup.cfg
    shim. Stopping at "filename is now genie-cli" would have shipped a
    worse bug than the one we started with.
- Failed:
  - First pass claimed completion after verifying only on Homebrew
    python3.13. The failing environment was system python3.9.6 on pip
    21.2.4, which wasn't exercised. That's why Sam's "still UNKNOWN"
    report looked contradictory. Lesson: verify on the exact
    environment the bug was reported in, not a nearby one.
  - The first-pass Retro's "add a CI smoke-install step" was the right
    instinct but needs to target multiple pip versions (or at least the
    oldest the project claims to support).
- Change next:
  - README / install doc should state "requires pip ≥22 and Python ≥3.10"
    explicitly. Quietly assuming modern pip is what lost us this cycle.
  - CI smoke: `pip install .` under Python 3.10 and Python 3.13 at
    minimum; assert wheel name matches `genie_cli-*` and that
    `pip show genie-cli | grep Requires-Python` returns `>=3.10`.

## Next Step

- Next action: none for v14.
- Follow-up candidates (separate iterations):
  1. README/install docs — call out the pip version floor.
  2. CI smoke install across supported Python versions.
  3. v10 T9 live MCP verify when Sam unblocks it.
- Next owner: Emily (tmux `emily-claude`)

## Archive / Handoff

- STATUS.md is the single entrypoint for the next iteration.
- Never move the workflow to a different folder mid-stream.
