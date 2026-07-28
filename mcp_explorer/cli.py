import asyncio
import json
import time
from dataclasses import dataclass

import click
import jsonschema
from mcp import types
from mcp.client import Client
from mcp.types.version import LATEST_MODERN_VERSION


@dataclass(frozen=True)
class CliOptions:
    json_output: bool
    stateless: bool


class ArgumentInputError(ValueError):
    pass


class ToolNotFoundError(ValueError):
    pass


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


async def fetch_prompts(url, stateless):
    """Connect to an MCP server and return all of its prompts."""
    prompts = []
    cursor = None

    async with Client(url, mode=_client_mode(stateless)) as client:
        while True:
            result = await client.list_prompts(cursor=cursor)
            prompts.extend(result.prompts)
            cursor = result.next_cursor
            if not cursor:
                return prompts


async def fetch_resources(url, stateless):
    """Connect to an MCP server and return all of its resources."""
    resources = []
    cursor = None

    async with Client(url, mode=_client_mode(stateless)) as client:
        while True:
            result = await client.list_resources(cursor=cursor)
            resources.extend(result.resources)
            cursor = result.next_cursor
            if not cursor:
                return resources


async def fetch_tool(url, name, stateless):
    """Connect to an MCP server and return the named tool, if it exists."""
    async with Client(url, mode=_client_mode(stateless)) as client:
        return await _find_tool(client, name)


async def _find_tool(client, name):
    cursor = None

    while True:
        result = await client.list_tools(cursor=cursor)
        for tool in result.tools:
            if tool.name == name:
                return tool
        cursor = result.next_cursor
        if not cursor:
            return None


def _resolve_local_ref(schema, root_schema):
    current = schema
    seen = set()

    while isinstance(current, dict):
        reference = current.get("$ref")
        if not reference or not reference.startswith("#/"):
            return current
        if reference in seen:
            return current
        seen.add(reference)

        resolved = root_schema
        try:
            for part in reference[2:].split("/"):
                part = part.replace("~1", "/").replace("~0", "~")
                resolved = resolved[part]
        except (KeyError, TypeError):
            return current
        current = resolved

    return schema


def _schema_allows_string(schema, root_schema):
    schema = _resolve_local_ref(schema, root_schema)
    schema_type = schema.get("type")
    if schema_type == "string":
        return True
    if isinstance(schema_type, list) and "string" in schema_type:
        return True
    if isinstance(schema.get("const"), str):
        return True
    if any(isinstance(value, str) for value in schema.get("enum", [])):
        return True

    alternatives = schema.get("anyOf") or schema.get("oneOf")
    if alternatives:
        return any(
            _schema_allows_string(item, root_schema)
            for item in alternatives
        )
    if schema.get("allOf"):
        return all(
            _schema_allows_string(item, root_schema)
            for item in schema["allOf"]
        )
    return False


def _schema_requires_json(schema, root_schema):
    schema = _resolve_local_ref(schema, root_schema)
    if not schema or _schema_allows_string(schema, root_schema):
        return False
    return any(
        key in schema
        for key in (
            "type",
            "const",
            "enum",
            "anyOf",
            "oneOf",
            "allOf",
        )
    )


def _parse_argument_value(name, value, schema, root_schema):
    if _schema_allows_string(schema, root_schema):
        return value

    try:
        return json.loads(value)
    except json.JSONDecodeError as ex:
        if _schema_requires_json(schema, root_schema):
            expected_type = _short_schema_type(
                _resolve_local_ref(schema, root_schema)
            )
            raise ArgumentInputError(
                f"Argument {name!r} must be valid JSON "
                f"for type {expected_type}"
            ) from ex
        return value


