#!/usr/bin/env python3
"""Validate MSYS role contracts and run provider conformance probes.

The module deliberately uses only the Python standard library.  It implements
the bounded JSON Schema vocabulary accepted inside role descriptors instead
of silently depending on whichever jsonschema package happens to be present.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


DESCRIPTOR_SCHEMA = "msys.role-contract.v1"
DESCRIPTOR_META_SCHEMA = "https://msys.local/schemas/role-contract.v1.json"
ADAPTER_SCHEMA = "msys.provider-conformance.v1"
CONTRACT_ID = re.compile(r"^[a-z][a-z0-9._-]*\.v([1-9][0-9]*)$")
ROLE = re.compile(r"^[a-z][a-z0-9.-]*$")
NAME = re.compile(r"^[a-z][a-z0-9._-]*$")
ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
TYPE_NAME = re.compile(r"^[a-z][a-z0-9._-]*$")
SEMVER = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-([0-9A-Za-z.-]+))?(?:\+([0-9A-Za-z.-]+))?$"
)
MAX_ADAPTER_PACKET = 256 * 1024

ROOT_FIELDS = {
    "$schema",
    "schema",
    "id",
    "role",
    "version",
    "status",
    "summary",
    "channel",
    "target",
    "provider",
    "types",
    "methods",
    "events",
    "artifacts",
    "dependencies",
    "errors",
}
PROVIDER_FIELDS = {
    "exclusive",
    "readiness_modes",
    "lifecycle_modes",
    "windowing_required",
    "windowing_modes",
    "required_interfaces",
    "required_capabilities",
    "requires_control_channel",
}
METHOD_FIELDS = {
    "name",
    "summary",
    "since",
    "required",
    "idempotent",
    "mutates",
    "request",
    "response",
    "errors",
    "conformance",
}
CASE_FIELDS = {"id", "mode", "request", "expect"}
EXPECT_FIELDS = {"type", "codes"}
EVENT_FIELDS = {"topic", "direction", "required", "payload", "summary"}
ARTIFACT_FIELDS = {"name", "transport", "required", "payload", "summary"}
DEPENDENCY_FIELDS = {"target", "methods", "required", "summary"}
ERROR_FIELDS = {"summary", "retryable"}
VALUE_SCHEMA_FIELDS = {
    "$ref",
    "$comment",
    "description",
    "type",
    "properties",
    "required",
    "additionalProperties",
    "items",
    "enum",
    "const",
    "oneOf",
    "anyOf",
    "allOf",
    "minItems",
    "maxItems",
    "uniqueItems",
    "minLength",
    "maxLength",
    "pattern",
    "minimum",
    "maximum",
    "minProperties",
    "maxProperties",
}
JSON_TYPES = {"object", "array", "string", "integer", "number", "boolean", "null"}
READINESS_MODES = {"exec", "mipc-ready", "x11-display"}
LIFECYCLE_MODES = {"session", "background", "on-demand", "manual"}
WINDOWING_MODES = {"window", "fullscreen", "background", "overlay", "display-provider"}
WINDOW_SYSTEMS = {"x11", "wayland", "framebuffer", "none", "custom"}
ROLE_WINDOW_FIELDS = {"system", "display", "mode", "title", "identity", "edge"}
WINDOW_IDENTITY_FIELDS = {
    "app_id",
    "x11_wm_class",
    "x11_wm_instance",
    "wayland_app_id",
}
APP_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
X11_DISPLAY = re.compile(r"^(?:inherit|:[0-9]+(?:\.[0-9]+)?)$")


class DuplicateKeyError(ValueError):
    """Raised when a JSON object contains the same member more than once."""


class ContractFormatError(ValueError):
    """Raised when a contract cannot be loaded as strict JSON."""


def _strict_object(pairs: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError("duplicate JSON member %r" % key)
        result[key] = value
    return result


def load_json(path: Path) -> Any:
    """Load UTF-8 JSON, rejecting BOM ambiguity, duplicate keys, and constants."""

    try:
        text = Path(path).read_text(encoding="utf-8")
        return json.loads(
            text,
            object_pairs_hook=_strict_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError("non-JSON numeric constant %s" % value)
            ),
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ContractFormatError("%s: %s" % (path, exc)) from exc


def _field_errors(value: Mapping[str, Any], allowed: set, path: str) -> List[str]:
    return [
        "%s: unknown field %r" % (path, key)
        for key in value
        if key not in allowed and not key.startswith("x-")
    ]


def _require(value: Mapping[str, Any], names: Iterable[str], path: str) -> List[str]:
    return ["%s: missing required field %r" % (path, name) for name in names if name not in value]


def _semver(value: Any) -> Optional[Tuple[int, int, int]]:
    if not isinstance(value, str):
        return None
    match = SEMVER.fullmatch(value)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def _string_list(
    value: Any,
    path: str,
    *,
    pattern: Optional[re.Pattern] = None,
    allowed: Optional[set] = None,
    allow_empty: bool = True,
) -> Tuple[List[str], List[str]]:
    errors: List[str] = []
    if not isinstance(value, list):
        return [], ["%s: must be an array" % path]
    if not allow_empty and not value:
        errors.append("%s: must not be empty" % path)
    result: List[str] = []
    seen = set()
    for index, item in enumerate(value):
        item_path = "%s[%d]" % (path, index)
        if not isinstance(item, str):
            errors.append("%s: must be a string" % item_path)
            continue
        if pattern is not None and pattern.fullmatch(item) is None:
            errors.append("%s: has invalid syntax" % item_path)
        if allowed is not None and item not in allowed:
            errors.append("%s: unsupported value %r" % (item_path, item))
        if item in seen:
            errors.append("%s: duplicate value %r" % (item_path, item))
        seen.add(item)
        result.append(item)
    return result, errors


def _json_equal(left: Any, right: Any) -> bool:
    """Compare JSON values without Python's True == 1 coercion."""

    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_json_equal(left[key], right[key]) for key in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(_json_equal(a, b) for a, b in zip(left, right))
    return bool(left == right)


