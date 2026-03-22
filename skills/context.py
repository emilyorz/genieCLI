"""
Skills that use page_context for compressed, AI-friendly output.
"""
from .base import BaseSkill


def _ctx():
    """Lazy import page_context to avoid module-level import failures."""
    import sys, os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if root not in sys.path:
        sys.path.insert(0, root)
    import page_context
    return page_context


class BrowserSnapshot(BaseSkill):
    name        = "browser_snapshot"
    description = (
        "Take a structured snapshot of the page: numbered interactive elements "
        "(buttons, inputs, links) with positions, plus a short text summary. "
        "USE THIS FIRST before any interaction — it gives you element IDs to click/type into."
    )
    args_schema = {
        "include_text": "Include visible text summary: true or false (default: true)",
    }

    def run(self, include_text=True):
        incl = str(include_text).lower() != "false"
        return _ctx().get_page_snapshot(include_text=incl)


class BrowserClickElement(BaseSkill):
    name        = "browser_click_element"
    description = (
        "Click an element by its ID from browser_snapshot. "
        "More reliable than CSS selectors — use after browser_snapshot."
    )
    args_schema = {
        "element_id": "The number shown in brackets from browser_snapshot e.g. 3",
    }

    def run(self, element_id=0):
        return _ctx().click_element(int(element_id))


class BrowserTypeElement(BaseSkill):
    name        = "browser_type_element"
    description = (
        "Type text into an input by its ID from browser_snapshot. "
        "Use after browser_snapshot to find the input ID."
    )
    args_schema = {
        "element_id": "The number shown in brackets from browser_snapshot",
        "text":       "Text to type",
    }

    def run(self, element_id=0, text=""):
        return _ctx().type_into_element(int(element_id), text)


class BrowserGetNumbers(BaseSkill):
    name        = "browser_get_numbers"
    description = (
        "Extract all numeric values visible on the page — "
        "metrics, stats, chart labels, table values. "
        "Useful for dashboards, Grafana, monitoring pages."
    )
    args_schema = {}

    def run(self):
        return _ctx().get_numbers_on_page()
