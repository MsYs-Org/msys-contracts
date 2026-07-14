from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tools.i18n_tool import (
    CATALOG_META_SCHEMA,
    CatalogFormatError,
    load_json,
    parse_placeholders,
    validate_catalog,
)


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = sorted((ROOT / "examples" / "i18n").glob("*.json"))


class I18nCatalogTests(unittest.TestCase):
    def test_all_examples_are_valid(self) -> None:
        self.assertGreaterEqual(len(EXAMPLES), 2)
        for path in EXAMPLES:
            with self.subTest(path=path.name):
                self.assertEqual(validate_catalog(load_json(path), str(path)), [])

    def test_partial_locale_inherits_but_cannot_invent_keys(self) -> None:
        document = load_json(ROOT / "examples" / "i18n" / "developer-app.catalog.json")
        self.assertNotIn("tasks.count", document["messages"]["zh-CN"])
        broken = copy.deepcopy(document)
        broken["messages"]["zh-CN"]["new.key"] = "额外"
        errors = validate_catalog(broken)
        self.assertTrue(any("absent from the default locale" in item for item in errors))

    def test_placeholder_sets_must_match(self) -> None:
        document = load_json(ROOT / "examples" / "i18n" / "settings.catalog.json")
        document["messages"]["zh-CN"]["wifi.connected"] = "已连接到 {network}"
        errors = validate_catalog(document)
        self.assertTrue(any("placeholders" in item for item in errors))

    def test_placeholder_parser_accepts_only_bounded_grammar(self) -> None:
        names, errors = parse_placeholders("{{user}} {name} {count} {name}")
        self.assertEqual(names, {"name", "count"})
        self.assertEqual(errors, [])
        for template in ("{name!r}", "{user.name}", "{name:>8}", "{", "}"):
            with self.subTest(template=template):
                self.assertTrue(parse_placeholders(template)[1])

    def test_default_locale_must_be_present(self) -> None:
        document = load_json(ROOT / "examples" / "i18n" / "settings.catalog.json")
        document["default_locale"] = "fr-FR"
        self.assertTrue(any("default_locale" in item for item in validate_catalog(document)))

    def test_unknown_fields_and_noncanonical_locales_are_rejected(self) -> None:
        document = load_json(ROOT / "examples" / "i18n" / "settings.catalog.json")
        document["typo"] = True
        document["messages"]["zh_cn"] = {"window.title": "设置"}
        errors = validate_catalog(document)
        self.assertTrue(any("unknown field" in item for item in errors))
        self.assertTrue(any("canonical locale" in item for item in errors))

    def test_duplicate_json_members_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "duplicate.json"
            path.write_text('{"schema":"a","schema":"b"}', encoding="utf-8")
            with self.assertRaises(CatalogFormatError):
                load_json(path)

    def test_cli_validates_examples(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "tools.i18n_tool", "validate"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("2 i18n catalog", result.stdout)


class I18nMetaSchemaTests(unittest.TestCase):
    def test_meta_schema_identity_and_strict_root(self) -> None:
        schema = json.loads(
            (ROOT / "schemas" / "i18n-catalog.v1.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(schema["$id"], CATALOG_META_SCHEMA)
        self.assertEqual(schema["properties"]["schema"]["const"], "msys.i18n.catalog.v1")
        self.assertFalse(schema["additionalProperties"])

    def test_manifest_presentation_extension_is_strict_and_package_relative(self) -> None:
        schema = json.loads(
            (ROOT / "examples" / "package-manifest.schema.json").read_text(encoding="utf-8")
        )
        presentation = schema["$defs"]["i18nPresentation"]
        self.assertEqual(presentation["required"], ["catalog"])
        self.assertFalse(presentation["additionalProperties"])
        pattern = presentation["properties"]["catalog"]["pattern"]
        import re

        self.assertIsNotNone(re.fullmatch(pattern, "files/share/i18n/catalog.json"))
        self.assertIsNone(re.fullmatch(pattern, "../outside.json"))
        self.assertIsNone(re.fullmatch(pattern, "/absolute/catalog.json"))
        self.assertEqual(
            schema["$defs"]["component"]["properties"]["x-msys-i18n"]["$ref"],
            "#/$defs/i18nPresentation",
        )


if __name__ == "__main__":
    unittest.main()
