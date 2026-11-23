"""Phase 11 capability discovery and schema validation tests."""
from __future__ import annotations

import pytest

from matlab_os.integrations.discovery import (
    DiscoveryError,
    SchemaValidationError,
    normalise_tool_id,
    normalise_schema,
    validate_record,
    validate_capabilities,
)


class TestNormaliseToolId:
    def test_strips_namespace(self):
        assert normalise_tool_id("matlab/model_edit") == "model_edit"

    def test_rejects_unsafe(self):
        with pytest.raises(DiscoveryError):
            normalise_tool_id("a<b>")

    def test_rejects_empty(self):
        with pytest.raises(DiscoveryError):
            normalise_tool_id("")


class TestNormaliseSchema:
    def test_dict_passes_through(self):
        assert normalise_schema({"type": "object"}) == {"type": "object"}

    def test_string_parses(self):
        assert normalise_schema('{"type": "object"}') == {"type": "object"}

    def test_rejects_non_object(self):
        with pytest.raises(SchemaValidationError):
            normalise_schema("[1, 2]")

    def test_rejects_invalid_json(self):
        with pytest.raises(SchemaValidationError):
            normalise_schema("{not json")


class TestValidateRecord:
    def test_valid(self):
        record = validate_record(
            {
                "id": "matlab/model_read",
                "name": "model_read",
                "input_schema": {"type": "object"},
            }
        )
        assert record["id"] == "model_read"
        assert record["description"] == ""

    def test_unknown_field_is_dropped(self):
        # Extra fields are never executed: they are dropped, never returned.
        record = validate_record({"id": "x", "secret": "nope"})
        # id falls back to "unknown" because the record has no usable id;
        # the secret is never returned and never executed.
        assert record["id"] == "unknown"
        assert "secret" not in record
        assert record["input_schema"] == {}

    def test_valid_with_empty_schema(self):
        record = validate_record({"id": "x", "input_schema": {}})
        assert record["input_schema"] == {}


class TestValidateCapabilities:
    def test_unique(self):
        records = [
            {"id": "a", "name": "a", "input_schema": {"type": "object"}},
            {"id": "b", "name": "b", "input_schema": {"type": "object"}},
        ]
        ids = {r["id"] for r in validate_capabilities(records)}
        assert ids == {"a", "b"}

    def test_duplicate_rejected(self):
        records = [
            {"id": "a", "name": "a", "input_schema": {"type": "object"}},
            {"id": "a", "name": "a", "input_schema": {"type": "object"}},
        ]
        with pytest.raises(DiscoveryError):
            validate_capabilities(records)
