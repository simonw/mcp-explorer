import asyncio
import json
import time
from dataclasses import dataclass

import click
from mcp import types
from mcp.client import Client
from mcp.types.version import LATEST_MODERN_VERSION


@dataclass(frozen=True)
class CliOptions:
    json_output: bool
    stateless: bool


@click.group()
@click.version_option()
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Output JSON.",
)
@click.option(
    "--stateless/--legacy",
    default=True,
    help="Force stateless MCP 2 (default) or the legacy initialize handshake.",
)
@click.pass_context
def cli(context, json_output, stateless):
    """CLI tool for exploring MCP servers."""
    context.obj = CliOptions(
        json_output=json_output,
        stateless=stateless,
    )


def _client_mode(stateless):
    return LATEST_MODERN_VERSION if stateless else "legacy"


async def fetch_tools(url, stateless):
    """Connect to an MCP server and return all of its tools."""
    tools = []
    cursor = None

    async with Client(url, mode=_client_mode(stateless)) as client:
        while True:
            result = await client.list_tools(cursor=cursor)
            tools.extend(result.tools)
            cursor = result.next_cursor
            if not cursor:
                return tools


async def fetch_tool(url, name, stateless):
    """Connect to an MCP server and return the named tool, if it exists."""
    cursor = None

    async with Client(url, mode=_client_mode(stateless)) as client:
        while True:
            result = await client.list_tools(cursor=cursor)
            for tool in result.tools:
                if tool.name == name:
                    return tool
            cursor = result.next_cursor
            if not cursor:
                return None


async def _discover_stateless(client):
    raw_result = await client.session.send_discover(LATEST_MODERN_VERSION)
    result = types.DiscoverResult.model_validate(raw_result)
    client.session.adopt(result)
    return result


def _model_dict(model):
    if model is None:
        return None
    return model.model_dump(
        mode="json",
        by_alias=True,
        exclude_none=True,
    )


async def fetch_server_info(url, stateless):
    """Return protocol and server metadata for the selected mode."""
    async with Client(
        url,
        mode=_client_mode(stateless),
        cache=None,
    ) as client:
        discover_result = None
        if stateless:
            discover_result = await _discover_stateless(client)

        return {
            "url": url,
            "mode": "stateless" if stateless else "legacy",
            "negotiation": "server/discover" if stateless else "initialize",
            "protocolVersion": client.protocol_version,
            "supportedVersions": (
                discover_result.supported_versions if discover_result else None
            ),
            "serverInfo": _model_dict(client.server_info),
            "capabilities": _model_dict(client.server_capabilities),
            "instructions": client.instructions,
        }


def _exception_message(exception):
    current = exception
    while getattr(current, "exceptions", None):
        current = current.exceptions[0]
    return str(current) or type(current).__name__


async def _doctor_mode(url, stateless, selected):
    started = time.perf_counter()
    mode_name = "stateless" if stateless else "legacy"

    try:
        async with Client(
            url,
            mode=_client_mode(stateless),
            cache=None,
        ) as client:
            if stateless:
                await _discover_stateless(client)

            cursor = None
            seen_cursors = set()
            pages = 0
            tool_count = 0

            while True:
                result = await client.list_tools(cursor=cursor)
                pages += 1
                tool_count += len(result.tools)
                cursor = result.next_cursor
                if not cursor:
                    break
                if cursor in seen_cursors:
                    raise RuntimeError(
                        f"Server repeated pagination cursor {cursor!r}"
                    )
                seen_cursors.add(cursor)

            return {
                "mode": mode_name,
                "selected": selected,
                "status": "ok",
                "protocolVersion": client.protocol_version,
                "latencyMs": round(
                    (time.perf_counter() - started) * 1000,
                    2,
                ),
                "toolCount": tool_count,
                "pages": pages,
                "serverInfo": _model_dict(client.server_info),
                "capabilities": _model_dict(client.server_capabilities),
            }
    except Exception as ex:
        return {
            "mode": mode_name,
            "selected": selected,
            "status": "error",
            "latencyMs": round(
                (time.perf_counter() - started) * 1000,
                2,
            ),
            "error": _exception_message(ex),
        }


async def doctor_server(url, stateless):
    """Check both protocol modes, putting the selected mode first."""
    modes = (stateless, not stateless)
    checks = await asyncio.gather(
        *(
            _doctor_mode(
                url,
                mode,
                selected=(mode == stateless),
            )
            for mode in modes
        )
    )
    return {
        "url": url,
        "selectedMode": "stateless" if stateless else "legacy",
        "healthy": checks[0]["status"] == "ok",
        "checks": list(checks),
    }


def _first_description_line(description):
    if not description:
        return None
    return next(
        (line.strip() for line in description.splitlines() if line.strip()),
        None,
    )


def _full_description_lines(description):
    if not description:
        return []
    return description.strip().splitlines()


def _short_schema_type(schema):
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        return " | ".join(schema_type)
    if schema_type == "array":
        return f"array[{_short_schema_type(schema.get('items', {}))}]"
    if schema_type:
        return schema_type
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]

    alternatives = schema.get("anyOf") or schema.get("oneOf")
    if alternatives:
        return " | ".join(_short_schema_type(item) for item in alternatives)
    return "value"


def _schema_type(schema):
    label = _short_schema_type(schema)
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


