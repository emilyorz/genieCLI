---
name: file-ops
description: >-
  File read/write/list operations. Use for any local filesystem interaction
  including reading file contents, writing text to files, listing directory
  entries, and applying unified diff patches.
version: 1.0.0
group: file
tier: core
---

# File Operations

Provides four tools for local filesystem interaction:

- **read_file** — Read content of a local file (up to 5000 chars)
- **write_file** — Write text content to a local file (creates parent dirs)
- **list_files** — List files in a directory (with optional pattern filter)
- **file_patch** — Apply a unified diff patch to a file
