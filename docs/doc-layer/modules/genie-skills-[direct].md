---
covers:
  - "genie/skills/__init__.py"
last_synced: "df1131522263a60bac2a7a0326499f43bc63c490"
---

## Purpose

`genie/skills/__init__.py` is an empty namespace marker that makes
`genie/skills` a Python package. It carries no logic of its own; all
skill implementations live in sub-packages (`mcp_trino`, `oracle2trino`,
`trino_query`). The file exists solely so that `from genie.skills.<sub>`
imports resolve correctly under the project's package layout.

## Exports

None. The file is empty (0 bytes). Consumers import directly from the
sub-packages:

- `genie.skills.mcp_trino` — MCP-backed Trino research and optimization
- `genie.skills.oracle2trino` — Oracle-to-Trino SQL transpilation
- `genie.skills.trino_query` — Direct Trino query execution and linting

## Invariants

- The file must remain empty; adding imports here would create hidden
  coupling between sub-packages that each manage their own `register()`
  entry point called by `genie.core.registry.SkillRegistry.discover`.
- Sub-package discovery is driven by `SkillRegistry.discover` /
  `discover_legacy` in `genie/core/registry.py`, not by any import in
  this file.
- Removing this file breaks the `genie.skills.*` import namespace for
  all three sub-packages.

## Change log

- df1131522263a60bac2a7a0326499f43bc63c490: initial doc-layer card created
