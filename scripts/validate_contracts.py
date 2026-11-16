#!/usr/bin/env python3
"""Offline validation for VigorSync M0 contracts."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Iterable

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012


ROOT = Path(__file__).resolve().parents[1]
CONTRACTS_DIR = ROOT / "packages" / "contracts"
EXAMPLES_DIR = CONTRACTS_DIR / "examples"
OPENAPI_PATH = CONTRACTS_DIR / "openapi.yaml"
STATE_MACHINE_PATH = CONTRACTS_DIR / "session-state-machine.json"

REQUIRED_PATHS = {
    "/api/v1/health",
    "/api/v1/pairing/claim",
    "/api/v1/sessions",
    "/api/v1/sessions/{session_id}",
    "/api/v1/sessions/{session_id}/start",
    "/api/v1/sessions/{session_id}/pause",
    "/api/v1/sessions/{session_id}/resume",
    "/api/v1/sessions/{session_id}/stop",
    "/api/v1/sessions/{session_id}/cancel",
    "/api/v1/sessions/{session_id}/events",
    "/api/v1/sessions/{session_id}/report",
    "/api/v1/sessions/{session_id}/media",
    "/api/v1/internal/sessions/{session_id}/state",
    "/api/v1/internal/sessions/{session_id}/finalize",
    "/internal/v1/sessions/{session_id}/start",
    "/internal/v1/sessions/{session_id}/pause",
    "/internal/v1/sessions/{session_id}/resume",
    "/internal/v1/sessions/{session_id}/stop",
    "/live/{session_id}/stream.mjpg",
    "/api/v1/ws/client",
    "/api/v1/ws/mirror",
    "/ws/live/{session_id}",
}

IDEMPOTENT_OPERATIONS = {
    ("/api/v1/sessions", "post"),
    ("/api/v1/sessions/{session_id}/start", "post"),
    ("/api/v1/sessions/{session_id}/pause", "post"),
    ("/api/v1/sessions/{session_id}/resume", "post"),
    ("/api/v1/sessions/{session_id}/stop", "post"),
    ("/api/v1/sessions/{session_id}/cancel", "post"),
    ("/api/v1/sessions/{session_id}/media", "delete"),
}

STATUSES = {
    "created",
    "preparing",
    "active",
    "paused",
    "stopping",
    "analyzing",
    "completed",
    "failed",
    "cancelled",
}


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def load_schemas() -> dict[str, dict[str, Any]]:
    return {
        path.name: load_json(path)
        for path in sorted(CONTRACTS_DIR.glob("*.schema.json"))
    }


def build_registry(schemas: dict[str, dict[str, Any]]) -> Registry:
    resources = []
    for schema in schemas.values():
        resource = Resource.from_contents(schema, default_specification=DRAFT202012)
        resources.append((schema["$id"], resource))
    return Registry().with_resources(resources)


def validator_for(schema: dict[str, Any], registry: Registry) -> Draft202012Validator:
    return Draft202012Validator(
        schema,
        registry=registry,
        format_checker=FormatChecker(),
    )


def format_error_path(error: Any) -> str:
    path = "$"
    for part in error.absolute_path:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += f".{part}"
    return path


def validate_payload(
    payload: Any,
    schema: dict[str, Any],
    registry: Registry,
) -> list[str]:
    validator = validator_for(schema, registry)
    return [
        f"{format_error_path(error)}: {error.message}"
        for error in sorted(validator.iter_errors(payload), key=lambda item: list(item.absolute_path))
    ]


def iter_refs(node: Any) -> Iterable[str]:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str):
            yield ref
        for value in node.values():
            yield from iter_refs(value)
    elif isinstance(node, list):
        for value in node:
            yield from iter_refs(value)


def resolve_json_pointer(document: Any, fragment: str) -> Any:
    if fragment in ("", "#"):
        return document
    pointer = fragment[1:] if fragment.startswith("#") else fragment
    if not pointer.startswith("/"):
        raise ValueError(f"unsupported fragment: {fragment}")
    current = document
    for raw_part in pointer[1:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            current = current[int(part)]
        else:
            current = current[part]
    return current


def validate_schema_references(
    schemas: dict[str, dict[str, Any]],
    registry: Registry,
) -> list[str]:
    errors: list[str] = []
    for name, schema in schemas.items():
        for ref in iter_refs(schema):
            try:
                if ref.startswith("#"):
                    registry.resolver(base_uri=schema["$id"]).lookup(ref)
                else:
                    registry.resolver().lookup(ref)
            except Exception as exc:  # referencing raises multiple exception types
                errors.append(f"{name}: unresolved $ref {ref}: {exc}")
    return errors


def validate_schemas(schemas: dict[str, dict[str, Any]], registry: Registry) -> list[str]:
    errors: list[str] = []
    for name, schema in schemas.items():
        try:
            Draft202012Validator.check_schema(schema)
        except Exception as exc:
            errors.append(f"{name}: invalid JSON Schema: {exc}")
            continue
        if not schema.get("$id"):
            errors.append(f"{name}: missing $id")
    errors.extend(validate_schema_references(schemas, registry))
    return errors


def validate_examples(schemas: dict[str, dict[str, Any]], registry: Registry) -> list[str]:
    errors: list[str] = []
    manifest = load_json(EXAMPLES_DIR / "manifest.json")
    entries = manifest.get("examples", [])
    if not entries:
        return ["examples/manifest.json: no examples declared"]

    seen_files: set[str] = set()
    for entry in entries:
        filename = entry.get("file", "")
        schema_name = entry.get("schema", "")
        if not filename or not schema_name:
            errors.append(f"examples/manifest.json: invalid entry {entry!r}")
            continue
        if filename in seen_files:
            errors.append(f"examples/manifest.json: duplicate example {filename}")
        seen_files.add(filename)
        if schema_name not in schemas:
            errors.append(f"{filename}: unknown schema {schema_name}")
            continue
        try:
            payload = load_json(EXAMPLES_DIR / filename)
        except Exception as exc:
            errors.append(f"{filename}: cannot load example: {exc}")
            continue
        errors.extend(
            f"{filename}: {message}"
            for message in validate_payload(payload, schemas[schema_name], registry)
        )
    return errors


def validate_state_machine(
    schemas: dict[str, dict[str, Any]],
    registry: Registry,
) -> list[str]:
    errors: list[str] = []
    machine = load_json(STATE_MACHINE_PATH)
    errors.extend(
        f"session-state-machine.json: {message}"
        for message in validate_payload(
            machine,
            schemas["session-state-machine.schema.json"],
            registry,
        )
    )
    if errors:
        return errors

    initial = machine["initial"]
    terminal = set(machine["terminal"])
    transitions = machine["transitions"]
    if initial not in STATUSES:
        errors.append(f"session-state-machine.json: unknown initial state {initial}")
    if not terminal <= STATUSES:
        errors.append("session-state-machine.json: terminal contains unknown status")

    seen: set[tuple[str, str, str]] = set()
    outgoing: dict[str, int] = {status: 0 for status in STATUSES}
    incoming: dict[str, int] = {status: 0 for status in STATUSES}
    edges: dict[str, set[str]] = {status: set() for status in STATUSES}
    for transition in transitions:
        source = transition["from"]
        target = transition["to"]
        key = (source, target, transition["trigger"])
        if key in seen:
            errors.append(f"session-state-machine.json: duplicate transition {key}")
        seen.add(key)
        if source in terminal:
            errors.append(f"session-state-machine.json: terminal state {source} has outgoing transition")
        outgoing[source] += 1
        incoming[target] += 1
        edges[source].add(target)

    for status in STATUSES:
        if status != initial and incoming[status] == 0:
            errors.append(f"session-state-machine.json: {status} is unreachable")
        if status not in terminal and outgoing[status] == 0:
            errors.append(f"session-state-machine.json: non-terminal {status} has no outgoing transition")

    reachable = {initial}
    frontier = [initial]
    while frontier:
        source = frontier.pop()
        for target in edges[source] - reachable:
            reachable.add(target)
            frontier.append(target)
    missing = STATUSES - reachable
    if missing:
        errors.append(f"session-state-machine.json: statuses not reachable from initial: {sorted(missing)}")
    return errors


def validate_openapi() -> list[str]:
    errors: list[str] = []
    try:
        document = yaml.safe_load(OPENAPI_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        return [f"openapi.yaml: cannot parse YAML: {exc}"]
    if not isinstance(document, dict):
        return ["openapi.yaml: root must be an object"]
    if document.get("openapi") != "3.1.0":
        errors.append("openapi.yaml: openapi must be 3.1.0")
    if not document.get("info", {}).get("title"):
        errors.append("openapi.yaml: info.title is required")

    paths = document.get("paths", {})
    missing = REQUIRED_PATHS - set(paths)
    if missing:
        errors.append(f"openapi.yaml: missing required paths: {sorted(missing)}")

    operation_ids: set[str] = set()
    for path, path_item in paths.items():
        for method, operation in path_item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            operation_id = operation.get("operationId")
            if not operation_id:
                errors.append(f"openapi.yaml: {method.upper()} {path} has no operationId")
            elif operation_id in operation_ids:
                errors.append(f"openapi.yaml: duplicate operationId {operation_id}")
            else:
                operation_ids.add(operation_id)
            if not operation.get("responses"):
                errors.append(f"openapi.yaml: {method.upper()} {path} has no responses")

    for path, method in IDEMPOTENT_OPERATIONS:
        operation = paths.get(path, {}).get(method, {})
        parameter_refs = {
            parameter.get("$ref")
            for parameter in operation.get("parameters", [])
            if isinstance(parameter, dict)
        }
        if "#/components/parameters/IdempotencyKey" not in parameter_refs:
            errors.append(f"openapi.yaml: {method.upper()} {path} must require Idempotency-Key")

    for ref in iter_refs(document):
        try:
            if ref.startswith("#"):
                resolve_json_pointer(document, ref)
                continue
            file_part, _, fragment = ref.partition("#")
            target = (OPENAPI_PATH.parent / file_part).resolve()
            if CONTRACTS_DIR.resolve() not in target.parents and target != CONTRACTS_DIR.resolve():
                errors.append(f"openapi.yaml: ref escapes contracts directory: {ref}")
                continue
            if target.suffix == ".json":
                referenced = load_json(target)
            elif target.suffix in {".yaml", ".yml"}:
                referenced = yaml.safe_load(target.read_text(encoding="utf-8"))
            else:
                errors.append(f"openapi.yaml: unsupported ref target: {ref}")
                continue
            if fragment:
                resolve_json_pointer(referenced, fragment)
        except Exception as exc:
            errors.append(f"openapi.yaml: unresolved $ref {ref}: {exc}")

    for extension, label in (
        ("x-session-state-machine", "state machine"),
        ("x-event-schema", "event schema"),
    ):
        if extension == "x-session-state-machine":
            value = document.get(extension)
            if value and not (OPENAPI_PATH.parent / value).is_file():
                errors.append(f"openapi.yaml: missing {label} reference {value}")
    live_ws = paths.get("/ws/live/{session_id}", {}).get("get", {})
    event_schema = live_ws.get("x-event-schema")
    if event_schema and not (OPENAPI_PATH.parent / event_schema).is_file():
        errors.append(f"openapi.yaml: missing event schema reference {event_schema}")

    for server in document.get("servers", []):
        url = server.get("url", "")
        if "127.0.0.1" not in url and "{host}" not in url:
            errors.append(f"openapi.yaml: unexpected public server URL {url}")

    try:
        from openapi_spec_validator import validate as validate_openapi_spec
    except ImportError:
        pass
    else:
        try:
            validate_openapi_spec(document, base_uri=OPENAPI_PATH.as_uri())
        except Exception as exc:
            errors.append(f"openapi.yaml: OpenAPI 3.1 validation failed: {exc}")
    return errors


def check_contracts() -> list[str]:
    schemas = load_schemas()
    registry = build_registry(schemas)
    errors: list[str] = []
    errors.extend(validate_schemas(schemas, registry))
    if errors:
        return errors
    errors.extend(validate_examples(schemas, registry))
    errors.extend(validate_state_machine(schemas, registry))
    errors.extend(validate_openapi())
    return errors


def main() -> int:
    errors = check_contracts()
    if errors:
        print(f"contract validation failed with {len(errors)} issue(s):", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("contract validation passed")
    print(f"- schemas: {len(list(CONTRACTS_DIR.glob('*.schema.json')))}")
    print(f"- examples: {len(load_json(EXAMPLES_DIR / 'manifest.json')['examples'])}")
    print("- state machine: valid")
    print("- openapi: valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
