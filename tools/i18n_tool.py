#!/usr/bin/env python3
"""Validate dependency-free MSYS i18n catalogs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Set, Tuple


CATALOG_SCHEMA = "msys.i18n.catalog.v1"
CATALOG_META_SCHEMA = "https://msys.local/schemas/i18n-catalog.v1.json"
CATALOG_ID = re.compile(
    r"^[a-z0-9][a-z0-9_-]*(?:\.[a-z0-9][a-z0-9_-]*)+$"
)
LOCALE = re.compile(
    r"^[a-z]{2,8}(?:-[A-Z][a-z]{3})?"
    r"(?:-(?:[A-Z]{2}|[0-9]{3}))?"
    r"(?:-(?:[a-z0-9]{5,8}|[0-9][a-z0-9]{3}))*$"
)
MESSAGE_KEY = re.compile(
    r"^[a-z][a-z0-9_-]*(?:\.[a-z0-9][a-z0-9_-]*)*$"
)
PLACEHOLDER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
EXTENSION_FIELD = re.compile(r"^x-[a-z0-9][a-z0-9._-]*$")
ROOT_FIELDS = {
    "$schema",
    "schema",
    "id",
    "description",
    "default_locale",
    "messages",
}


class CatalogFormatError(ValueError):
    """A catalog is not strict UTF-8 JSON."""


def _strict_object(pairs: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member %r" % key)
        result[key] = value
    return result


def load_json(path: Path) -> Any:
    """Load strict UTF-8 JSON without duplicate members or numeric constants."""

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
        raise CatalogFormatError("%s: %s" % (path, exc)) from exc


def parse_placeholders(template: str) -> Tuple[Set[str], List[str]]:
    """Return placeholder names and every v1 brace-grammar error."""

    names: Set[str] = set()
    errors: List[str] = []
    index = 0
    while index < len(template):
        character = template[index]
        if character == "{":
            if index + 1 < len(template) and template[index + 1] == "{":
                index += 2
                continue
            end = template.find("}", index + 1)
            if end < 0:
                errors.append("unclosed '{' at offset %d" % index)
                break
            name = template[index + 1 : end]
            if PLACEHOLDER.fullmatch(name) is None:
                errors.append("invalid placeholder %r at offset %d" % (name, index))
            else:
                names.add(name)
            index = end + 1
            continue
        if character == "}":
            if index + 1 < len(template) and template[index + 1] == "}":
                index += 2
                continue
            errors.append("unescaped '}' at offset %d" % index)
        index += 1
    return names, errors


def validate_catalog(document: Any, source: str = "<catalog>") -> List[str]:
    """Return all structural and semantic v1 catalog errors."""

    if not isinstance(document, dict):
        return ["%s: catalog must be an object" % source]

    errors: List[str] = []
    for field in document:
        if field not in ROOT_FIELDS and (
            not isinstance(field, str) or EXTENSION_FIELD.fullmatch(field) is None
        ):
            errors.append("%s: unknown field %r" % (source, field))
    for field in ("schema", "id", "default_locale", "messages"):
        if field not in document:
            errors.append("%s: missing required field %r" % (source, field))

    if document.get("schema") != CATALOG_SCHEMA:
        errors.append("%s.schema: expected %r" % (source, CATALOG_SCHEMA))
    if "$schema" in document and document["$schema"] != CATALOG_META_SCHEMA:
        errors.append("%s.$schema: expected %r" % (source, CATALOG_META_SCHEMA))

    catalog_id = document.get("id")
    if (
        not isinstance(catalog_id, str)
        or len(catalog_id) > 160
        or CATALOG_ID.fullmatch(catalog_id) is None
    ):
        errors.append("%s.id: invalid catalog id" % source)
    description = document.get("description")
    if description is not None and (
        not isinstance(description, str)
        or not description
        or len(description) > 256
    ):
        errors.append("%s.description: expected 1..256 characters" % source)

    default_locale = document.get("default_locale")
    if (
        not isinstance(default_locale, str)
        or len(default_locale) > 63
        or LOCALE.fullmatch(default_locale) is None
    ):
        errors.append("%s.default_locale: invalid canonical locale" % source)

    messages = document.get("messages")
    if not isinstance(messages, dict) or not messages:
        errors.append("%s.messages: expected a non-empty object" % source)
        return errors
    if len(messages) > 128:
        errors.append("%s.messages: at most 128 locales are allowed" % source)
    if isinstance(default_locale, str) and default_locale not in messages:
        errors.append("%s.messages: default_locale %r is absent" % (source, default_locale))

    parsed: Dict[str, Dict[str, Set[str]]] = {}
    for locale, message_map in messages.items():
        locale_path = "%s.messages.%s" % (source, locale)
        if (
            not isinstance(locale, str)
            or len(locale) > 63
            or LOCALE.fullmatch(locale) is None
        ):
            errors.append("%s: invalid canonical locale" % locale_path)
        if not isinstance(message_map, dict) or not message_map:
            errors.append("%s: expected a non-empty message object" % locale_path)
            continue
        if len(message_map) > 20000:
            errors.append("%s: at most 20000 messages are allowed" % locale_path)
        parsed[locale] = {}
        for key, template in message_map.items():
            key_path = "%s.%s" % (locale_path, key)
            if (
                not isinstance(key, str)
                or len(key) > 160
                or MESSAGE_KEY.fullmatch(key) is None
            ):
                errors.append("%s: invalid message key" % key_path)
            if not isinstance(template, str):
                errors.append("%s: message must be a string" % key_path)
                continue
            if len(template) > 16384 or "\x00" in template:
                errors.append("%s: message is too long or contains NUL" % key_path)
            names, template_errors = parse_placeholders(template)
            parsed[locale][key] = names
            for detail in template_errors:
                errors.append("%s: %s" % (key_path, detail))

    default_messages = messages.get(default_locale)
    default_names = parsed.get(default_locale, {})
    if isinstance(default_messages, dict):
        default_keys = set(default_messages)
        for locale, message_map in messages.items():
            if not isinstance(message_map, dict) or locale == default_locale:
                continue
            for key in message_map:
                key_path = "%s.messages.%s.%s" % (source, locale, key)
                if key not in default_keys:
                    errors.append("%s: key is absent from the default locale" % key_path)
                    continue
                if key in parsed.get(locale, {}) and parsed[locale][key] != default_names.get(key):
                    errors.append(
                        "%s: placeholders %s do not match default %s"
                        % (
                            key_path,
                            sorted(parsed[locale][key]),
                            sorted(default_names.get(key, set())),
                        )
                    )
    return errors


def _catalog_paths(raw_paths: Iterable[str]) -> List[Path]:
    paths: List[Path] = []
    for raw in raw_paths:
        path = Path(raw)
        if path.is_dir():
            paths.extend(sorted(path.rglob("*.json")))
        else:
            paths.append(path)
    return paths


def command_validate(paths: Sequence[str]) -> int:
    selected = _catalog_paths(paths or ["examples/i18n"])
    if not selected:
        print("no catalog files found", file=sys.stderr)
        return 2
    errors: List[str] = []
    for path in selected:
        try:
            document = load_json(path)
        except CatalogFormatError as exc:
            errors.append(str(exc))
            continue
        errors.extend(validate_catalog(document, str(path)))
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print("validated %d i18n catalog(s)" % len(selected))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["validate"])
    parser.add_argument("paths", nargs="*")
    arguments = parser.parse_args(argv)
    if arguments.command == "validate":
        return command_validate(arguments.paths)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