def _validate_value_schema_shape(schema: Any, root: Mapping[str, Any], path: str) -> List[str]:
    errors: List[str] = []
    if isinstance(schema, bool):
        return errors
    if not isinstance(schema, dict):
        return ["%s: value schema must be an object or boolean" % path]
    errors.extend(_field_errors(schema, VALUE_SCHEMA_FIELDS, path))
    if "$ref" in schema:
        ref = schema["$ref"]
        if not isinstance(ref, str) or re.fullmatch(r"#/types/[a-z][a-z0-9._-]*", ref) is None:
            errors.append("%s.$ref: only #/types/<name> references are supported" % path)
        elif ref[len("#/types/") :] not in root.get("types", {}):
            errors.append("%s.$ref: unknown type %r" % (path, ref[len("#/types/") :]))
    if "type" in schema:
        raw_type = schema["type"]
        types = [raw_type] if isinstance(raw_type, str) else raw_type
        if not isinstance(types, list) or not types:
            errors.append("%s.type: must be a type name or non-empty array" % path)
        else:
            seen = set()
            for index, item in enumerate(types):
                if not isinstance(item, str) or item not in JSON_TYPES:
                    errors.append("%s.type[%d]: unsupported JSON type %r" % (path, index, item))
                elif item in seen:
                    errors.append("%s.type[%d]: duplicate type %r" % (path, index, item))
                seen.add(item)
    if "properties" in schema:
        properties = schema["properties"]
        if not isinstance(properties, dict):
            errors.append("%s.properties: must be an object" % path)
        else:
            for name, child in properties.items():
                errors.extend(_validate_value_schema_shape(child, root, "%s.properties.%s" % (path, name)))
    if "required" in schema:
        names, nested = _string_list(schema["required"], "%s.required" % path)
        errors.extend(nested)
        properties = schema.get("properties")
        if isinstance(properties, dict):
            for name in names:
                if name not in properties:
                    errors.append("%s.required: %r is absent from properties" % (path, name))
    if "additionalProperties" in schema:
        additional = schema["additionalProperties"]
        if not isinstance(additional, bool):
            errors.extend(_validate_value_schema_shape(additional, root, "%s.additionalProperties" % path))
    if "items" in schema:
        errors.extend(_validate_value_schema_shape(schema["items"], root, "%s.items" % path))
    for keyword in ("oneOf", "anyOf", "allOf"):
        if keyword not in schema:
            continue
        branches = schema[keyword]
        if not isinstance(branches, list) or not branches:
            errors.append("%s.%s: must be a non-empty array" % (path, keyword))
        else:
            for index, child in enumerate(branches):
                errors.extend(_validate_value_schema_shape(child, root, "%s.%s[%d]" % (path, keyword, index)))
    if "enum" in schema:
        values = schema["enum"]
        if not isinstance(values, list) or not values:
            errors.append("%s.enum: must be a non-empty array" % path)
        elif any(_json_equal(item, other) for index, item in enumerate(values) for other in values[:index]):
            errors.append("%s.enum: values must be unique" % path)
    for keyword in (
        "minItems",
        "maxItems",
        "minLength",
        "maxLength",
        "minProperties",
        "maxProperties",
    ):
        if keyword in schema and (
            isinstance(schema[keyword], bool)
            or not isinstance(schema[keyword], int)
            or schema[keyword] < 0
        ):
            errors.append("%s.%s: must be a non-negative integer" % (path, keyword))
    for low, high in (
        ("minItems", "maxItems"),
        ("minLength", "maxLength"),
        ("minProperties", "maxProperties"),
        ("minimum", "maximum"),
    ):
        if low in schema and high in schema:
            low_value = schema[low]
            high_value = schema[high]
            comparable = (
                not isinstance(low_value, bool)
                and not isinstance(high_value, bool)
                and isinstance(low_value, (int, float))
                and isinstance(high_value, (int, float))
            )
            if comparable and low_value > high_value:
                errors.append("%s: %s must not exceed %s" % (path, low, high))
    for keyword in ("minimum", "maximum"):
        if keyword in schema and (
            isinstance(schema[keyword], bool)
            or not isinstance(schema[keyword], (int, float))
            or not math.isfinite(schema[keyword])
        ):
            errors.append("%s.%s: must be a finite number" % (path, keyword))
    if "uniqueItems" in schema and not isinstance(schema["uniqueItems"], bool):
        errors.append("%s.uniqueItems: must be a boolean" % path)
    if "pattern" in schema:
        pattern = schema["pattern"]
        if not isinstance(pattern, str):
            errors.append("%s.pattern: must be a string" % path)
        else:
            try:
                re.compile(pattern)
            except re.error as exc:
                errors.append("%s.pattern: invalid regular expression: %s" % (path, exc))
    for keyword in ("description", "$comment"):
        if keyword in schema and not isinstance(schema[keyword], str):
            errors.append("%s.%s: must be a string" % (path, keyword))
    return errors


def _instance_type_matches(instance: Any, expected: str) -> bool:
    if expected == "null":
        return instance is None
    if expected == "boolean":
        return isinstance(instance, bool)
    if expected == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if expected == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool) and math.isfinite(instance)
    if expected == "string":
        return isinstance(instance, str)
    if expected == "array":
        return isinstance(instance, list)
    if expected == "object":
        return isinstance(instance, dict)
    return False


