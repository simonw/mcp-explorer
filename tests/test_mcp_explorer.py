import asyncio
import json

from click.testing import CliRunner
from mcp import types
from mcp.types.version import LATEST_MODERN_VERSION

import mcp_explorer.cli as cli_module


def test_version():
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(cli_module.cli, ["--version"])
        assert result.exit_code == 0
        assert result.output.startswith("cli, version ")


def test_list(monkeypatch):
    async def mock_fetch_tools(url, stateless):
        assert url == "https://example.com/mcp"
        assert stateless is True
        return [
            types.Tool(
                name="get_weather",
                description="Get the current weather.\nMore details follow.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "city": {
                            "type": "string",
                            "description": "City to look up.",
                        }
                    },
                    "required": ["city"],
                },
            ),
            types.Tool(
                name="get_time",
                description=None,
                inputSchema={"type": "object"},
            ),
        ]

    monkeypatch.setattr(cli_module, "fetch_tools", mock_fetch_tools)
    result = CliRunner().invoke(
        cli_module.cli,
        ["list", "https://example.com/mcp"],
    )

    assert result.exit_code == 0
    assert result.output == (
        "get_weather\n"
        "  Get the current weather.\n"
        "  More details follow.\n"
        "  Parameters:\n"
        "    city (string, required)\n"
        "      City to look up.\n"
        "\n"
        "get_time\n"
        "  Parameters: none\n"
    )


def test_list_json(monkeypatch):
    async def mock_fetch_tools(url, stateless):
        assert stateless is True
        return [
            types.Tool(
                name="get_weather",
                description="Get the current weather.",
                inputSchema={
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                },
            )
        ]

    monkeypatch.setattr(cli_module, "fetch_tools", mock_fetch_tools)
    result = CliRunner().invoke(
        cli_module.cli,
        ["list", "https://example.com/mcp", "--json"],
    )

    assert result.exit_code == 0
    assert json.loads(result.output) == [
        {
            "name": "get_weather",
            "description": "Get the current weather.",
            "inputSchema": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
            },
        }
    ]


def test_list_with_no_tools(monkeypatch):
    async def mock_fetch_tools(url, stateless):
        assert stateless is True
        return []

    monkeypatch.setattr(cli_module, "fetch_tools", mock_fetch_tools)
    result = CliRunner().invoke(
        cli_module.cli,
        ["list", "https://example.com/mcp"],
    )

    assert result.exit_code == 0
    assert result.output == "No tools available.\n"


def test_legacy_option(monkeypatch):
    async def mock_fetch_tools(url, stateless):
        assert stateless is False
        return []

    monkeypatch.setattr(cli_module, "fetch_tools", mock_fetch_tools)
    result = CliRunner().invoke(
        cli_module.cli,
        ["list", "--legacy", "https://example.com/mcp"],
    )

    assert result.exit_code == 0


def test_fetch_tools_forces_protocol_mode_and_follows_pagination(monkeypatch):
    tool_pages = [
        types.ListToolsResult(
            tools=[
                types.Tool(
                    name="first",
                    inputSchema={"type": "object"},
                )
            ],
            nextCursor="next-page",
        ),
        types.ListToolsResult(
            tools=[
                types.Tool(
                    name="second",
                    inputSchema={"type": "object"},
                )
            ],
        ),
    ]
    modes = []

    class MockClient:
        def __init__(self, url, *, mode):
            assert url == "https://example.com/mcp"
            modes.append(mode)
            self.page = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def list_tools(self, *, cursor):
            expected_cursor = None if self.page == 0 else "next-page"
            assert cursor == expected_cursor
            result = tool_pages[self.page]
            self.page += 1
            return result

    monkeypatch.setattr(cli_module, "Client", MockClient)

    tools = asyncio.run(
        cli_module.fetch_tools("https://example.com/mcp", stateless=True)
    )
    legacy_tools = asyncio.run(
        cli_module.fetch_tools("https://example.com/mcp", stateless=False)
    )

    assert [tool.name for tool in tools] == ["first", "second"]
    assert [tool.name for tool in legacy_tools] == ["first", "second"]
    assert modes == [LATEST_MODERN_VERSION, "legacy"]
