"""Shared SQL extraction utilities used by research modules."""
from __future__ import annotations

import re
from typing import Optional


def extract_sql_from_reply(reply: str) -> Optional[str]:
    """Extract SQL from an AI reply.

    Tries in order:
    1. Fenced ```sql ... ``` block
    2. Fenced ``` ... ``` block that looks like SQL
    3. None (no SQL found)
    """
    sql_blocks = re.findall(r"```sql\s*\n(.*?)```", reply, re.DOTALL | re.IGNORECASE)
    if sql_blocks:
        return sql_blocks[-1].strip().rstrip(";")

    generic_blocks = re.findall(r"```\s*\n(.*?)```", reply, re.DOTALL)
    for block in reversed(generic_blocks):
        block = block.strip()
        if any(kw in block.upper() for kw in ["SELECT", "WITH", "INSERT", "UPDATE", "DELETE"]):
            return block.rstrip(";")

    return None