def validate_instance(
    instance: Any,
    schema: Any,
    contract: Mapping[str, Any],
    path: str = "$",
    _refs: Tuple[str, ...] = (),
) -> List[str]:
    """Validate a JSON value using the descriptor's strict schema subset."""

    if schema is True:
        return []
    if schema is False:
        return ["%s: value is forbidden" % path]
    if not isinstance(schema, dict):
        return ["%s: invalid value schema" % path]
    errors: List[str] = []
    if "$ref" in schema:
        ref = schema["$ref"]
        if ref in _refs:
            return ["%s: cyclic schema reference %s" % (path, ref)]
        name = str(ref)[len("#/types/") :]
        target = contract.get("types", {}).get(name)
        if target is None:
            return ["%s: unresolved schema reference %s" % (path, ref)]
        errors.extend(validate_instance(instance, target, contract, path, _refs + (ref,)))
    if "type" in schema:
        raw = schema["type"]
        types = [raw] if isinstance(raw, str) else raw
        if not any(_instance_type_matches(instance, item) for item in types):
            errors.append("%s: expected %s, got %s" % (path, " or ".join(types), type(instance).__name__))
            return errors
    if "const" in schema and not _json_equal(instance, schema["const"]):
        errors.append("%s: value does not equal const" % path)
    if "enum" in schema and not any(_json_equal(instance, value) for value in schema["enum"]):
        errors.append("%s: value is not in enum" % path)
    for keyword in ("allOf", "anyOf", "oneOf"):
        if keyword not in schema:
            continue
        branch_errors = [validate_instance(instance, branch, contract, path, _refs) for branch in schema[keyword]]
        passing = sum(not item for item in branch_errors)
        if keyword == "allOf" and passing != len(branch_errors):
            errors.append("%s: does not satisfy allOf" % path)
        elif keyword == "anyOf" and passing == 0:
            errors.append("%s: does not satisfy anyOf" % path)
        elif keyword == "oneOf" and passing != 1:
            errors.append("%s: satisfies %d oneOf branches, expected exactly one" % (path, passing))
    if isinstance(instance, dict):
        if "minProperties" in schema and len(instance) < schema["minProperties"]:
            errors.append("%s: has fewer than minProperties" % path)
        if "maxProperties" in schema and len(instance) > schema["maxProperties"]:
            errors.append("%s: has more than maxProperties" % path)
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        for name in required:
            if name not in instance:
                errors.append("%s: missing required property %r" % (path, name))
        additional = schema.get("additionalProperties", True)
        for name, value in instance.items():
            child_path = "%s.%s" % (path, name)
            if name in properties:
                errors.extend(validate_instance(value, properties[name], contract, child_path, _refs))
            elif additional is False:
                errors.append("%s: additional property is forbidden" % child_path)
            elif isinstance(additional, dict) or isinstance(additional, bool):
                errors.extend(validate_instance(value, additional, contract, child_path, _refs))
    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append("%s: has fewer than minItems" % path)
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append("%s: has more than maxItems" % path)
        if schema.get("uniqueItems"):
            for index, item in enumerate(instance):
                if any(_json_equal(item, previous) for previous in instance[:index]):
                    errors.append("%s[%d]: duplicate array item" % (path, index))
        if "items" in schema:
            for index, item in enumerate(instance):
                errors.extend(validate_instance(item, schema["items"], contract, "%s[%d]" % (path, index), _refs))
    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append("%s: shorter than minLength" % path)
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append("%s: longer than maxLength" % path)
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            errors.append("%s: does not match pattern" % path)
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append("%s: less than minimum" % path)
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append("%s: greater than maximum" % path)
    return errors


