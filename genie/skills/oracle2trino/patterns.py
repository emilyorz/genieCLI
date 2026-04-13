"""Re-export shared Oracle construct catalog from genie.core.sql_patterns.

The canonical data now lives in genie/core/sql_patterns.py so both
oracle2trino and trino_linter import from core, not across skill boundaries.
"""
from genie.core.sql_patterns import (  # noqa: F401
    ORACLE_CONSTRUCTS,
    compute_confidence,
    get_construct_meta,
    get_construct_pattern,
)
