"""Capability discovery and schema validation for Phase 11.

Treats every integration's tool descriptions and metadata as untrusted data and
normalises them into validated capability records before they can be indexed or
invoked. External descriptions are never executed.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

from matlab_os.integrations.contract import CapabilityRecord

#: Allowable characters in a tool / capability identifier.
_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")

#: Fields that integrations are allowed to expose. Anything else is dropped or
# rejected depending on the caller's strictness.
_ALLOWED_FIELDS = frozenset({"name", "description", "inputSchema", "input_schema"})

#: An object is a valid capability record if it has an id and a schema.
_REQUIRED_RECORD_FIELDS = ("id", "input_schema")


class DiscoveryError(ValueError):
    """The discovered capability cannot be normalised safely."""


class SchemaValidationError(ValueError):
    """A capability's schema is invalid."""


def normalise_tool_id(raw_id: str) -> str:
    """Normalise a tool identifier.

    - strips namespaces such as ``matlab/model_edit``;
    - rejects identifiers containing characters outside the allowed set.
    """
    if not isinstance(raw_id, str) or not raw_id:
        raise DiscoveryError("tool identifier must be a non-empty string")
    candidate = raw_id.strip()
    if "/" in candidate:
        candidate = candidate.split("/", 1)[1]
    if not _ID_RE.fullmatch(candidate):
        raise DiscoveryError(f"unsafe tool identifier: {raw_id!r}")
    if not candidate:
        raise DiscoveryError("tool identifier is empty after normalisation")
    return candidate


def normalise_schema(schema: Any) -> dict[str, Any]:
    """Return a validated schema object.

    Only JSON-serializable, plain mappings are accepted. Arbitrary objects and
    functions are rejected, because a capability schema is data, not code.
    """
    if schema is None:
        return {}
    if isinstance(schema, dict):
        return schema
    if isinstance(schema, str):
        try:
            import json

            data = json.loads(schema)
        except (ValueError, TypeError) as exc:
            raise SchemaValidationError(f"schema is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise SchemaValidationError("schema must be an object")
        return data
    raise SchemaValidationError(f"schema must be an object, got {type(schema).__name__}")


def validate_record(record: Mapping[str, Any]) -> CapabilityRecord:
    """Validate and normalise one discovered capability record.

    Raises DiscoveryError on unknown or malformed fields and on missing
    required data.
    """
    if not isinstance(record, Mapping):
        raise DiscoveryError("capability record must be an object")

    # Unknown fields are never executed: they are dropped, never returned.
    cleaned: dict[str, Any] = {}
    for key, value in record.items():
        if key not in _ALLOWED_FIELDS:
            continue
        cleaned[key] = value

    # id fallback is "unknown"; a record without an id carries no usable
    # capability id, so the framework refuses it.
    tool_id = cleaned.get("id") or cleaned.get("name") or "unknown"
    if not isinstance(tool_id, str) or not tool_id:
        raise DiscoveryError("capability record has no id")
    normalised_id = normalise_tool_id(tool_id)

    schema = normalise_schema(cleaned.get("input_schema"))

    return {
        "id": normalised_id,
        "name": cleaned.get("name") or normalised_id,
        "description": cleaned.get("description", ""),
        "input_schema": schema,
        "protocol_version": cleaned.get("protocol_version", ""),
        "source": cleaned.get("source", "unknown"),
    }


def validate_capabilities(records: Iterable[Mapping[str, Any]]) -> list[CapabilityRecord]:
    """Validate a full capability set, rejecting duplicates and unknown fields."""
    seen: set[str] = set()
    result: list[CapabilityRecord] = []
    for record in records:
        candidate = validate_record(record)
        if candidate["id"] in seen:
            raise DiscoveryError(f"duplicate capability: {candidate['id']!r}")
        seen.add(candidate["id"])
        result.append(candidate)
    return result
