import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.contract_tool import (  # noqa: E402
    ContractFormatError,
    discover_contracts,
    load_json,
    run_conformance,
    validate_contract,
    validate_instance,
    validate_manifest_claims,
)


class RoleContractCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        loaded, errors = discover_contracts([ROOT / "contracts" / "roles"])
        if errors:
            raise AssertionError("\n".join(errors))
        cls.loaded = loaded
        cls.contracts = {document["role"]: document for _, document in loaded}

    def test_all_baseline_roles_are_machine_valid(self):
        self.assertEqual(
            set(self.contracts),
            {"launcher", "window-manager", "navigation-bar", "display-output", "hal-manager"},
        )
        for path, document in self.loaded:
            with self.subTest(path=path):
                self.assertEqual(validate_contract(document, str(path)), [])

    def test_major_id_version_target_and_method_names_are_stable(self):
        for role, document in self.contracts.items():
            with self.subTest(role=role):
                self.assertTrue(document["id"].endswith(".v1"))
                self.assertTrue(document["version"].startswith("1."))
                self.assertEqual(document["target"], "role:" + role)
                names = [method["name"] for method in document["methods"]]
                self.assertEqual(len(names), len(set(names)))

    def test_lifecycle_only_display_contract_has_no_fake_rpc(self):
        display = self.contracts["display-output"]
        self.assertEqual(display["channel"], "supervisor-lifecycle")
        self.assertEqual(display["methods"], [])
        self.assertFalse(display["provider"]["requires_control_channel"])
        self.assertEqual(display["artifacts"][0]["transport"], "readiness-document")

    def test_navigation_contract_delegates_policy(self):
        navigation = self.contracts["navigation-bar"]
        self.assertEqual(navigation["methods"], [])
        dependency = next(item for item in navigation["dependencies"] if item["target"] == "role:window-manager")
        self.assertIn("navigation_action", dependency["methods"])

    def test_return_conformance_inputs_match_request_schema(self):
        for role, contract in self.contracts.items():
            for method in contract["methods"]:
                for case in method["conformance"]:
                    if case["expect"]["type"] != "return":
                        continue
                    with self.subTest(role=role, method=method["name"], case=case["id"]):
                        self.assertEqual(
                            validate_instance(case["request"], method["request"], contract),
                            [],
                        )

    def test_display_session_example_shape(self):
        contract = self.contracts["display-output"]
        sample = {
            "schema": "msys.display-session.v1",
            "state": "ready",
            "provider": "org.example.output:main",
            "generation": 3,
            "display": ":24",
            "geometry": {"width": 320, "height": 480, "depth": 24},
            "input_transform": {
                "enabled": True,
                "mode": "ch347-direct",
                "device": "CH347 XPT2046",
                "space": "normalized-display",
                "matrix": [1, 0, 0, 0, 1, 0, 0, 0, 1],
                "source": "ch347-direct-effective",
                "verified": True,
            },
            "observed_at_unix_ms": 100,
        }
        self.assertEqual(validate_instance(sample, {"$ref": "#/types/display-session"}, contract), [])
        broken = copy.deepcopy(sample)
        broken["display"] = "inherit"
        self.assertTrue(validate_instance(broken, {"$ref": "#/types/display-session"}, contract))

    def test_boolean_is_not_accepted_as_integer(self):
        contract = self.contracts["launcher"]
        errors = validate_instance(True, {"type": "integer"}, contract)
        self.assertTrue(errors)

    def test_bad_descriptor_reports_multiple_semantic_errors(self):
        document = copy.deepcopy(self.contracts["launcher"])
        document["target"] = "role:fixed-launcher"
        document["version"] = "2.0.0"
        document["methods"][0]["errors"] = ["NOT_DEFINED"]
        errors = validate_contract(document, "bad.json")
        self.assertTrue(any("target" in item for item in errors))
        self.assertTrue(any("major" in item for item in errors))
        self.assertTrue(any("undefined error" in item for item in errors))

    def test_invalid_numeric_schema_is_reported_not_crashed(self):
        document = copy.deepcopy(self.contracts["launcher"])
        document["types"]["broken"] = {"type": "integer", "minimum": "low", "maximum": 3}
        errors = validate_contract(document, "bad-number.json")
        self.assertTrue(any("finite number" in item for item in errors))

    def test_duplicate_json_members_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "duplicate.json"
            path.write_text('{"schema":"a","schema":"b"}', encoding="utf-8")
            with self.assertRaises(ContractFormatError):
                load_json(path)


class ProviderClaimTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        loaded, errors = discover_contracts([ROOT / "contracts" / "roles"])
        if errors:
            raise AssertionError("\n".join(errors))
        cls.contracts = [document for _, document in loaded]
        cls.manifest = load_json(ROOT / "examples" / "providers" / "role-provider-manifest.json")

    def test_example_manifest_claims_all_contracts(self):
        self.assertEqual(
            validate_manifest_claims(self.manifest, self.contracts, "example.json"),
            [],
        )

    def test_known_role_requires_explicit_version_claim(self):
        manifest = copy.deepcopy(self.manifest)
        del manifest["components"][0]["provides"][0]["x-msys-contract"]
        errors = validate_manifest_claims(manifest, self.contracts, "missing.json")
        self.assertTrue(any("missing x-msys-contract" in item for item in errors))
        self.assertEqual(
            validate_manifest_claims(
                manifest,
                self.contracts,
                "migration.json",
                allow_unversioned=True,
            ),
            [],
        )

    def test_wrong_contract_revision_is_rejected(self):
        manifest = copy.deepcopy(self.manifest)
        claim = manifest["components"][0]["provides"][0]["x-msys-contract"]
        claim["version"] = "1.1.0"
        errors = validate_manifest_claims(manifest, self.contracts, "wrong-version.json")
        self.assertTrue(any("unknown contract revision" in item for item in errors))

    def test_role_and_exclusivity_are_checked(self):
        manifest = copy.deepcopy(self.manifest)
        provide = manifest["components"][0]["provides"][0]
        provide["role"] = "navigation-bar"
        provide["exclusive"] = False
        errors = validate_manifest_claims(manifest, self.contracts, "wrong-role.json")
        self.assertTrue(any("claim is for role" in item for item in errors))
        self.assertTrue(any("exclusive" in item for item in errors))

    def test_readiness_lifecycle_windowing_interface_and_capability_are_checked(self):
        cases = []
        readiness = copy.deepcopy(self.manifest)
        readiness["components"][0]["readiness"]["mode"] = "exec"
        cases.append((readiness, "readiness.mode"))
        lifecycle = copy.deepcopy(self.manifest)
        lifecycle["components"][1]["lifecycle"] = "manual"
        cases.append((lifecycle, "lifecycle"))
        windowing = copy.deepcopy(self.manifest)
        windowing["components"][3]["windowing"]["mode"] = "background"
        cases.append((windowing, "windowing.mode"))
        interface = copy.deepcopy(self.manifest)
        interface["components"][4]["provides"] = interface["components"][4]["provides"][:1]
        cases.append((interface, "required interface"))
        capability = copy.deepcopy(self.manifest)
        capability["components"][3]["provides"] = [
            item for item in capability["components"][3]["provides"] if item.get("capability") != "display.session.v1"
        ]
        cases.append((capability, "required capability"))
        for manifest, needle in cases:
            with self.subTest(needle=needle):
                errors = validate_manifest_claims(manifest, self.contracts, "bad-provider.json")
                self.assertTrue(any(needle in item for item in errors), errors)

    def test_one_component_can_claim_strict_role_specific_surfaces(self):
        manifest = copy.deepcopy(self.manifest)
        launcher = manifest["components"][0]
        navigation = manifest["components"][2]
        launcher["provides"].append(copy.deepcopy(navigation["provides"][0]))
        launcher["x-msys-role-windows"] = {
            "launcher": copy.deepcopy(launcher["windowing"]),
            "navigation-bar": copy.deepcopy(navigation["windowing"]),
        }
        del manifest["components"][2]
        self.assertEqual(validate_manifest_claims(manifest, self.contracts, "multi.json"), [])

        bad_field = copy.deepcopy(manifest)
        bad_field["components"][0]["x-msys-role-windows"]["launcher"]["guess"] = True
        self.assertTrue(any(
            "unknown field" in item
            for item in validate_manifest_claims(bad_field, self.contracts, "bad-field.json")
        ))

        unknown = copy.deepcopy(manifest)
        unknown["components"][0]["x-msys-role-windows"]["task-switcher"] = copy.deepcopy(
            navigation["windowing"]
        )
        errors = validate_manifest_claims(unknown, self.contracts, "unknown-role.json")
        self.assertTrue(any("unknown versioned role" in item for item in errors))
        self.assertTrue(any("no corresponding component claim" in item for item in errors))

        conflict = copy.deepcopy(manifest)
        conflict["components"][0]["windowing"]["title"] = "Different legacy surface"
        self.assertTrue(any(
            "conflicts with x-msys-role-windows" in item
            for item in validate_manifest_claims(conflict, self.contracts, "conflict.json")
        ))


class BehavioralRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = load_json(ROOT / "contracts" / "roles" / "launcher.v1.json")

    def test_reference_adapter_passes_safe_suite(self):
        executed, skipped, errors = run_conformance(
            self.contract,
            [sys.executable, str(ROOT / "examples" / "conformance" / "launcher_adapter.py")],
        )
        self.assertEqual((executed, skipped), (1, 2))
        self.assertEqual(errors, [])

    def test_reference_adapter_passes_mutating_suite(self):
        executed, skipped, errors = run_conformance(
            self.contract,
            [sys.executable, str(ROOT / "examples" / "conformance" / "launcher_adapter.py")],
            include_mutating=True,
        )
        self.assertEqual((executed, skipped), (3, 0))
        self.assertEqual(errors, [])

    def test_bad_adapter_response_is_rejected(self):
        executed, skipped, errors = run_conformance(
            self.contract,
            [sys.executable, str(ROOT / "tests" / "fixtures" / "bad_launcher_adapter.py")],
        )
        self.assertEqual((executed, skipped), (1, 2))
        self.assertTrue(errors)
        self.assertTrue(any("const" in item or "minimum" in item or "required" in item for item in errors))

    def test_missing_adapter_is_a_typed_failure(self):
        executed, skipped, errors = run_conformance(
            self.contract,
            [str(ROOT / "does-not-exist")],
        )
        self.assertEqual((executed, skipped), (0, 0))
        self.assertTrue(any("adapter start" in item for item in errors))

    def test_cli_end_to_end(self):
        commands = [
            [sys.executable, "-m", "tools.contract_tool", "validate"],
            [
                sys.executable,
                "-m",
                "tools.contract_tool",
                "manifest",
                "examples/providers/role-provider-manifest.json",
            ],
            [
                sys.executable,
                "-m",
                "tools.contract_tool",
                "run",
                "contracts/roles/launcher.v1.json",
                "--",
                sys.executable,
                "examples/conformance/launcher_adapter.py",
            ],
        ]
        for command in commands:
            with self.subTest(command=command):
                result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=15)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class MetaSchemaTests(unittest.TestCase):
    def test_meta_schema_is_strict_json(self):
        document = load_json(ROOT / "schemas" / "role-contract.v1.schema.json")
        self.assertEqual(document["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(document["properties"]["schema"]["const"], "msys.role-contract.v1")


if __name__ == "__main__":
    unittest.main()
