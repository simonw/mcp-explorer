# mcp-explorer

[![PyPI](https://img.shields.io/pypi/v/mcp-explorer.svg)](https://pypi.org/project/mcp-explorer/)
[![Changelog](https://img.shields.io/github/v/release/simonw/mcp-explorer?include_prereleases&label=changelog)](https://github.com/simonw/mcp-explorer/releases)
[![Tests](https://github.com/simonw/mcp-explorer/actions/workflows/test.yml/badge.svg)](https://github.com/simonw/mcp-explorer/actions/workflows/test.yml)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](https://github.com/simonw/mcp-explorer/blob/master/LICENSE)

CLI tool for exploring MCP servers

## Installation

Install this tool using `pip`:
```bash
pip install mcp-explorer
```
## Usage

List the tools exposed by a streamable HTTP MCP server:

```bash
mcp-explorer list https://agentic-mermaid.dev/mcp
```

This forces the current MCP 2 stateless protocol by default. Use `--legacy` to
force the older initialize-handshake protocol instead:

```bash
mcp-explorer list --legacy https://agentic-mermaid.dev/mcp
```

Use `--json` to output the complete tool definitions, including their input
schemas:

```bash
mcp-explorer list --json https://agentic-mermaid.dev/mcp
```

Inspect a single tool in detail:

```bash
mcp-explorer inspect https://agentic-mermaid.dev/mcp render_svg
```

This displays the tool's complete description, nested input and output schemas,
annotations, execution metadata, icons, and `_meta`. Use `--json` for the
complete tool definition as a single JSON object:

```bash
mcp-explorer inspect --json https://agentic-mermaid.dev/mcp render_svg
```

The `inspect` command also accepts `--legacy` to force the older
initialize-handshake protocol.

For help, run:

```bash
mcp-explorer --help
```

You can also use:

```bash
python -m mcp_explorer --help
```
## Development

To contribute to this tool, first checkout the code. Run the tests like this:
```bash
cd mcp-explorer
uv run pytest
```
And the development version of the tool like this:
```bash
uv run mcp-explorer --help
```
