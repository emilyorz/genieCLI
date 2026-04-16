"""Tests for MCP Trino client and skill registration."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from genie.core.registry import SkillRegistry
from genie.skills.mcp_trino.client import McpClient, McpConfig, McpError


# ── McpConfig ────────────────────────────────────────────────────────────────


class TestMcpConfig:
    def test_defaults(self):
        cfg = McpConfig()
        assert cfg.url == "http://localhost:8811/mcp"
        assert cfg.enabled is True
        assert cfg.timeout == 30

    def test_endpoint_strips_trailing_slash(self):
        cfg = McpConfig(url="http://localhost:8811/mcp/")
        assert cfg.endpoint() == "http://localhost:8811/mcp"

    def test_custom_values(self):
        cfg = McpConfig(url="http://trino-mcp:9999", enabled=False, timeout=60)
        assert cfg.endpoint() == "http://trino-mcp:9999"
        assert cfg.enabled is False
        assert cfg.timeout == 60


# ── McpClient ────────────────────────────────────────────────────────────────


class TestMcpClient:
    def setup_method(self):
        self.config = McpConfig(url="http://localhost:8811/mcp")
        self.client = McpClient(self.config)

    def test_next_id_increments(self):
        assert self.client._next_id() == 1
        assert self.client._next_id() == 2
        assert self.client._next_id() == 3

    @patch.object(McpClient, "_post")
    def test_list_tools_returns_tools_array(self, mock_post):
        self.client._initialized = True
        mock_post.return_value = {
            "tools": [
                {"name": "query", "description": "Run SQL", "inputSchema": {}},
                {"name": "schema", "description": "List schemas", "inputSchema": {}},
            ]
        }
        tools = self.client.list_tools()
        assert len(tools) == 2
        assert tools[0]["name"] == "query"
        assert tools[1]["name"] == "schema"

    @patch.object(McpClient, "_post")
    def test_call_tool_text_content(self, mock_post):
        self.client._initialized = True
        mock_post.return_value = {
            "content": [{"type": "text", "text": "SELECT 1 returned 1 row"}]
        }
        result = self.client.call_tool("query", {"sql": "SELECT 1"})
        assert "SELECT 1 returned 1 row" in result

    @patch.object(McpClient, "_post")
    def test_call_tool_multi_content(self, mock_post):
        self.client._initialized = True
        mock_post.return_value = {
            "content": [
                {"type": "text", "text": "line1"},
                {"type": "text", "text": "line2"},
            ]
        }
        result = self.client.call_tool("query", {"sql": "SELECT 1"})
        assert "line1" in result
        assert "line2" in result

    def test_parse_sse_response_extracts_result(self):
        sse_text = (
            "event: message\n"
            'data: {"jsonrpc":"2.0","id":1,"result":{"tools":[{"name":"test"}]}}\n'
            "\n"
        )
        result = self.client._parse_sse_response(sse_text)
        assert result == {"tools": [{"name": "test"}]}

    def test_parse_sse_response_raises_on_error(self):
        sse_text = 'data: {"jsonrpc":"2.0","id":1,"error":{"code":-32601,"message":"not found"}}\n'
        with pytest.raises(McpError) as exc_info:
            self.client._parse_sse_response(sse_text)
        assert exc_info.value.code == -32601


# ── Skill Registration ───────────────────────────────────────────────────────


class TestMcpTrinoSkill:
    def setup_method(self):
        SkillRegistry.clear()

    def teardown_method(self):
        SkillRegistry.clear()

    def test_build_args_from_schema(self):
        from genie.skills.mcp_trino import _build_args

        schema = {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "SQL to execute"},
                "limit": {"type": "integer", "description": "Row limit", "default": 100},
            },
            "required": ["sql"],
        }
        args = _build_args(schema)
        assert len(args) == 2
        assert args[0].name == "sql"
        assert args[0].required is True
        assert args[0].type == "str"
        assert args[1].name == "limit"
        assert args[1].required is False
        assert args[1].type == "int"

    def test_skill_name_prefixed_with_mcp(self):
        from genie.skills.mcp_trino import McpTrinoSkill

        mock_client = MagicMock()
        tool_def = {"name": "query", "description": "Run SQL", "inputSchema": {}}
        skill = McpTrinoSkill(tool_def, mock_client)
        assert skill.name == "mcp_query"
        assert skill.group == "mcp_trino"

    def test_skill_run_calls_client(self):
        from genie.skills.mcp_trino import McpTrinoSkill

        mock_client = MagicMock()
        mock_client.call_tool.return_value = '{"rows": []}'
        tool_def = {"name": "query", "description": "Run SQL", "inputSchema": {}}
        skill = McpTrinoSkill(tool_def, mock_client)
        result = skill.run(sql="SELECT 1")
        mock_client.call_tool.assert_called_once_with("query", {"sql": "SELECT 1"})
        assert '"rows"' in result


# ── Config Loading ───────────────────────────────────────────────────────────


class TestLoadMcpConfig:
    @patch.dict("os.environ", {"GENIE_MCP_TRINO_URL": "http://remote:9999"}, clear=False)
    def test_env_overrides_default(self):
        from genie.skills.mcp_trino.client import load_mcp_config
        cfg = load_mcp_config()
        assert cfg.url == "http://remote:9999"

    @patch.dict("os.environ", {"GENIE_MCP_TRINO_ENABLED": "false"}, clear=False)
    def test_env_disables(self):
        from genie.skills.mcp_trino.client import load_mcp_config
        cfg = load_mcp_config()
        assert cfg.enabled is False