def validate_contract(document: Any, source: str = "<contract>") -> List[str]:
    """Return every structural and semantic error in one role descriptor."""

    if not isinstance(document, dict):
        return ["%s: contract must be a JSON object" % source]
    errors = _field_errors(document, ROOT_FIELDS, source)
    errors.extend(
        _require(
            document,
            (
                "schema",
                "id",
                "role",
                "version",
                "status",
                "summary",
                "channel",
                "target",
                "provider",
                "types",
                "methods",
                "events",
                "artifacts",
                "dependencies",
                "errors",
            ),
            source,
        )
    )
    if document.get("schema") != DESCRIPTOR_SCHEMA:
        errors.append("%s.schema: expected %r" % (source, DESCRIPTOR_SCHEMA))
    if "$schema" in document and document["$schema"] != DESCRIPTOR_META_SCHEMA:
        errors.append("%s.$schema: expected %r" % (source, DESCRIPTOR_META_SCHEMA))
    contract_id = document.get("id")
    match = CONTRACT_ID.fullmatch(contract_id) if isinstance(contract_id, str) else None
    if match is None:
        errors.append("%s.id: invalid versioned contract id" % source)
    role = document.get("role")
    if not isinstance(role, str) or ROLE.fullmatch(role) is None:
        errors.append("%s.role: invalid role name" % source)
    version = _semver(document.get("version"))
    if version is None:
        errors.append("%s.version: expected canonical semantic version" % source)
    elif match is not None and version[0] != int(match.group(1)):
        errors.append("%s.version: major must match the .vN contract id" % source)
    if document.get("status") not in {"experimental", "stable", "deprecated"}:
        errors.append("%s.status: unsupported stability value" % source)
    if not isinstance(document.get("summary"), str) or not str(document.get("summary", "")).strip():
        errors.append("%s.summary: must be a non-empty string" % source)
    channel = document.get("channel")
    if channel not in {"mipc", "supervisor-lifecycle"}:
        errors.append("%s.channel: must be mipc or supervisor-lifecycle" % source)
    expected_target = "role:%s" % role if isinstance(role, str) else None
    if document.get("target") != expected_target:
        errors.append("%s.target: must equal %r" % (source, expected_target))

    provider = document.get("provider")
    if not isinstance(provider, dict):
        errors.append("%s.provider: must be an object" % source)
    else:
        path = "%s.provider" % source
        errors.extend(_field_errors(provider, PROVIDER_FIELDS, path))
        errors.extend(_require(provider, ("exclusive", "readiness_modes", "lifecycle_modes", "requires_control_channel"), path))
        for field in ("exclusive", "windowing_required", "requires_control_channel"):
            if field in provider and not isinstance(provider[field], bool):
                errors.append("%s.%s: must be a boolean" % (path, field))
        _, nested = _string_list(provider.get("readiness_modes"), "%s.readiness_modes" % path, allowed=READINESS_MODES, allow_empty=False)
        errors.extend(nested)
        _, nested = _string_list(provider.get("lifecycle_modes"), "%s.lifecycle_modes" % path, allowed=LIFECYCLE_MODES, allow_empty=False)
        errors.extend(nested)
        if "windowing_modes" in provider:
            _, nested = _string_list(provider["windowing_modes"], "%s.windowing_modes" % path, allowed=WINDOWING_MODES, allow_empty=False)
            errors.extend(nested)
        for field in ("required_interfaces", "required_capabilities"):
            if field in provider:
                _, nested = _string_list(provider[field], "%s.%s" % (path, field), pattern=NAME)
                errors.extend(nested)
        if provider.get("requires_control_channel") and "mipc-ready" not in provider.get("readiness_modes", []):
            errors.append("%s: a control-channel role must allow mipc-ready" % path)
    types = document.get("types")
    if not isinstance(types, dict):
        errors.append("%s.types: must be an object" % source)
    else:
        for name, schema in types.items():
            if TYPE_NAME.fullmatch(name) is None:
                errors.append("%s.types.%s: invalid type name" % (source, name))
            errors.extend(_validate_value_schema_shape(schema, document, "%s.types.%s" % (source, name)))

    error_defs = document.get("errors")
    if not isinstance(error_defs, dict):
        errors.append("%s.errors: must be an object" % source)
        error_defs = {}
    else:
        for code, definition in error_defs.items():
            path = "%s.errors.%s" % (source, code)
            if ERROR_CODE.fullmatch(code) is None:
                errors.append("%s: invalid error code" % path)
            if not isinstance(definition, dict):
                errors.append("%s: must be an object" % path)
                continue
            errors.extend(_field_errors(definition, ERROR_FIELDS, path))
            errors.extend(_require(definition, ("summary", "retryable"), path))
            if not isinstance(definition.get("summary"), str) or not definition.get("summary", "").strip():
                errors.append("%s.summary: must be a non-empty string" % path)
            if not isinstance(definition.get("retryable"), bool):
                errors.append("%s.retryable: must be a boolean" % path)

    methods = document.get("methods")
    method_names = set()
    case_ids = set()
    if not isinstance(methods, list):
        errors.append("%s.methods: must be an array" % source)
        methods = []
    if channel == "supervisor-lifecycle" and methods:
        errors.append("%s.methods: lifecycle-only roles cannot declare mIPC methods" % source)
    for index, method in enumerate(methods):
        path = "%s.methods[%d]" % (source, index)
        if not isinstance(method, dict):
            errors.append("%s: must be an object" % path)
            continue
        errors.extend(_field_errors(method, METHOD_FIELDS, path))
        errors.extend(_require(method, ("name", "summary", "since", "required", "idempotent", "mutates", "request", "response", "errors", "conformance"), path))
        name = method.get("name")
        if not isinstance(name, str) or NAME.fullmatch(name) is None:
            errors.append("%s.name: invalid method name" % path)
        elif name in method_names:
            errors.append("%s.name: duplicate method %r" % (path, name))
        method_names.add(name)
        if not isinstance(method.get("summary"), str) or not method.get("summary", "").strip():
            errors.append("%s.summary: must be a non-empty string" % path)
        since = _semver(method.get("since"))
        if since is None:
            errors.append("%s.since: invalid semantic version" % path)
        elif version is not None and (since[0] != version[0] or since > version):
            errors.append("%s.since: must be in this major and not newer than the descriptor" % path)
        for field in ("required", "idempotent", "mutates"):
            if not isinstance(method.get(field), bool):
                errors.append("%s.%s: must be a boolean" % (path, field))
        errors.extend(_validate_value_schema_shape(method.get("request"), document, "%s.request" % path))
        errors.extend(_validate_value_schema_shape(method.get("response"), document, "%s.response" % path))
        codes, nested = _string_list(method.get("errors"), "%s.errors" % path, pattern=ERROR_CODE)
        errors.extend(nested)
        for code in codes:
            if code not in error_defs:
                errors.append("%s.errors: undefined error code %r" % (path, code))
        cases = method.get("conformance")
        if not isinstance(cases, list):
            errors.append("%s.conformance: must be an array" % path)
            continue
        for case_index, case in enumerate(cases):
            case_path = "%s.conformance[%d]" % (path, case_index)
            if not isinstance(case, dict):
                errors.append("%s: must be an object" % case_path)
                continue
            errors.extend(_field_errors(case, CASE_FIELDS, case_path))
            errors.extend(_require(case, ("id", "mode", "request", "expect"), case_path))
            case_id = case.get("id")
            if not isinstance(case_id, str) or NAME.fullmatch(case_id) is None:
                errors.append("%s.id: invalid case id" % case_path)
            elif case_id in case_ids:
                errors.append("%s.id: duplicate conformance case %r" % (case_path, case_id))
            case_ids.add(case_id)
            mode = case.get("mode")
            if mode not in {"safe", "mutating"}:
                errors.append("%s.mode: must be safe or mutating" % case_path)
            if method.get("mutates") is True and mode == "safe":
                errors.append("%s.mode: mutating method cannot have a safe case" % case_path)
            expect = case.get("expect")
            if not isinstance(expect, dict):
                errors.append("%s.expect: must be an object" % case_path)
                continue
            errors.extend(_field_errors(expect, EXPECT_FIELDS, "%s.expect" % case_path))
            errors.extend(_require(expect, ("type",), "%s.expect" % case_path))
            expected_type = expect.get("type")
            if expected_type not in {"return", "error"}:
                errors.append("%s.expect.type: must be return or error" % case_path)
            if expected_type == "error":
                expected_codes, nested = _string_list(expect.get("codes"), "%s.expect.codes" % case_path, pattern=ERROR_CODE, allow_empty=False)
                errors.extend(nested)
                for code in expected_codes:
                    if code not in codes:
                        errors.append("%s.expect.codes: %r is not declared by the method" % (case_path, code))
            elif "codes" in expect:
                errors.append("%s.expect.codes: only error cases may list codes" % case_path)
            elif "request" in case:
                errors.extend(validate_instance(case["request"], method.get("request"), document, "%s.request" % case_path))

    events = document.get("events")
    topics = set()
    if not isinstance(events, list):
        errors.append("%s.events: must be an array" % source)
    else:
        for index, event in enumerate(events):
            path = "%s.events[%d]" % (source, index)
            if not isinstance(event, dict):
                errors.append("%s: must be an object" % path)
                continue
            errors.extend(_field_errors(event, EVENT_FIELDS, path))
            errors.extend(_require(event, ("topic", "direction", "required", "payload", "summary"), path))
            topic = event.get("topic")
            if not isinstance(topic, str) or NAME.fullmatch(topic) is None:
                errors.append("%s.topic: invalid concrete event topic" % path)
            key = (topic, event.get("direction"))
            if key in topics:
                errors.append("%s: duplicate event/direction" % path)
            topics.add(key)
            if event.get("direction") not in {"publish", "subscribe"}:
                errors.append("%s.direction: must be publish or subscribe" % path)
            if not isinstance(event.get("required"), bool):
                errors.append("%s.required: must be a boolean" % path)
            if not isinstance(event.get("summary"), str) or not event.get("summary", "").strip():
                errors.append("%s.summary: must be a non-empty string" % path)
            errors.extend(_validate_value_schema_shape(event.get("payload"), document, "%s.payload" % path))

    artifacts = document.get("artifacts")
    artifact_names = set()
    if not isinstance(artifacts, list):
        errors.append("%s.artifacts: must be an array" % source)
    else:
        for index, artifact in enumerate(artifacts):
            path = "%s.artifacts[%d]" % (source, index)
            if not isinstance(artifact, dict):
                errors.append("%s: must be an object" % path)
                continue
            errors.extend(_field_errors(artifact, ARTIFACT_FIELDS, path))
            errors.extend(_require(artifact, ("name", "transport", "required", "payload", "summary"), path))
            name = artifact.get("name")
            if not isinstance(name, str) or NAME.fullmatch(name) is None:
                errors.append("%s.name: invalid artifact name" % path)
            elif name in artifact_names:
                errors.append("%s.name: duplicate artifact %r" % (path, name))
            artifact_names.add(name)
            if artifact.get("transport") not in {"readiness-document"}:
                errors.append("%s.transport: unsupported artifact transport" % path)
            if not isinstance(artifact.get("required"), bool):
                errors.append("%s.required: must be a boolean" % path)
            if not isinstance(artifact.get("summary"), str) or not artifact.get("summary", "").strip():
                errors.append("%s.summary: must be a non-empty string" % path)
            errors.extend(_validate_value_schema_shape(artifact.get("payload"), document, "%s.payload" % path))

    dependencies = document.get("dependencies")
    if not isinstance(dependencies, list):
        errors.append("%s.dependencies: must be an array" % source)
    else:
        seen_dependencies = set()
        for index, dependency in enumerate(dependencies):
            path = "%s.dependencies[%d]" % (source, index)
            if not isinstance(dependency, dict):
                errors.append("%s: must be an object" % path)
                continue
            errors.extend(_field_errors(dependency, DEPENDENCY_FIELDS, path))
            errors.extend(_require(dependency, ("target", "methods", "required", "summary"), path))
            target = dependency.get("target")
            if not isinstance(target, str) or not (
                target == "msys.core"
                or re.fullmatch(r"(?:role|interface):[a-z][a-z0-9._:-]*", target)
            ):
                errors.append("%s.target: invalid mIPC target" % path)
            if target in seen_dependencies:
                errors.append("%s.target: duplicate dependency" % path)
            seen_dependencies.add(target)
            _, nested = _string_list(dependency.get("methods"), "%s.methods" % path, pattern=NAME, allow_empty=False)
            errors.extend(nested)
            if not isinstance(dependency.get("required"), bool):
                errors.append("%s.required: must be a boolean" % path)
            if not isinstance(dependency.get("summary"), str) or not dependency.get("summary", "").strip():
                errors.append("%s.summary: must be a non-empty string" % path)
    return errors


