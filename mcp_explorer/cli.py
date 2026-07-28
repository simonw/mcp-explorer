import asyncio
import json

import click
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client


@click.group()
@click.version_option()
def cli():
    """CLI tool for exploring MCP servers."""


async def fetch_tools(url):
    """Connect to an MCP server and return all of its tools."""
    tools = []
    cursor = None

    async with streamable_http_client(url) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            while True:
                params = (
                    types.PaginatedRequestParams(cursor=cursor) if cursor else None
                )
                result = await session.list_tools(params=params)
                tools.extend(result.tools)
                cursor = result.next_cursor
                if not cursor:
                    return tools


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
        details.append(f"pattern {schema['pattern']!r}")

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
def list_tools(url, json_output):
    """List the tools exposed by an MCP server at URL."""
    try:
        tools = asyncio.run(fetch_tools(url))
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