def build_arguments(input_schema, arguments_json, argument_pairs):
    """Combine raw JSON and individual arguments, then validate them."""
    if arguments_json is None:
        arguments = {}
    else:
        try:
            arguments = json.loads(arguments_json)
        except json.JSONDecodeError as ex:
            raise ArgumentInputError(
                f"Raw arguments must be valid JSON: {ex.msg}"
            ) from ex
        if not isinstance(arguments, dict):
            raise ArgumentInputError("Raw arguments must be a JSON object")

    properties = input_schema.get("properties", {})
    additional_properties = input_schema.get("additionalProperties", {})
    for name, value in argument_pairs:
        parameter_schema = properties.get(name)
        if parameter_schema is None:
            parameter_schema = (
                additional_properties
                if isinstance(additional_properties, dict)
                else {}
            )
        arguments[name] = _parse_argument_value(
            name,
            value,
            parameter_schema,
            input_schema,
        )

    try:
        validator_class = jsonschema.validators.validator_for(input_schema)
        validator_class.check_schema(input_schema)
        validation_errors = sorted(
            validator_class(input_schema).iter_errors(arguments),
            key=lambda error: list(error.absolute_path),
        )
    except jsonschema.exceptions.SchemaError as ex:
        raise ArgumentInputError(
            f"Tool input schema is invalid: {ex.message}"
        ) from ex

    if validation_errors:
        error = validation_errors[0]
        path = ".".join(str(part) for part in error.absolute_path)
        message = f"{path}: {error.message}" if path else error.message
        raise ArgumentInputError(
            f"Arguments do not match input schema: {message}"
        )

    return arguments


async def invoke_tool(
    url,
    tool_name,
    arguments_json,
    argument_pairs,
    stateless,
):
    """Find, validate, and invoke a tool using one MCP connection."""
    async with Client(url, mode=_client_mode(stateless)) as client:
        tool = await _find_tool(client, tool_name)
        if tool is None:
            raise ToolNotFoundError(f"Tool {tool_name!r} not found.")
        arguments = build_arguments(
            tool.input_schema,
            arguments_json,
            argument_pairs,
        )
        return await client.call_tool(tool_name, arguments)


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


def _find_nested_exception(exception, exception_type):
    if isinstance(exception, exception_type):
        return exception
    for nested in getattr(exception, "exceptions", ()):
        match = _find_nested_exception(nested, exception_type)
        if match is not None:
            return match
    return None


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


def _listing_mode(options, command_legacy):
    return options.stateless and not command_legacy


def _listing_json(options, command_json_output):
    return options.json_output or command_json_output


@cli.command(name="prompts")
@click.argument("url")
@click.option(
    "--json",
    "command_json_output",
    is_flag=True,
    help="Output complete prompt definitions as JSON.",
)
@click.option(
    "--legacy",
    "command_legacy",
    is_flag=True,
    help="Force the legacy initialize handshake.",
)
@click.pass_obj
def prompts_command(
    options,
    url,
    command_json_output,
    command_legacy,
):
    """List the prompts exposed by an MCP server at URL."""
    try:
        prompts = asyncio.run(
            fetch_prompts(
                url,
                _listing_mode(options, command_legacy),
            )
        )
    except Exception as ex:
        raise click.ClickException(_exception_message(ex)) from ex

    if _listing_json(options, command_json_output):
        click.echo(
            json.dumps(
                [_model_dict(prompt) for prompt in prompts],
                indent=2,
            )
        )
        return

    if not prompts:
        click.echo("No prompts available.")
        return

    for index, prompt in enumerate(prompts):
        if index:
            click.echo()
        heading = prompt.name
        if prompt.title:
            heading = f"{heading} - {prompt.title}"
        click.echo(heading)
        summary = _first_description_line(prompt.description)
        if summary:
            click.echo(f"  {summary}")

        if not prompt.arguments:
            click.echo("  Arguments: none")
            continue
        click.echo("  Arguments:")
        for argument in prompt.arguments:
            requirement = "required" if argument.required else "optional"
            argument_heading = argument.name
            if argument.title:
                argument_heading = f"{argument_heading} - {argument.title}"
            click.echo(f"    {argument_heading} ({requirement})")
            description = _first_description_line(argument.description)
            if description:
                click.echo(f"      {description}")


