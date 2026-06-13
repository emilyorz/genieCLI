---
covers:
  - "genie/skills/__init__.py"
last_synced: "dfeabc1a64fb3dcf297942cf39e4cf5ba55f334b"
---

## Purpose

Empty package marker that makes `genie/skills` a Python package. It declares no symbols, imports, or initialisation logic — its sole role is to allow sibling sub-packages (`mcp_trino`, `trino_query`, `oracle2trino`) to be imported as `genie.skills.<subpackage>`.

## Exports

> See exports file: /Users/leeabc/work/emilyorz/genieCLI/docs/doc-layer/exports/genie-skills--direct-.md

No exported symbols — the file is intentionally empty.

## Invariants

- File must remain empty (zero bytes or a single blank line) — any logic added here runs on every `import genie.skills.*` and would become a hidden shared side-effect — `genie/skills/__init__.py:1` — ``

## Change log

- dfeabc1a64fb3dcf297942cf39e4cf5ba55f334b: initial card created; file confirmed empty package marker
