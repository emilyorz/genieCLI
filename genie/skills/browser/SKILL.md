---
name: browser
description: >-
  Chrome CDP browser automation tools. Use for web page interaction,
  navigation, element inspection, screenshots, JavaScript execution,
  and DOM manipulation via Chrome DevTools Protocol.
version: 1.0.0
group: browser
tier: core
requires:
  python:
    - websocket-client
  system:
    - "Chrome with remote-debugging enabled (--remote-debugging-port=9222)"
---

# Browser Automation

Provides 30 tools for Chrome CDP browser automation including navigation,
element interaction (click, type, hover, drag), screenshot capture,
DOM inspection, JavaScript execution, dialog handling, and tab management.

Requires a running Chrome instance with `--remote-debugging-port=9222`.