@cli.command(name="resources")
@click.argument("url")
@click.option(
    "--json",
    "command_json_output",
    is_flag=True,
    help="Output complete resource definitions as JSON.",
)
@click.option(
    "--legacy",
    "command_legacy",
    is_flag=True,
    help="Force the legacy initialize handshake.",
)
@click.pass_obj
def resources_command(
    options,
    url,
    command_json_output,
    command_legacy,
):
    """List the resources exposed by an MCP server at URL."""
    try:
        resources = asyncio.run(
            fetch_resources(
                url,
                _listing_mode(options, command_legacy),
            )
        )
    except Exception as ex:
        raise click.ClickException(_exception_message(ex)) from ex

    if _listing_json(options, command_json_output):
        click.echo(
            json.dumps(
                [_model_dict(resource) for resource in resources],
                indent=2,
            )
        )
        return

    if not resources:
        click.echo("No resources available.")
        return

    for index, resource in enumerate(resources):
        if index:
            click.echo()
        heading = resource.name
        if resource.title:
            heading = f"{heading} - {resource.title}"
        click.echo(heading)
        click.echo(f"  {resource.uri}")
        summary = _first_description_line(resource.description)
        if summary:
            click.echo(f"  {summary}")
        if resource.mime_type:
            click.echo(f"  MIME type: {resource.mime_type}")
        if resource.size is not None:
            click.echo(f"  Size: {resource.size} bytes")


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


def _render_call_result(result):
    for content in result.content:
        if isinstance(content, types.TextContent):
            click.echo(content.text)
        elif isinstance(content, types.ImageContent):
            click.echo(
                f"[image: {content.mime_type}, "
                f"{len(content.data)} base64 characters]"
            )
        elif isinstance(content, types.AudioContent):
            click.echo(
                f"[audio: {content.mime_type}, "
                f"{len(content.data)} base64 characters]"
            )
        elif isinstance(content, types.ResourceLink):
            click.echo(f"[resource: {content.name} ({content.uri})]")
        elif isinstance(content, types.EmbeddedResource):
            resource = content.resource
            if isinstance(resource, types.TextResourceContents):
                click.echo(resource.text)
            else:
                mime_type = resource.mime_type or "application/octet-stream"
                click.echo(
                    f"[resource: {resource.uri}, {mime_type}, "
                    f"{len(resource.blob)} base64 characters]"
                )
        else:
            _render_json_section("Content", _model_dict(content))

    if result.structured_content is not None:
        _render_json_section(
            "Structured content",
            result.structured_content,
        )


@cli.command(name="call")
@click.argument("url")
@click.argument("tool_name")
@click.argument("arguments_json", required=False)
@click.option(
    "-a",
    "--argument",
    "argument_pairs",
    nargs=2,
    multiple=True,
    metavar="NAME VALUE",
    help="Set one tool argument; repeat for multiple arguments.",
)
@click.pass_obj
def call_command(
    options,
    url,
    tool_name,
    arguments_json,
    argument_pairs,
):
    """Call a tool with optional JSON and individual arguments."""
    if arguments_json == "-":
        arguments_json = click.get_text_stream("stdin").read()

    try:
        result = asyncio.run(
            invoke_tool(
                url,
                tool_name,
                arguments_json,
                argument_pairs,
                options.stateless,
            )
        )
    except ArgumentInputError as ex:
        raise click.UsageError(str(ex)) from ex
    except ToolNotFoundError as ex:
        raise click.ClickException(str(ex)) from ex
    except Exception as ex:
        argument_error = _find_nested_exception(ex, ArgumentInputError)
        if argument_error is not None:
            raise click.UsageError(str(argument_error)) from ex
        tool_error = _find_nested_exception(ex, ToolNotFoundError)
        if tool_error is not None:
            raise click.ClickException(str(tool_error)) from ex
        raise click.ClickException(_exception_message(ex)) from ex

    if options.json_output:
        click.echo(json.dumps(_model_dict(result), indent=2))
    else:
        _render_call_result(result)

    if result.is_error:
        raise click.exceptions.Exit(1)


@cli.command(name="info")
@click.argument("url")
@click.option(
    "--json",
    "command_json_output",
    is_flag=True,
    help="Output server information as JSON.",
)
@click.pass_obj
def info_command(options, url, command_json_output):
    """Show protocol and metadata for an MCP server at URL."""
    try:
        info = asyncio.run(fetch_server_info(url, options.stateless))
    except Exception as ex:
        raise click.ClickException(_exception_message(ex)) from ex

    if options.json_output or command_json_output:
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
