"""Tests for genie/session/manager.py"""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

import genie.session.manager as sm


# ── new_msg ───────────────────────────────────────────────────────────────────

def test_new_msg_structure():
    msg = sm.new_msg("user", "hello")
    assert msg["role"] == "user"
    assert msg["content"][0]["text"] == "hello"
    assert msg["content"][0]["type"] == "text"
    assert msg["content"][0]["reasonText"] is None
    assert "id" in msg
    assert "timestamp" in msg


def test_new_msg_unique_ids():
    m1 = sm.new_msg("user", "a")
    m2 = sm.new_msg("user", "b")
    assert m1["id"] != m2["id"]


# ── slug ──────────────────────────────────────────────────────────────────────

def test_slug_lowercases():
    assert sm.slug("Hello World") == "hello-world"


def test_slug_strips_special_chars():
    assert sm.slug("hello! @world#") == "hello-world"


def test_slug_truncates():
    result = sm.slug("a" * 50, max_len=10)
    assert len(result) <= 10


def test_slug_replaces_underscores():
    assert sm.slug("hello_world") == "hello-world"


def test_slug_strips_leading_trailing_dashes():
    result = sm.slug("-hello-")
    assert not result.startswith("-")
    assert not result.endswith("-")


# ── new_session ───────────────────────────────────────────────────────────────

def test_new_session_defaults():
    session = sm.new_session()
    assert session["title"] == "New conversation"
    assert session["history"] == []
    assert session["filename"] is None
    assert len(session["id"]) == 8


def test_new_session_with_system_prompt():
    session = sm.new_session(system_prompt="You are helpful.")
    assert len(session["history"]) == 1
    assert session["history"][0]["role"] == "system"
    assert session["history"][0]["content"][0]["text"] == "You are helpful."


def test_new_session_unique_ids():
    s1 = sm.new_session()
    s2 = sm.new_session()
    assert s1["id"] != s2["id"]


# ── save_session / load_session ───────────────────────────────────────────────

def test_save_and_load_session(tmp_path):
    with patch.object(sm, "SESSIONS_DIR", tmp_path):
        session = sm.new_session()
        session["title"] = "Test Chat"
        sm.save_session(session)
        assert session["filename"] is not None

        loaded = sm.load_session(session["filename"])
        assert loaded["title"] == "Test Chat"
        assert loaded["id"] == session["id"]


def test_save_session_sets_filename_from_title(tmp_path):
    with patch.object(sm, "SESSIONS_DIR", tmp_path):
        session = sm.new_session()
        session["title"] = "My First Chat"
        sm.save_session(session)
        assert "my-first-chat" in session["filename"]


def test_save_session_preserves_existing_filename(tmp_path):
    with patch.object(sm, "SESSIONS_DIR", tmp_path):
        session = sm.new_session()
        session["title"] = "Test"
        sm.save_session(session)
        first_filename = session["filename"]
        sm.save_session(session)  # second save
        assert session["filename"] == first_filename


# ── list_sessions ─────────────────────────────────────────────────────────────

def test_list_sessions_empty(tmp_path):
    with patch.object(sm, "SESSIONS_DIR", tmp_path):
        result = sm.list_sessions()
    assert result == []


def test_list_sessions_returns_entries(tmp_path):
    with patch.object(sm, "SESSIONS_DIR", tmp_path):
        for title in ("Alpha", "Beta", "Gamma"):
            session = sm.new_session()
            session["title"] = title
            sm.save_session(session)
            # Add a user message to count turns
            session["history"].append(sm.new_msg("user", "hi"))
            sm.save_session(session)

        entries = sm.list_sessions()
    assert len(entries) == 3
    titles = {e["title"] for e in entries}
    assert titles == {"Alpha", "Beta", "Gamma"}


def test_list_sessions_skips_corrupt_files(tmp_path):
    with patch.object(sm, "SESSIONS_DIR", tmp_path):
        bad = tmp_path / "corrupt.json"
        bad.write_text("not json", encoding="utf-8")
        result = sm.list_sessions()
    assert result == []


def test_list_sessions_counts_user_turns(tmp_path):
    with patch.object(sm, "SESSIONS_DIR", tmp_path):
        session = sm.new_session()
        session["title"] = "Chat"
        session["history"].append(sm.new_msg("user", "q1"))
        session["history"].append(sm.new_msg("assistant", "a1"))
        session["history"].append(sm.new_msg("user", "q2"))
        sm.save_session(session)
        entries = sm.list_sessions()
    assert entries[0]["turns"] == 2


# ── update_title ──────────────────────────────────────────────────────────────

def test_update_title_changes_title():
    session = sm.new_session()
    sm.update_title(session, "Tell me about Python")
    assert session["title"] == "Tell me about Python"


def test_update_title_truncates_at_40():
    session = sm.new_session()
    sm.update_title(session, "x" * 60)
    assert len(session["title"]) == 40


def test_update_title_skips_if_already_set():
    session = sm.new_session()
    session["title"] = "Custom Title"
    sm.update_title(session, "Should not change")
    assert session["title"] == "Custom Title"


def test_update_title_sets_filename():
    session = sm.new_session()
    sm.update_title(session, "Refactoring tips")
    assert "refactoring-tips" in session["filename"]
