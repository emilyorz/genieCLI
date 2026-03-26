"""Browser skill package — Chrome CDP automation.

Imports browser tools from the legacy skills/browser.py and skills/context.py
by loading the legacy `skills` package (which supports relative imports).
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent.parent  # genieCLI/


def register(registry) -> None:
    """Register all available browser skills."""
    # Add root so `import skills.browser` resolves to the legacy package
    root_str = str(_ROOT)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)

    try:
        import skills.browser as _browser_mod  # type: ignore[import]
        import skills.context as _context_mod  # type: ignore[import]
    except ImportError as exc:
        warnings.warn(f"Browser skill unavailable: {exc}", stacklevel=2)
        return

    skill_classes = set()
    for mod in (_browser_mod, _context_mod):
        for attr_name in dir(mod):
            obj = getattr(mod, attr_name)
            if (
                isinstance(obj, type)
                and hasattr(obj, "name")
                and hasattr(obj, "run")
                and getattr(obj, "name", "")
                and obj not in skill_classes
            ):
                skill_classes.add(obj)

    for cls in skill_classes:
        try:
            registry.register(cls())
        except Exception:
            pass