def discover_contracts(paths: Sequence[Path]) -> Tuple[List[Tuple[Path, Dict[str, Any]]], List[str]]:
    files: List[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            files.extend(sorted(path.rglob("*.json")))
        else:
            files.append(path)
    loaded: List[Tuple[Path, Dict[str, Any]]] = []
    errors: List[str] = []
    for path in sorted(set(item.resolve() for item in files)):
        try:
            document = load_json(path)
        except ContractFormatError as exc:
            errors.append(str(exc))
            continue
        nested = validate_contract(document, str(path))
        errors.extend(nested)
        if isinstance(document, dict):
            loaded.append((path, document))
    return loaded, errors


def contract_catalog(contracts: Iterable[Mapping[str, Any]]) -> Tuple[Dict[Tuple[str, str], Mapping[str, Any]], List[str]]:
    result: Dict[Tuple[str, str], Mapping[str, Any]] = {}
    errors: List[str] = []
    for contract in contracts:
        key = (str(contract.get("id", "")), str(contract.get("version", "")))
        if key in result:
            errors.append("duplicate contract revision %s@%s" % key)
        result[key] = contract
    return result, errors


def _validate_role_window(window: Any, path: str) -> List[str]:
    """Validate the deliberately closed role-specific surface descriptor."""

    if not isinstance(window, dict):
        return ["%s: must be an object" % path]
    errors = [
        "%s: unknown field %r" % (path, name)
        for name in window
        if name not in ROLE_WINDOW_FIELDS
    ]
    for name in ("system", "mode"):
        if name not in window:
            errors.append("%s: missing required field %r" % (path, name))
    system = window.get("system")
    mode = window.get("mode")
    if system not in WINDOW_SYSTEMS:
        errors.append("%s.system: unsupported value %r" % (path, system))
    if mode not in WINDOWING_MODES:
        errors.append("%s.mode: unsupported value %r" % (path, mode))
    display = window.get("display")
    if system == "x11":
        if not isinstance(display, str) or X11_DISPLAY.fullmatch(display) is None:
            errors.append("%s.display: X11 surface requires inherit or :<display>[.<screen>]" % path)
    elif display is not None and (not isinstance(display, str) or not 1 <= len(display) <= 128):
        errors.append("%s.display: must be a non-empty string of at most 128 characters" % path)
    if mode == "display-provider" and display == "inherit":
        errors.append("%s.display: display provider cannot inherit" % path)
    title = window.get("title")
    identity = window.get("identity")
    if mode in {"window", "fullscreen", "overlay"}:
        if not isinstance(title, str) or not 1 <= len(title) <= 256:
            errors.append("%s.title: visible surface requires a non-empty title" % path)
        if not isinstance(identity, dict):
            errors.append("%s.identity: visible surface requires an identity object" % path)
    elif title is not None and (not isinstance(title, str) or not 1 <= len(title) <= 256):
        errors.append("%s.title: must be a non-empty string of at most 256 characters" % path)
    if isinstance(identity, dict):
        for name in identity:
            if name not in WINDOW_IDENTITY_FIELDS:
                errors.append("%s.identity: unknown field %r" % (path, name))
        app_id = identity.get("app_id")
        if not isinstance(app_id, str) or APP_ID.fullmatch(app_id) is None:
            errors.append("%s.identity.app_id: required stable app id has invalid syntax" % path)
        for name, maximum in (
            ("x11_wm_class", 128),
            ("x11_wm_instance", 128),
            ("wayland_app_id", 255),
        ):
            value = identity.get(name)
            if value is not None and (not isinstance(value, str) or not 1 <= len(value) <= maximum):
                errors.append("%s.identity.%s: must be a non-empty bounded string" % (path, name))
    edge = window.get("edge")
    if edge is not None and edge not in {"top", "right", "bottom", "left"}:
        errors.append("%s.edge: unsupported value %r" % (path, edge))
    if edge is not None and mode != "overlay":
        errors.append("%s.edge: is only valid for overlay surfaces" % path)
    return errors


def _role_window_identity_keys(window: Mapping[str, Any]) -> List[Tuple[str, ...]]:
    identity = window.get("identity")
    if not isinstance(identity, dict):
        return []
    keys: List[Tuple[str, ...]] = []
    if isinstance(identity.get("app_id"), str):
        keys.append(("app_id", identity["app_id"]))
    if isinstance(identity.get("x11_wm_class"), str):
        keys.append((
            "x11",
            identity["x11_wm_class"],
            str(identity.get("x11_wm_instance", "")),
        ))
    if isinstance(identity.get("wayland_app_id"), str):
        keys.append(("wayland", identity["wayland_app_id"]))
    return keys


def validate_manifest_claims(
    manifest: Any,
    contracts: Iterable[Mapping[str, Any]],
    source: str = "<manifest>",
    *,
    allow_unversioned: bool = False,
) -> List[str]:
    """Validate role claims plus provider declaration constraints."""

    catalog, errors = contract_catalog(contracts)
    known_roles = {contract.get("role") for contract in catalog.values()}
    if not isinstance(manifest, dict):
        return errors + ["%s: manifest must be an object" % source]
    components = manifest.get("components")
    if not isinstance(components, list):
        return errors + ["%s.components: must be an array" % source]
    for component_index, component in enumerate(components):
        component_path = "%s.components[%d]" % (source, component_index)
        if not isinstance(component, dict):
            errors.append("%s: must be an object" % component_path)
            continue
        provides = component.get("provides", [])
        if not isinstance(provides, list):
            errors.append("%s.provides: must be an array" % component_path)
            continue
        claimed_roles = [
            item.get("role")
            for item in provides
            if isinstance(item, dict) and isinstance(item.get("role"), str)
        ]
        claimed_role_set = set(claimed_roles)
        for role in sorted(claimed_role_set):
            if claimed_roles.count(role) > 1:
                errors.append("%s.provides: conflicting duplicate role claim %r" % (component_path, role))
        role_windows_raw = component.get("x-msys-role-windows")
        role_windows: Dict[str, Mapping[str, Any]] = {}
        if role_windows_raw is not None:
            role_windows_path = "%s.x-msys-role-windows" % component_path
            if not isinstance(role_windows_raw, dict) or not role_windows_raw:
                errors.append("%s: must be a non-empty object" % role_windows_path)
            else:
                identities: Dict[Tuple[str, ...], str] = {}
                for role, descriptor in role_windows_raw.items():
                    descriptor_path = "%s.%s" % (role_windows_path, role)
                    if not isinstance(role, str) or ROLE.fullmatch(role) is None:
                        errors.append("%s: invalid role key %r" % (role_windows_path, role))
                        continue
                    if role not in known_roles:
                        errors.append("%s: unknown versioned role %r" % (descriptor_path, role))
                    if role not in claimed_role_set:
                        errors.append("%s: role has no corresponding component claim" % descriptor_path)
                    errors.extend(_validate_role_window(descriptor, descriptor_path))
                    if not isinstance(descriptor, dict):
                        continue
                    role_windows[role] = descriptor
                    for identity_key in _role_window_identity_keys(descriptor):
                        previous = identities.get(identity_key)
                        if previous is not None and previous != role:
                            errors.append(
                                "%s.identity: conflicts with role %r stable surface identity"
                                % (descriptor_path, previous)
                            )
                        else:
                            identities[identity_key] = role
                legacy = component.get("windowing")
                if isinstance(legacy, dict) and role_windows and not any(
                    _json_equal(legacy, descriptor) for descriptor in role_windows.values()
                ):
                    errors.append(
                        "%s.windowing: conflicts with x-msys-role-windows; legacy metadata must mirror one declared role surface"
                        % component_path
                    )
        interfaces = {
            item.get("interface") for item in provides if isinstance(item, dict) and isinstance(item.get("interface"), str)
        }
        capabilities = {
            item.get("capability") for item in provides if isinstance(item, dict) and isinstance(item.get("capability"), str)
        }
        for provide_index, provide in enumerate(provides):
            path = "%s.provides[%d]" % (component_path, provide_index)
            if not isinstance(provide, dict) or not isinstance(provide.get("role"), str):
                continue
            role = provide["role"]
            claim = provide.get("x-msys-contract")
            if claim is None:
                if role in known_roles and not allow_unversioned:
                    errors.append("%s: role %r is missing x-msys-contract" % (path, role))
                continue
            if not isinstance(claim, dict):
                errors.append("%s.x-msys-contract: must be an object" % path)
                continue
            unknown = set(claim) - {"id", "version"}
            if unknown:
                errors.append("%s.x-msys-contract: unknown field %r" % (path, sorted(unknown)[0]))
            if set(claim) < {"id", "version"}:
                errors.append("%s.x-msys-contract: id and version are required" % path)
                continue
            key = (claim.get("id"), claim.get("version"))
            contract = catalog.get(key)
            if contract is None:
                errors.append("%s.x-msys-contract: unknown contract revision %r@%r" % (path, key[0], key[1]))
                continue
            if contract.get("role") != role:
                errors.append("%s: claim is for role %r, not %r" % (path, contract.get("role"), role))
            provider = contract.get("provider", {})
            if provide.get("exclusive") is not provider.get("exclusive"):
                errors.append("%s.exclusive: must be %r for %s" % (path, provider.get("exclusive"), contract.get("id")))
            readiness = component.get("readiness", {})
            readiness_mode = readiness.get("mode") if isinstance(readiness, dict) else None
            if readiness_mode not in provider.get("readiness_modes", []):
                errors.append("%s.readiness.mode: %r is not allowed by %s" % (component_path, readiness_mode, contract.get("id")))
            if component.get("lifecycle") not in provider.get("lifecycle_modes", []):
                errors.append("%s.lifecycle: %r is not allowed by %s" % (component_path, component.get("lifecycle"), contract.get("id")))
            windowing = role_windows.get(role, component.get("windowing"))
            windowing_path = (
                "%s.x-msys-role-windows.%s" % (component_path, role)
                if role in role_windows
                else "%s.windowing" % component_path
            )
            if provider.get("windowing_required") and not isinstance(windowing, dict):
                errors.append("%s: required by %s" % (windowing_path, contract.get("id")))
            if isinstance(windowing, dict) and provider.get("windowing_modes"):
                if windowing.get("mode") not in provider["windowing_modes"]:
                    errors.append("%s.mode: %r is not allowed by %s" % (windowing_path, windowing.get("mode"), contract.get("id")))
                if windowing.get("mode") == "display-provider" and windowing.get("display") == "inherit":
                    errors.append("%s.display: display provider cannot inherit" % windowing_path)
            missing_interfaces = set(provider.get("required_interfaces", [])) - interfaces
            if missing_interfaces:
                errors.append("%s.provides: missing required interface %r" % (component_path, sorted(missing_interfaces)[0]))
            missing_capabilities = set(provider.get("required_capabilities", [])) - capabilities
            if missing_capabilities:
                errors.append("%s.provides: missing required capability %r" % (component_path, sorted(missing_capabilities)[0]))
    return errors


class AdapterSession:
    """Cross-platform JSON-lines adapter process with bounded reads."""

    def __init__(self, command: Sequence[str]) -> None:
        if not command:
            raise ValueError("adapter command is empty")
        self.process = subprocess.Popen(
            list(command),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            text=False,
            bufsize=0,
        )
        self.responses: "queue.Queue[Tuple[str, Any]]" = queue.Queue()
        self._reader = threading.Thread(target=self._read, name="msys-contract-adapter", daemon=True)
        self._reader.start()

    def _read(self) -> None:
        assert self.process.stdout is not None
        try:
            while True:
                raw = self.process.stdout.readline(MAX_ADAPTER_PACKET + 1)
                if not raw:
                    break
                if len(raw) > MAX_ADAPTER_PACKET:
                    while raw and not raw.endswith(b"\n"):
                        raw = self.process.stdout.readline(MAX_ADAPTER_PACKET + 1)
                    self.responses.put(("error", ValueError("adapter packet exceeds 256 KiB")))
                    continue
                if not raw.endswith(b"\n"):
                    self.responses.put(("error", ValueError("adapter packet is not newline terminated")))
                    continue
                self.responses.put(("line", raw.decode("utf-8", errors="strict")))
        except (OSError, UnicodeError) as exc:
            self.responses.put(("error", exc))
        finally:
            self.responses.put(("eof", None))

    def exchange(self, message: Mapping[str, Any], timeout_ms: int) -> Any:
        if self.process.poll() is not None:
            raise RuntimeError("adapter exited with status %s" % self.process.returncode)
        assert self.process.stdin is not None
        wire = (json.dumps(message, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
        if len(wire) > MAX_ADAPTER_PACKET:
            raise ValueError("adapter request exceeds 256 KiB")
        try:
            self.process.stdin.write(wire)
            self.process.stdin.flush()
        except (OSError, BrokenPipeError) as exc:
            raise RuntimeError("adapter input failed: %s" % exc) from exc
        try:
            kind, value = self.responses.get(timeout=timeout_ms / 1000.0)
        except queue.Empty as exc:
            raise TimeoutError("adapter response exceeded %d ms" % timeout_ms) from exc
        if kind == "eof":
            raise RuntimeError("adapter closed stdout before replying")
        if kind == "error":
            raise RuntimeError("adapter output failed: %s" % value)
        try:
            return json.loads(value, object_pairs_hook=_strict_object)
        except (json.JSONDecodeError, DuplicateKeyError, ValueError) as exc:
            raise RuntimeError("adapter emitted invalid JSON: %s" % exc) from exc

    def close(self) -> None:
        try:
            if self.process.stdin is not None:
                self.process.stdin.close()
        except OSError:
            pass
        try:
            self.process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=1.0)
        self._reader.join(timeout=1.0)
        if self.process.stdout is not None:
            self.process.stdout.close()


def _response_frame_errors(response: Any, request_id: int, path: str) -> List[str]:
    if not isinstance(response, dict):
        return ["%s: adapter response must be an object" % path]
    errors: List[str] = []
    if response.get("schema") != ADAPTER_SCHEMA:
        errors.append("%s.schema: expected %r" % (path, ADAPTER_SCHEMA))
    if response.get("id") != request_id or isinstance(response.get("id"), bool):
        errors.append("%s.id: must echo integer request id %d" % (path, request_id))
    return errors


def run_conformance(
    contract: Mapping[str, Any],
    command: Sequence[str],
    *,
    include_mutating: bool = False,
    timeout_ms: int = 3000,
) -> Tuple[int, int, List[str]]:
    """Run descriptor cases through one language-neutral JSON-lines adapter."""

    errors = validate_contract(contract)
    if errors:
        return 0, 0, errors
    try:
        session = AdapterSession(command)
    except (OSError, ValueError) as exc:
        return 0, 0, ["adapter start: %s" % exc]
    executed = 0
    skipped = 0
    request_id = 0
    try:
        try:
            response = session.exchange(
                {"schema": ADAPTER_SCHEMA, "id": request_id, "op": "describe"},
                timeout_ms,
            )
        except Exception as exc:
            return executed, skipped, ["adapter describe: %s" % exc]
        errors.extend(_response_frame_errors(response, request_id, "adapter.describe"))
        if isinstance(response, dict):
            if response.get("type") != "describe":
                errors.append("adapter.describe.type: expected 'describe'")
            claims = response.get("contracts")
            if not isinstance(claims, list):
                errors.append("adapter.describe.contracts: must be an array")
            else:
                wanted = {"id": contract["id"], "version": contract["version"]}
                if not any(isinstance(item, dict) and item == wanted for item in claims):
                    errors.append("adapter.describe.contracts: missing exact claim %s@%s" % (contract["id"], contract["version"]))
        if errors:
            return executed, skipped, errors
        for method in contract["methods"]:
            for case in method["conformance"]:
                if case["mode"] == "mutating" and not include_mutating:
                    skipped += 1
                    continue
                request_id += 1
                executed += 1
                request = {
                    "schema": ADAPTER_SCHEMA,
                    "id": request_id,
                    "op": "call",
                    "contract": {"id": contract["id"], "version": contract["version"]},
                    "case": case["id"],
                    "target": contract["target"],
                    "method": method["name"],
                    "payload": case["request"],
                }
                label = "%s.%s" % (method["name"], case["id"])
                try:
                    response = session.exchange(request, timeout_ms)
                except Exception as exc:
                    errors.append("%s: %s" % (label, exc))
                    continue
                frame_errors = _response_frame_errors(response, request_id, label)
                errors.extend(frame_errors)
                if frame_errors or not isinstance(response, dict):
                    continue
                expected = case["expect"]
                if response.get("type") != expected["type"]:
                    errors.append("%s.type: expected %r, got %r" % (label, expected["type"], response.get("type")))
                    continue
                if response["type"] == "return":
                    if "payload" not in response:
                        errors.append("%s.payload: return frame is missing payload" % label)
                    else:
                        errors.extend(validate_instance(response["payload"], method["response"], contract, "%s.payload" % label))
                else:
                    code = response.get("code")
                    if code not in expected.get("codes", []):
                        errors.append("%s.code: expected one of %r, got %r" % (label, expected.get("codes", []), code))
                    if not isinstance(response.get("message"), str) or not response.get("message"):
                        errors.append("%s.message: error frame requires a non-empty message" % label)
    finally:
        session.close()
    return executed, skipped, errors


def _default_contract_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "contracts" / "roles"


def _print_errors(errors: Sequence[str]) -> int:
    for error in errors:
        print("error: %s" % error, file=sys.stderr)
    return 1 if errors else 0


def command_validate(args: argparse.Namespace) -> int:
    paths = [Path(item) for item in (args.paths or [_default_contract_dir()])]
    loaded, errors = discover_contracts(paths)
    if not errors:
        print("validated %d role contract(s)" % len(loaded))
    return _print_errors(errors)


def command_manifest(args: argparse.Namespace) -> int:
    loaded, errors = discover_contracts([Path(args.contracts)])
    if errors:
        return _print_errors(errors)
    try:
        manifest = load_json(Path(args.manifest))
    except ContractFormatError as exc:
        return _print_errors([str(exc)])
    errors = validate_manifest_claims(
        manifest,
        [item for _, item in loaded],
        args.manifest,
        allow_unversioned=args.allow_unversioned,
    )
    if not errors:
        print("manifest role claims conform: %s" % args.manifest)
    return _print_errors(errors)


def command_run(args: argparse.Namespace) -> int:
    try:
        contract = load_json(Path(args.contract))
    except ContractFormatError as exc:
        return _print_errors([str(exc)])
    command = list(args.adapter)
    if command and command[0] == "--":
        command.pop(0)
    if not command:
        return _print_errors(["adapter command is required after --"])
    executed, skipped, errors = run_conformance(
        contract,
        command,
        include_mutating=args.include_mutating,
        timeout_ms=args.timeout_ms,
    )
    if errors:
        print("conformance failed: %d executed, %d skipped, %d error(s)" % (executed, skipped, len(errors)), file=sys.stderr)
    else:
        print("conformance passed: %d executed, %d skipped" % (executed, skipped))
    return _print_errors(errors)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate", help="validate descriptor files or directories")
    validate.add_argument("paths", nargs="*", help="JSON contract paths; defaults to built-in role contracts")
    validate.set_defaults(handler=command_validate)

    manifest = subparsers.add_parser("manifest", help="validate manifest role contract claims")
    manifest.add_argument("manifest")
    manifest.add_argument("--contracts", default=str(_default_contract_dir()))
    manifest.add_argument(
        "--allow-unversioned",
        action="store_true",
        help="permit known roles without x-msys-contract during migration",
    )
    manifest.set_defaults(handler=command_manifest)

    run = subparsers.add_parser("run", help="run behavioral cases through a JSON-lines adapter")
    run.add_argument("contract")
    run.add_argument("--timeout-ms", type=int, default=3000)
    run.add_argument("--include-mutating", action="store_true")
    run.add_argument("adapter", nargs="*", help="adapter command, conventionally after --")
    run.set_defaults(handler=command_run)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "timeout_ms", 1) < 1 or getattr(args, "timeout_ms", 1) > 300000:
        parser.error("--timeout-ms must be between 1 and 300000")
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