def _tool_heading(tool, include_signature=False):
    heading = tool.name
    if include_signature:
        required = set(tool.input_schema.get("required", []))
        parameters = []
        for name, schema in tool.input_schema.get("properties", {}).items():
            optional = "" if name in required else "?"
            parameters.append(
                f"{name}{optional}: {_short_schema_type(schema)}"
            )
        heading = f"{heading}({', '.join(parameters)})"
    if tool.title:
        heading = f"{heading} - {tool.title}"
    return heading


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
        for line in _full_description_lines(schema.get("description")):
            click.echo(f"      {line}" if line else "")


def _tool_dict(tool):
    return _model_dict(tool)


def _render_json_section(label, value):
    if value is None:
        return

    click.echo(f"{label}:")
    for line in json.dumps(value, indent=2).splitlines():
        click.echo(f"  {line}")


@cli.command(name="list")
@click.argument("url")
@click.option(
    "-N",
    "--no-truncate",
    is_flag=True,
    help="Show full descriptions and detailed parameters.",
)
@click.pass_obj
def list_tools(options, url, no_truncate):
    """List the tools exposed by an MCP server at URL."""
    try:
        tools = asyncio.run(fetch_tools(url, options.stateless))
    except Exception as ex:
        raise click.ClickException(_exception_message(ex)) from ex

    if options.json_output:
        click.echo(json.dumps([_tool_dict(tool) for tool in tools], indent=2))
        return

    if not tools:
        click.echo("No tools available.")
        return

    for index, tool in enumerate(tools):
        if index:
            click.echo()

        if no_truncate:
            click.echo(_tool_heading(tool))
            for line in _full_description_lines(tool.description):
                click.echo(f"  {line}" if line else "")
            _render_parameters(tool.input_schema)
        else:
            click.echo(_tool_heading(tool, include_signature=True))
            summary = _first_description_line(tool.description)
            if summary:
                click.echo(f"  {summary}")


@cli.command(name="inspect")
@click.argument("url")
@click.argument("tool_name")
@click.pass_obj
def inspect_tool(options, url, tool_name):
    """Inspect one tool exposed by an MCP server at URL."""
    try:
        tool = asyncio.run(
            fetch_tool(
                url,
                tool_name,
                options.stateless,
            )
        )
    except Exception as ex:
        raise click.ClickException(_exception_message(ex)) from ex

    if tool is None:
        raise click.ClickException(f"Tool {tool_name!r} not found.")

    if options.json_output:
        click.echo(json.dumps(_tool_dict(tool), indent=2))
        return

    click.echo(_tool_heading(tool))
    for line in _full_description_lines(tool.description):
        click.echo(f"  {line}" if line else "")

    _render_json_section("Input schema", tool.input_schema)
    _render_json_section("Output schema", tool.output_schema)
    _render_json_section("Annotations", _model_dict(tool.annotations))
    _render_json_section("Execution", _model_dict(tool.execution))
    _render_json_section(
        "Icons",
        [_model_dict(icon) for icon in tool.icons] if tool.icons else None,
    )
    _render_json_section("Metadata", tool.meta)


@cli.command(name="info")
@click.argument("url")
@click.pass_obj
def info_command(options, url):
    """Show protocol and metadata for an MCP server at URL."""
    try:
        info = asyncio.run(fetch_server_info(url, options.stateless))
    except Exception as ex:
        raise click.ClickException(_exception_message(ex)) from ex

    if options.json_output:
        click.echo(json.dumps(info, indent=2))
        return

    click.echo(f"URL: {info['url']}")
    click.echo(f"Mode: {info['mode']}")
    click.echo(f"Negotiation: {info['negotiation']}")
    click.echo(f"Protocol version: {info['protocolVersion']}")
    if info["supportedVersions"]:
        click.echo(
            "Supported versions: "
            + ", ".join(info["supportedVersions"])
        )
    _render_json_section("Server info", info["serverInfo"])
    _render_json_section("Capabilities", info["capabilities"])
    if info["instructions"]:
        click.echo("Instructions:")
        for line in info["instructions"].splitlines():
            click.echo(f"  {line}" if line else "")


def _render_doctor(report):
    click.echo(f"URL: {report['url']}")
    click.echo(f"Selected mode: {report['selectedMode']}")

    for check in report["checks"]:
        click.echo()
        selected = " (selected)" if check["selected"] else ""
        click.echo(f"{check['mode']}{selected}: {check['status']}")
        click.echo(f"  Latency: {check['latencyMs']:.2f} ms")
        if check["status"] == "ok":
            click.echo(
                f"  Protocol version: {check['protocolVersion']}"
            )
            click.echo(
                f"  Tools: {check['toolCount']} "
                f"across {check['pages']} page(s)"
            )
            capabilities = check.get("capabilities") or {}
            if capabilities:
                click.echo(
                    "  Capabilities: " + ", ".join(capabilities)
                )
        else:
            click.echo(f"  Error: {check['error']}")

    click.echo()
    click.echo(
        "Result: " + ("healthy" if report["healthy"] else "unhealthy")
    )


@cli.command(name="doctor")
@click.argument("url")
@click.pass_obj
def doctor_command(options, url):
    """Check stateless and legacy compatibility for an MCP server at URL."""
    report = asyncio.run(doctor_server(url, options.stateless))

    if options.json_output:
        click.echo(json.dumps(report, indent=2))
    else:
        _render_doctor(report)

    if not report["healthy"]:
        raise click.exceptions.Exit(1)
