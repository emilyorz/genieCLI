from .base    import BaseSkill
from .context import (
    BrowserSnapshot, BrowserClickElement,
    BrowserTypeElement, BrowserGetNumbers,
)
from .browser import (
    BrowserListTabs, BrowserSwitchTab,
    BrowserNavigate, BrowserGetURL,
    BrowserGetText, BrowserGetElement, BrowserGetBoundingBox,
    BrowserGetLocalStorage, BrowserGetDOM, BrowserInterceptXHR,
    BrowserClick, BrowserDoubleClick, BrowserRightClick,
    BrowserDrag, BrowserType, BrowserKeyboard,
    BrowserHover, BrowserMouseSweep,
    BrowserScroll, BrowserSelect, BrowserCheckbox,
    BrowserHandleDialog, BrowserWait,
    BrowserExecuteJS,
    BrowserScreenshot, BrowserScreenshotElement,
)
from .system import ReadFile, WriteFile, ListFiles

ALL_SKILLS = [
    # ── Preferred: context-aware (use these first) ──
    BrowserSnapshot(),        # page snapshot with element IDs
    BrowserClickElement(),    # click by element ID
    BrowserTypeElement(),     # type by element ID
    BrowserGetNumbers(),      # extract all numeric values

    # ── Navigation ──
    BrowserListTabs(),
    BrowserSwitchTab(),
    BrowserNavigate(),
    BrowserGetURL(),

    # ── Reading (fallback) ──
    BrowserGetText(),
    BrowserGetElement(),
    BrowserGetBoundingBox(),
    BrowserGetLocalStorage(),
    BrowserGetDOM(),
    BrowserInterceptXHR(),

    # ── Interaction (fallback) ──
    BrowserClick(),
    BrowserDoubleClick(),
    BrowserRightClick(),
    BrowserDrag(),
    BrowserType(),
    BrowserKeyboard(),
    BrowserHover(),
    BrowserMouseSweep(),
    BrowserScroll(),
    BrowserSelect(),
    BrowserCheckbox(),
    BrowserHandleDialog(),
    BrowserWait(),

    # ── Power tools ──
    BrowserExecuteJS(),

    # ── Visual ──
    BrowserScreenshot(),
    BrowserScreenshotElement(),

    # ── File ──
    ReadFile(),
    WriteFile(),
    ListFiles(),
]
