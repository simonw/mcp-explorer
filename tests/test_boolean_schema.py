"""Boolean JSON Schema subschemas must not crash argument handling."""

import pytest

from mcp_explorer.cli import build_arguments, ArgumentInputError


def test_boolean_true_property_schema_passes_string_through():
    schema = {
        "type": "object",
        "properties": {"verbose": True, "count": {"type": "integer"}},
    }
    assert build_arguments(schema, None, [("verbose", "hello"), ("count", "3")]) == {
        "verbose": "hello",
        "count": 3,
    }


def test_boolean_false_property_schema_rejected_cleanly():
    schema = {"type": "object", "properties": {"name": False}}
    with pytest.raises(ArgumentInputError):
        build_arguments(schema, None, [("name", "hello")])
