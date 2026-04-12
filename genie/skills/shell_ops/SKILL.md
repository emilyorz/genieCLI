---
name: shell-ops
description: >-
  Safe whitelisted shell command execution. Runs commands through
  pre-configured profiles (python-test, node-test, lint, build, custom)
  with timeout and output limits.
version: 1.0.0
group: shell
tier: core
---

# Shell Operations

Provides one tool for safe shell command execution:

- **command_run** — Execute a shell command using a named profile that controls allowed commands, timeouts, and output limits.

Profiles: `python-test`, `node-test`, `lint`, `build`, `custom`.
