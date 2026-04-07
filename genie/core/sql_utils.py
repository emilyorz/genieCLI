"""Shared SQL text utilities — used by oracle2trino and trino_linter skills."""
from __future__ import annotations


def strip_comments_and_strings(sql: str) -> str:
    """Replace comment/string content with spaces, preserving newlines and character positions."""
    chars = list(sql)
    i = 0
    n = len(sql)
    while i < n:
        if sql[i:i+2] == '--':
            while i < n and sql[i] != '\n':
                chars[i] = ' '
                i += 1
        elif sql[i:i+2] == '/*':
            chars[i] = ' '
            chars[i + 1] = ' '
            i += 2
            while i < n:
                if sql[i:i+2] == '*/':
                    chars[i] = ' '
                    chars[i + 1] = ' '
                    i += 2
                    break
                if sql[i] != '\n':
                    chars[i] = ' '
                i += 1
        elif sql[i] == "'":
            chars[i] = ' '
            i += 1
            while i < n:
                if sql[i] == "'":
                    chars[i] = ' '
                    i += 1
                    if i < n and sql[i] == "'":
                        chars[i] = ' '
                        i += 1
                        continue
                    break
                if sql[i] != '\n':
                    chars[i] = ' '
                i += 1
        else:
            i += 1
    return ''.join(chars)
