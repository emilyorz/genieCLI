"""Browser skill package — Chrome CDP automation.

Registers all browser tools from genie.skills.browser.tools.
No sys.path manipulation — all imports are genie-native.
If CDP dependencies (requests, websocket-client) are absent,
emits a warning and skips registration without crashing.
"""
from __future__ import annotations

import warnings


def register(registry) -> None:
    """Register all available browser skills."""
    try:
        from genie.skills.browser.tools import ALL_BROWSER_SKILLS
    except ImportError as exc:
        warnings.warn(f"Browser skill unavailable: {exc}", stacklevel=2)
        return

    for skill_cls in ALL_BROWSER_SKILLS:
        try:
            registry.register(skill_cls())
        except Exception:
            pass
