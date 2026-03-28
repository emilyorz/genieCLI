"""Structured result models for oracle2trino conversion output."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class UnsupportedConstruct:
    construct: str    # e.g. "ROWNUM", "CONNECT BY"
    severity: str     # "high" | "medium" | "low"
    message: str      # human-readable description
    suggestion: str   # recommended fix

    def to_dict(self) -> dict:
        return {
            "construct": self.construct,
            "severity": self.severity,
            "message": self.message,
            "suggestion": self.suggestion,
        }


@dataclass
class ConversionResult:
    converted_sql: str
    unsupported: list[UnsupportedConstruct] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    confidence: float = 1.0
    manual_fix_notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "converted_sql": self.converted_sql,
            "unsupported": [u.to_dict() for u in self.unsupported],
            "warnings": self.warnings,
            "confidence": round(self.confidence, 4),
            "manual_fix_notes": self.manual_fix_notes,
        }
