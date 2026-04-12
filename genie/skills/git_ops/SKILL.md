---
name: git-ops
description: >-
  Git status, diff, log, and checkpoint operations. Use for inspecting
  repository state, viewing changes, reading commit history, and
  creating/restoring working-tree checkpoints.
version: 1.0.0
group: git
tier: core
---

# Git Operations

Provides five tools for git repository interaction:

- **git_status** — Show working tree status (porcelain format)
- **git_diff** — Show diff for worktree, staged, or HEAD~1
- **git_log** — Show recent commit log (one line per commit)
- **git_checkpoint_create** — Snapshot current working-tree state
- **git_checkpoint_restore** — Restore to a previous checkpoint
