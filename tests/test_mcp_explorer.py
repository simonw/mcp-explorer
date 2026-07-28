import asyncio
import json

import pytest
from click.testing import CliRunner
from mcp import types
from mcp.types.version import LATEST_MODERN_VERSION

import mcp_explorer.cli as cli_module


def weather_tool():
    return types.Tool(
        name="get_weather",
        title="Weather Lookup",
        description="Get the current weather.\nIncludes forecast metadata.",
        inputSchema={
            "type": "object",
            "properties": {
                "location": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string"},
                        "country": {"type": "string"},
                    },
                    "required": ["city"],
                }
            },
            "required": ["location"],
        },
        outputSchema={
            "type": "object",
            "properties": {
                "temperature": {"type": "number"},
            },
            "required": ["temperature"],
        },
        annotations=types.ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
        execution=types.ToolExecution(taskSupport="optional"),
        icons=[
            types.Icon(
                src="https://example.com/weather.png",
                mimeType="image/png",
                sizes=["64x64"],
                theme="light",
            )
        ],
        _meta={"owner": "weather-team"},
    )


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
        "get_weather(city: string)\n" "  Get the current weather.\n" "\n" "get_time()\n"
    )


def test_list_no_truncate(monkeypatch):
    async def mock_fetch_tools(url, stateless):
        return [
            types.Tool(
                name="get_weather",
                description=(
                    "Get the current weather.\n"
                    "\n"
                    "Includes current conditions and forecasts."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "units": {
                            "type": "string",
                            "enum": ["metric", "imperial"],
                            "description": "Units to use.",
                        }
                    },
                },
            )
        ]

    monkeypatch.setattr(cli_module, "fetch_tools", mock_fetch_tools)
    result = CliRunner().invoke(
        cli_module.cli,
        ["list", "-N", "https://example.com/mcp"],
    )

    assert result.exit_code == 0
    assert result.output == (
        "get_weather\n"
        "  Get the current weather.\n"
        "\n"
        "  Includes current conditions and forecasts.\n"
        "  Parameters:\n"
        '    units (string; one of "metric", "imperial", optional)\n'
        "      Units to use.\n"
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
        ["--json", "list", "https://example.com/mcp"],
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
        ["--legacy", "list", "https://example.com/mcp"],
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
    second_tool = asyncio.run(
        cli_module.fetch_tool(
            "https://example.com/mcp",
            "second",
            stateless=True,
        )
    )
    missing_tool = asyncio.run(
        cli_module.fetch_tool(
            "https://example.com/mcp",
            "missing",
            stateless=False,
        )
    )

    assert [tool.name for tool in tools] == ["first", "second"]
    assert [tool.name for tool in legacy_tools] == ["first", "second"]
    assert second_tool.name == "second"
    assert missing_tool is None
    assert modes == [
        LATEST_MODERN_VERSION,
        "legacy",
        LATEST_MODERN_VERSION,
        "legacy",
    ]


def test_inspect(monkeypatch):
    async def mock_fetch_tool(url, name, stateless):
        assert url == "https://example.com/mcp"
        assert name == "get_weather"
        assert stateless is True
        return weather_tool()

    monkeypatch.setattr(cli_module, "fetch_tool", mock_fetch_tool)
    result = CliRunner().invoke(
        cli_module.cli,
        ["inspect", "https://example.com/mcp", "get_weather"],
    )

    assert result.exit_code == 0
    assert result.output.startswith(
        "get_weather - Weather Lookup\n"
        "  Get the current weather.\n"
        "  Includes forecast metadata.\n"
    )
    assert 'Input schema:\n  {\n    "type": "object",' in result.output
    assert '"city": {\n            "type": "string"\n          }' in result.output
    assert 'Output schema:\n  {\n    "type": "object",' in result.output
    assert 'Annotations:\n  {\n    "readOnlyHint": true,' in result.output
    assert 'Execution:\n  {\n    "taskSupport": "optional"\n  }' in result.output
    assert (
        'Icons:\n  [\n    {\n      "src": "https://example.com/weather.png",'
        in result.output
    )
    assert 'Metadata:\n  {\n    "owner": "weather-team"\n  }' in result.output


def test_inspect_json_and_legacy(monkeypatch):
    async def mock_fetch_tool(url, name, stateless):
        assert stateless is False
        return weather_tool()

    monkeypatch.setattr(cli_module, "fetch_tool", mock_fetch_tool)
    result = CliRunner().invoke(
        cli_module.cli,
        [
            "--legacy",
            "--json",
            "inspect",
            "https://example.com/mcp",
            "get_weather",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.output) == weather_tool().model_dump(
        mode="json",
        by_alias=True,
        exclude_none=True,
    )


def test_inspect_missing_tool(monkeypatch):
    async def mock_fetch_tool(url, name, stateless):
        return None

    monkeypatch.setattr(cli_module, "fetch_tool", mock_fetch_tool)
    result = CliRunner().invoke(
        cli_module.cli,
        ["inspect", "https://example.com/mcp", "missing"],
    )

    assert result.exit_code == 1
    assert result.output == "Error: Tool 'missing' not found.\n"


def test_shared_options_are_only_on_the_cli_group():
    runner = CliRunner()
    group_help = runner.invoke(cli_module.cli, ["--help"])
    list_help = runner.invoke(cli_module.cli, ["list", "--help"])

    assert group_help.exit_code == 0
    assert "--json" in group_help.output
    assert "--stateless / --legacy" in group_help.output
    assert "--json" not in list_help.output
    assert "--stateless / --legacy" not in list_help.output


def test_info(monkeypatch):
    info = {
        "url": "https://example.com/mcp",
        "mode": "stateless",
        "negotiation": "server/discover",
        "protocolVersion": "2026-07-28",
        "supportedVersions": ["2026-07-28"],
        "serverInfo": {"name": "example", "version": "1.2.3"},
        "capabilities": {
            "tools": {"listChanged": True},
            "resources": {},
        },
        "instructions": "Use carefully.",
    }

    async def mock_fetch_server_info(url, stateless):
        assert url == "https://example.com/mcp"
        assert stateless is True
        return info

    monkeypatch.setattr(
        cli_module,
        "fetch_server_info",
        mock_fetch_server_info,
    )
    result = CliRunner().invoke(
        cli_module.cli,
        ["info", "https://example.com/mcp"],
    )

    assert result.exit_code == 0
    assert "URL: https://example.com/mcp\n" in result.output
    assert "Mode: stateless\n" in result.output
    assert "Negotiation: server/discover\n" in result.output
    assert "Protocol version: 2026-07-28\n" in result.output
    assert "Supported versions: 2026-07-28\n" in result.output
    assert 'Server info:\n  {\n    "name": "example",' in result.output
    assert 'Capabilities:\n  {\n    "tools": {' in result.output
    assert "Instructions:\n  Use carefully.\n" in result.output


def test_info_json_and_legacy(monkeypatch):
    info = {
        "url": "https://example.com/mcp",
        "mode": "legacy",
        "negotiation": "initialize",
        "protocolVersion": "2025-11-25",
        "supportedVersions": None,
        "serverInfo": {"name": "example", "version": "1.2.3"},
        "capabilities": {"tools": {}},
        "instructions": None,
    }

    async def mock_fetch_server_info(url, stateless):
        assert stateless is False
        return info

    monkeypatch.setattr(
        cli_module,
        "fetch_server_info",
        mock_fetch_server_info,
    )
    result = CliRunner().invoke(
        cli_module.cli,
        ["--json", "--legacy", "info", "https://example.com/mcp"],
    )

    assert result.exit_code == 0
    assert json.loads(result.output) == info


def test_info_accepts_command_local_json_option(monkeypatch):
    info = {
        "url": "https://example.com/mcp",
        "mode": "stateless",
        "negotiation": "server/discover",
        "protocolVersion": "2026-07-28",
        "supportedVersions": ["2026-07-28"],
        "serverInfo": None,
        "capabilities": {"tools": {}},
        "instructions": None,
    }

    async def mock_fetch_server_info(url, stateless):
        return info

    monkeypatch.setattr(
        cli_module,
        "fetch_server_info",
        mock_fetch_server_info,
    )
    result = CliRunner().invoke(
        cli_module.cli,
        ["info", "--json", "https://example.com/mcp"],
    )

    assert result.exit_code == 0
    assert json.loads(result.output) == info


def test_doctor(monkeypatch):
    report = {
        "url": "https://example.com/mcp",
        "selectedMode": "stateless",
        "healthy": True,
        "checks": [
            {
                "mode": "stateless",
                "selected": True,
                "status": "ok",
                "protocolVersion": "2026-07-28",
                "latencyMs": 12.34,
                "toolCount": 3,
                "pages": 2,
            },
            {
                "mode": "legacy",
                "selected": False,
                "status": "error",
                "latencyMs": 2.1,
                "error": "Method not found",
            },
        ],
    }

    async def mock_doctor_server(url, stateless):
        assert url == "https://example.com/mcp"
        assert stateless is True
        return report

    monkeypatch.setattr(cli_module, "doctor_server", mock_doctor_server)
    result = CliRunner().invoke(
        cli_module.cli,
        ["doctor", "https://example.com/mcp"],
    )

    assert result.exit_code == 0
    assert "Selected mode: stateless\n" in result.output
    assert "stateless (selected): ok\n" in result.output
    assert "  Protocol version: 2026-07-28\n" in result.output
    assert "  Tools: 3 across 2 page(s)\n" in result.output
    assert "legacy: error\n" in result.output
    assert "  Error: Method not found\n" in result.output
    assert result.output.endswith("Result: healthy\n")


def test_doctor_json_exits_nonzero_when_selected_mode_fails(monkeypatch):
    report = {
        "url": "https://example.com/mcp",
        "selectedMode": "legacy",
        "healthy": False,
        "checks": [
            {
                "mode": "legacy",
                "selected": True,
                "status": "error",
                "latencyMs": 1.0,
                "error": "Connection failed",
            },
            {
                "mode": "stateless",
                "selected": False,
                "status": "ok",
                "protocolVersion": "2026-07-28",
                "latencyMs": 2.0,
                "toolCount": 1,
                "pages": 1,
            },
        ],
    }

    async def mock_doctor_server(url, stateless):
        assert stateless is False
        return report

    monkeypatch.setattr(cli_module, "doctor_server", mock_doctor_server)
    result = CliRunner().invoke(
        cli_module.cli,
        ["--json", "--legacy", "doctor", "https://example.com/mcp"],
    )

    assert result.exit_code == 1
    assert json.loads(result.output) == report


def test_call_with_repeated_arguments(monkeypatch):
    async def mock_invoke_tool(
        url,
        tool_name,
        arguments_json,
        argument_pairs,
        stateless,
    ):
        assert url == "https://example.com/mcp"
        assert tool_name == "render_svg"
        assert arguments_json is None
        assert argument_pairs == (
            ("source", "graph TD; A-->B"),
            ("options", '{"padding":24}'),
        )
        assert stateless is True
        return types.CallToolResult(
            content=[types.TextContent(text="<svg>diagram</svg>")],
            structuredContent={"ok": True, "width": 640},
        )

    monkeypatch.setattr(cli_module, "invoke_tool", mock_invoke_tool)
    result = CliRunner().invoke(
        cli_module.cli,
        [
            "call",
            "https://example.com/mcp",
            "render_svg",
            "-a",
            "source",
            "graph TD; A-->B",
            "--argument",
            "options",
            '{"padding":24}',
        ],
    )

    assert result.exit_code == 0
    assert result.output == (
        "<svg>diagram</svg>\n"
        "Structured content:\n"
        "  {\n"
        '    "ok": true,\n'
        '    "width": 640\n'
        "  }\n"
    )


def test_call_with_raw_json_and_json_output(monkeypatch):
    call_result = types.CallToolResult(
        content=[types.TextContent(text="done")],
        _meta={"requestId": "abc"},
    )

    async def mock_invoke_tool(
        url,
        tool_name,
        arguments_json,
        argument_pairs,
        stateless,
    ):
        assert arguments_json == '{"source":"graph TD; A-->B"}'
        assert argument_pairs == ()
        return call_result

    monkeypatch.setattr(cli_module, "invoke_tool", mock_invoke_tool)
    result = CliRunner().invoke(
        cli_module.cli,
        [
            "--json",
            "call",
            "https://example.com/mcp",
            "render_svg",
            '{"source":"graph TD; A-->B"}',
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.output) == call_result.model_dump(
        mode="json",
        by_alias=True,
        exclude_none=True,
    )


def test_call_reads_raw_json_from_stdin(monkeypatch):
    async def mock_invoke_tool(
        url,
        tool_name,
        arguments_json,
        argument_pairs,
        stateless,
    ):
        assert arguments_json == '{"source":"from stdin"}\n'
        return types.CallToolResult(
            content=[types.TextContent(text="done")],
        )

    monkeypatch.setattr(cli_module, "invoke_tool", mock_invoke_tool)
    result = CliRunner().invoke(
        cli_module.cli,
        ["call", "https://example.com/mcp", "render_svg", "-"],
        input='{"source":"from stdin"}\n',
    )

    assert result.exit_code == 0
    assert result.output == "done\n"


def test_build_arguments_uses_schema_types_and_overrides_raw_json():
    schema = {
        "type": "object",
        "properties": {
            "source": {"type": "string"},
            "count": {"type": "integer"},
            "enabled": {"type": "boolean"},
            "options": {"type": "object"},
            "tags": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": ["source", "count"],
        "additionalProperties": False,
    }

    arguments = cli_module.build_arguments(
        schema,
        '{"source":"old","count":1}',
        (
            ("source", "graph TD; A-->B"),
            ("count", "2"),
            ("count", "3"),
            ("enabled", "true"),
            ("options", '{"padding":24}'),
            ("tags", '["one","two"]'),
        ),
    )

    assert arguments == {
        "source": "graph TD; A-->B",
        "count": 3,
        "enabled": True,
        "options": {"padding": 24},
        "tags": ["one", "two"],
    }


@pytest.mark.parametrize(
    ("arguments_json", "argument_pairs", "message"),
    (
        ("[]", (), "Raw arguments must be a JSON object"),
        ("not json", (), "Raw arguments must be valid JSON"),
        (
            '{"source":"diagram"}',
            (("count", "not-an-integer"),),
            "Argument 'count' must be valid JSON",
        ),
        (
            '{"source":"diagram"}',
            (("unknown", "value"),),
            "Additional properties are not allowed",
        ),
    ),
)
def test_build_arguments_rejects_invalid_input(
    arguments_json,
    argument_pairs,
    message,
):
    schema = {
        "type": "object",
        "properties": {
            "source": {"type": "string"},
            "count": {"type": "integer"},
        },
        "required": ["source"],
        "additionalProperties": False,
    }

    with pytest.raises(cli_module.ArgumentInputError, match=message):
        cli_module.build_arguments(
            schema,
            arguments_json,
            argument_pairs,
        )


def test_call_tool_error_has_nonzero_exit(monkeypatch):
    async def mock_invoke_tool(*args):
        return types.CallToolResult(
            content=[types.TextContent(text="Rendering failed")],
            isError=True,
        )

    monkeypatch.setattr(cli_module, "invoke_tool", mock_invoke_tool)
    result = CliRunner().invoke(
        cli_module.cli,
        ["call", "https://example.com/mcp", "render_svg"],
    )

    assert result.exit_code == 1
    assert result.output == "Rendering failed\n"


def test_call_wrapped_argument_error_is_a_usage_error(monkeypatch):
    class WrappedError(Exception):
        def __init__(self, exception):
            self.exceptions = [exception]

    async def mock_invoke_tool(*args):
        raise WrappedError(
            cli_module.ArgumentInputError(
                "Argument 'count' must be valid JSON for type integer"
            )
        )

    monkeypatch.setattr(cli_module, "invoke_tool", mock_invoke_tool)
    result = CliRunner().invoke(
        cli_module.cli,
        [
            "call",
            "https://example.com/mcp",
            "counter",
            "-a",
            "count",
            "not-a-number",
        ],
    )

    assert result.exit_code == 2
    assert (
        "Error: Argument 'count' must be valid JSON for type integer\n" in result.output
    )


def test_prompts(monkeypatch):
    prompts = [
        types.Prompt(
            name="code_review",
            title="Code Review",
            description="Review code for correctness.",
            arguments=[
                types.PromptArgument(
                    name="code",
                    description="Code to review.",
                    required=True,
                ),
                types.PromptArgument(
                    name="language",
                    description="Programming language.",
                ),
            ],
        ),
        types.Prompt(name="summarize"),
    ]

    async def mock_fetch_prompts(url, stateless):
        assert url == "https://example.com/mcp"
        assert stateless is True
        return prompts

    monkeypatch.setattr(
        cli_module,
        "fetch_prompts",
        mock_fetch_prompts,
    )
    result = CliRunner().invoke(
        cli_module.cli,
        ["prompts", "https://example.com/mcp"],
    )

    assert result.exit_code == 0
    assert result.output == (
        "code_review - Code Review\n"
        "  Review code for correctness.\n"
        "  Arguments:\n"
        "    code (required)\n"
        "      Code to review.\n"
        "    language (optional)\n"
        "      Programming language.\n"
        "\n"
        "summarize\n"
        "  Arguments: none\n"
    )


def test_prompts_command_local_json_and_legacy(monkeypatch):
    prompts = [
        types.Prompt(
            name="code_review",
            arguments=[types.PromptArgument(name="code", required=True)],
        )
    ]

    async def mock_fetch_prompts(url, stateless):
        assert stateless is False
        return prompts

    monkeypatch.setattr(
        cli_module,
        "fetch_prompts",
        mock_fetch_prompts,
    )
    result = CliRunner().invoke(
        cli_module.cli,
        [
            "prompts",
            "--legacy",
            "--json",
            "https://example.com/mcp",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.output) == [
        prompt.model_dump(
            mode="json",
            by_alias=True,
            exclude_none=True,
        )
        for prompt in prompts
    ]


def test_prompts_with_no_prompts(monkeypatch):
    async def mock_fetch_prompts(url, stateless):
        return []

    monkeypatch.setattr(
        cli_module,
        "fetch_prompts",
        mock_fetch_prompts,
    )
    result = CliRunner().invoke(
        cli_module.cli,
        ["prompts", "https://example.com/mcp"],
    )

    assert result.exit_code == 0
    assert result.output == "No prompts available.\n"


def test_resources(monkeypatch):
    resources = [
        types.Resource(
            name="project-readme",
            title="Project README",
            uri="file:///project/README.md",
            description="Project documentation.",
            mimeType="text/markdown",
            size=1234,
        ),
        types.Resource(
            name="settings",
            uri="config://settings",
        ),
    ]

    async def mock_fetch_resources(url, stateless):
        assert url == "https://example.com/mcp"
        assert stateless is True
        return resources

    monkeypatch.setattr(
        cli_module,
        "fetch_resources",
        mock_fetch_resources,
    )
    result = CliRunner().invoke(
        cli_module.cli,
        ["resources", "https://example.com/mcp"],
    )

    assert result.exit_code == 0
    assert result.output == (
        "project-readme - Project README\n"
        "  file:///project/README.md\n"
        "  Project documentation.\n"
        "  MIME type: text/markdown\n"
        "  Size: 1234 bytes\n"
        "\n"
        "settings\n"
        "  config://settings\n"
    )


def test_resources_respect_shared_json_and_legacy(monkeypatch):
    resources = [
        types.Resource(
            name="settings",
            uri="config://settings",
        )
    ]

    async def mock_fetch_resources(url, stateless):
        assert stateless is False
        return resources

    monkeypatch.setattr(
        cli_module,
        "fetch_resources",
        mock_fetch_resources,
    )
    result = CliRunner().invoke(
        cli_module.cli,
        [
            "--legacy",
            "--json",
            "resources",
            "https://example.com/mcp",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.output) == [
        resource.model_dump(
            mode="json",
            by_alias=True,
            exclude_none=True,
        )
        for resource in resources
    ]


def test_resources_accept_command_local_json_and_legacy(monkeypatch):
    resources = [
        types.Resource(
            name="settings",
            uri="config://settings",
        )
    ]

    async def mock_fetch_resources(url, stateless):
        assert stateless is False
        return resources

    monkeypatch.setattr(
        cli_module,
        "fetch_resources",
        mock_fetch_resources,
    )
    result = CliRunner().invoke(
        cli_module.cli,
        [
            "resources",
            "--legacy",
            "--json",
            "https://example.com/mcp",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.output)[0]["uri"] == "config://settings"


def test_prompt_and_resource_fetchers_follow_pagination(monkeypatch):
    modes = []

    class MockClient:
        def __init__(self, url, *, mode):
            assert url == "https://example.com/mcp"
            modes.append(mode)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def list_prompts(self, *, cursor):
            if cursor is None:
                return types.ListPromptsResult(
                    prompts=[types.Prompt(name="first")],
                    nextCursor="prompts-page-2",
                )
            assert cursor == "prompts-page-2"
            return types.ListPromptsResult(prompts=[types.Prompt(name="second")])

        async def list_resources(self, *, cursor):
            if cursor is None:
                return types.ListResourcesResult(
                    resources=[
                        types.Resource(
                            name="first",
                            uri="test://first",
                        )
                    ],
                    nextCursor="resources-page-2",
                )
            assert cursor == "resources-page-2"
            return types.ListResourcesResult(
                resources=[
                    types.Resource(
                        name="second",
                        uri="test://second",
                    )
                ]
            )

    monkeypatch.setattr(cli_module, "Client", MockClient)

    prompts = asyncio.run(
        cli_module.fetch_prompts(
            "https://example.com/mcp",
            stateless=True,
        )
    )
    resources = asyncio.run(
        cli_module.fetch_resources(
            "https://example.com/mcp",
            stateless=False,
        )
    )

    assert [prompt.name for prompt in prompts] == ["first", "second"]
    assert [resource.name for resource in resources] == ["first", "second"]
    assert modes == [LATEST_MODERN_VERSION, "legacy"]
