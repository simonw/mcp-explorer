import asyncio
import json

import click
from mcp.client import Client
from mcp.types.version import LATEST_MODERN_VERSION


@click.group()
@click.version_option()
def cli():
    """CLI tool for exploring MCP servers."""


async def fetch_tools(url, stateless):
    """Connect to an MCP server and return all of its tools."""
    tools = []
    cursor = None
    mode = LATEST_MODERN_VERSION if stateless else "legacy"

    async with Client(url, mode=mode) as client:
        while True:
            result = await client.list_tools(cursor=cursor)
            tools.extend(result.tools)
            cursor = result.next_cursor
            if not cursor:
                return tools


async def fetch_tool(url, name, stateless):
    """Connect to an MCP server and return the named tool, if it exists."""
    cursor = None
    mode = LATEST_MODERN_VERSION if stateless else "legacy"

    async with Client(url, mode=mode) as client:
        while True:
            result = await client.list_tools(cursor=cursor)
            for tool in result.tools:
                if tool.name == name:
                    return tool
            cursor = result.next_cursor
            if not cursor:
                return None


def _description_lines(description):
    if not description:
        return []

    lines = []
    for line in description.strip().splitlines():
        if not line.strip() and lines:
            break
        if line.strip():
            lines.append(line.strip())
    return lines


def _schema_type(schema):
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        label = " | ".join(schema_type)
    elif schema_type == "array":
        label = f"array[{_schema_type(schema.get('items', {}))}]"
    elif schema_type:
        label = schema_type
    elif "$ref" in schema:
        label = schema["$ref"].rsplit("/", 1)[-1]
    else:
        alternatives = schema.get("anyOf") or schema.get("oneOf")
        if alternatives:
            label = " | ".join(_schema_type(item) for item in alternatives)
        else:
            label = "value"

    details = []
    if "enum" in schema:
        details.append(
            "one of " + ", ".join(json.dumps(value) for value in schema["enum"])
        )
    if "const" in schema:
        details.append(f"must be {json.dumps(schema['const'])}")
    if "default" in schema:
        details.append(f"default {json.dumps(schema['default'])}")
    if "minimum" in schema:
        details.append(f">= {schema['minimum']}")
    if "exclusiveMinimum" in schema:
        details.append(f"> {schema['exclusiveMinimum']}")
    if "maximum" in schema:
        details.append(f"<= {schema['maximum']}")
    if "exclusiveMaximum" in schema:
        details.append(f"< {schema['exclusiveMaximum']}")
    if "minItems" in schema:
        details.append(f"at least {schema['minItems']} item(s)")
    if "maxItems" in schema:
        details.append(f"at most {schema['maxItems']} item(s)")
    if "pattern" in schema:
        details.append("pattern constrained")

    if details:
        return f"{label}; {'; '.join(details)}"
    return label


def _render_parameters(input_schema):
    properties = input_schema.get("properties", {})
    required = set(input_schema.get("required", []))

    if not properties:
        click.echo("  Parameters: none")
        return

    click.echo("  Parameters:")
    for name, schema in properties.items():
        requirement = "required" if name in required else "optional"
        click.echo(f"    {name} ({_schema_type(schema)}, {requirement})")
        for line in _description_lines(schema.get("description")):
            click.echo(f"      {line}")


@cli.command(name="list")
@click.argument("url")
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Output the complete tool definitions as JSON.",
)
@click.option(
    "--stateless/--legacy",
    default=True,
    help="Force stateless MCP 2 (default) or the legacy initialize handshake.",
)
def list_tools(url, json_output, stateless):
    """List the tools exposed by an MCP server at URL."""
    try:
        tools = asyncio.run(fetch_tools(url, stateless))
    except Exception as ex:
        raise click.ClickException(str(ex)) from ex

    if json_output:
        click.echo(
            json.dumps(
                [
                    tool.model_dump(
                        mode="json",
                        by_alias=True,
                        exclude_none=True,
                    )
                    for tool in tools
                ],
                indent=2,
            )
        )
        return

    if not tools:
        click.echo("No tools available.")
        return

    for index, tool in enumerate(tools):
        if index:
            click.echo()
        heading = tool.name
        if tool.title:
            heading = f"{heading} - {tool.title}"
        click.echo(heading)
        for line in _description_lines(tool.description):
            click.echo(f"  {line}")
        _render_parameters(tool.input_schema)


def _tool_dict(tool):
    return tool.model_dump(
        mode="json",
        by_alias=True,
        exclude_none=True,
    )


def _render_json_section(label, value):
    if value is None:
        return

    click.echo(f"{label}:")
    for line in json.dumps(value, indent=2).splitlines():
        click.echo(f"  {line}")


@cli.command(name="inspect")
@click.argument("url")
@click.argument("tool_name")
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Output the complete tool definition as JSON.",
)
@click.option(
    "--stateless/--legacy",
    default=True,
    help="Force stateless MCP 2 (default) or the legacy initialize handshake.",
)
def inspect_tool(url, tool_name, json_output, stateless):
    """Inspect one tool exposed by an MCP server at URL."""
    try:
        tool = asyncio.run(fetch_tool(url, tool_name, stateless))
    except Exception as ex:
        raise click.ClickException(str(ex)) from ex

    if tool is None:
        raise click.ClickException(f"Tool {tool_name!r} not found.")

    if json_output:
        click.echo(json.dumps(_tool_dict(tool), indent=2))
        return

    heading = tool.name
    if tool.title:
        heading = f"{heading} - {tool.title}"
    click.echo(heading)
    if tool.description:
        for line in tool.description.strip().splitlines():
            click.echo(f"  {line}" if line else "")

    _render_json_section("Input schema", tool.input_schema)
    _render_json_section("Output schema", tool.output_schema)
    _render_json_section(
        "Annotations",
        (
            tool.annotations.model_dump(
                mode="json",
                by_alias=True,
                exclude_none=True,
            )
            if tool.annotations
            else None
        ),
    )
    _render_json_section(
        "Execution",
        (
            tool.execution.model_dump(
                mode="json",
                by_alias=True,
                exclude_none=True,
            )
            if tool.execution
            else None
        ),
    )
    _render_json_section(
        "Icons",
        (
            [
                icon.model_dump(
                    mode="json",
                    by_alias=True,
                    exclude_none=True,
                )
                for icon in tool.icons
            ]
            if tool.icons
            else None
        ),
    )
    _render_json_section("Metadata", tool.meta)
