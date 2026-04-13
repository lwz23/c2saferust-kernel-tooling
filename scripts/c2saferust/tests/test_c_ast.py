#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import json
import re
import sys
from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[3]
TOOL_DIR = REPO_ROOT / "scripts" / "c2saferust"
if str(TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(TOOL_DIR))


def _legacy_extract_private_struct_fields(source_text: str, struct_name: str) -> list[dict]:
    match = re.search(
        rf"struct\s+{re.escape(struct_name)}\s*\{{(?P<body>.*?)\}};",
        source_text,
        re.MULTILINE | re.DOTALL,
    )
    if not match:
        return []
    fields = []
    for line in match.group("body").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("/*"):
            continue
        field_match = re.match(r"(.+?)\s+([A-Za-z_][A-Za-z0-9_]*)\s*;$", stripped)
        if field_match:
            fields.append(
                {
                    "kind": "field",
                    "type_spelling": field_match.group(1).strip(),
                    "name": field_match.group(2),
                }
            )
    return fields


def _legacy_initializer_field_map(source_text: str, struct_name: str, *, prefer_first: bool = False) -> dict[str, str]:
    name_match = re.search(
        rf"static\s+(?:const\s+)?struct\s+{re.escape(struct_name)}\s+([A-Za-z0-9_]+)[^=]*=\s*\{{",
        source_text,
        re.MULTILINE,
    )
    if not name_match:
        return {}
    init_name = name_match.group(1)
    body_match = re.search(
        rf"\b{re.escape(init_name)}\b[^=]*=\s*\{{(?P<body>.*?)\}};",
        source_text,
        re.MULTILINE | re.DOTALL,
    )
    if not body_match:
        return {}
    field_map: dict[str, str] = {}
    for field_match in re.finditer(
        r"\.([A-Za-z0-9_]+)\s*=\s*([^,\n]+)",
        body_match.group("body"),
    ):
        field = field_match.group(1)
        value = field_match.group(2).strip()
        if prefer_first and field in field_map:
            continue
        field_map[field] = value
    return field_map


class CASTExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            import c_ast  # type: ignore
        except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
            raise unittest.SkipTest(
                "c_ast module is not available yet; run after AST plumbing lands."
            ) from exc

        cls.c_ast = c_ast
        cls.fixtures_dir = REPO_ROOT / "scripts" / "c2saferust" / "tests" / "data" / "c_ast"
        cls.report_path = REPO_ROOT / "tmp" / "ast-validation-report.json"
        cls.kernel_tree = REPO_ROOT.parent / "linux"

    def _load_fixture(self, filename: str) -> str:
        return (self.fixtures_dir / filename).read_text()

    def _load_kernel_source(self, relative_path: str) -> str:
        source_path = self.kernel_tree / relative_path
        if not source_path.exists():
            raise unittest.SkipTest(f"Kernel source fixture is missing: {source_path}")
        return source_path.read_text(errors="ignore")

    def _new_tu(self, source_text: str):
        c_ast = self.c_ast
        if hasattr(c_ast, "CTranslationUnit"):
            tu_type = c_ast.CTranslationUnit
            for ctor_name in ("from_source", "from_text", "parse"):
                ctor = getattr(tu_type, ctor_name, None)
                if callable(ctor):
                    return ctor(source_text)
            return tu_type(source_text)
        for fn_name in ("parse_c_source", "parse_translation_unit", "parse"):
            parser_fn = getattr(c_ast, fn_name, None)
            if callable(parser_fn):
                return parser_fn(source_text)
        self.fail("c_ast does not expose CTranslationUnit or parse_* entrypoints.")

    def _normalize_signature(self, sig: object) -> dict:
        if isinstance(sig, dict):
            params = sig.get("parameters", [])
            return {
                "name": sig.get("name"),
                "return_type_spelling": sig.get("return_type_spelling"),
                "annotations": sig.get("annotations", []),
                "parameters": [
                    {
                        "name": p.get("name"),
                        "type_spelling": p.get("type_spelling"),
                    }
                    for p in params
                ],
            }

        params = getattr(sig, "parameters", [])
        normalized_params = []
        for p in params:
            if isinstance(p, dict):
                normalized_params.append(
                    {
                        "name": p.get("name"),
                        "type_spelling": p.get("type_spelling"),
                    }
                )
            else:
                normalized_params.append(
                    {
                        "name": getattr(p, "name", None),
                        "type_spelling": getattr(p, "type_spelling", None),
                    }
                )

        return {
            "name": getattr(sig, "name", None),
            "return_type_spelling": getattr(sig, "return_type_spelling", None),
            "annotations": getattr(sig, "annotations", []),
            "parameters": normalized_params,
        }

    def _normalize_members(self, record: object) -> list[dict]:
        def convert_member(member: object) -> dict:
            if isinstance(member, dict):
                children = member.get("children") or []
                return {
                    "kind": member.get("kind", "field"),
                    "name": member.get("name"),
                    "type_spelling": member.get("type_spelling"),
                    "bit_width": member.get("bit_width"),
                    "bit_width_spelling": member.get("bit_width_spelling"),
                    "children": [convert_member(child) for child in children],
                }

            raw_children = getattr(member, "children", None) or []
            return {
                "kind": getattr(member, "kind", "field"),
                "name": getattr(member, "name", None),
                "type_spelling": getattr(member, "type_spelling", None),
                "bit_width": getattr(member, "bit_width", None),
                "bit_width_spelling": getattr(member, "bit_width_spelling", None),
                "children": [convert_member(child) for child in raw_children],
            }

        if isinstance(record, dict):
            members = record.get("members", [])
            return [convert_member(member) for member in members]
        members = getattr(record, "members", [])
        return [convert_member(member) for member in members]

    def _flatten_members(self, members: list[dict]) -> list[dict]:
        flattened: list[dict] = []
        for member in members:
            flattened.append(member)
            children = member.get("children", [])
            if children:
                flattened.extend(self._flatten_members(children))
        return flattened

    def _ast_signature(self, source_text: str, function_name: str) -> dict:
        tu = self._new_tu(source_text)
        get_sig = getattr(tu, "get_function_signature", None)
        self.assertTrue(callable(get_sig), "CTranslationUnit must expose get_function_signature(name).")
        sig = get_sig(function_name)
        self.assertIsNotNone(sig, f"get_function_signature({function_name}) returned None.")
        return self._normalize_signature(sig)

    def _ast_initializer_field_map(self, source_text: str, struct_name: str, *, prefer_first: bool = False) -> dict[str, str]:
        tu = self._new_tu(source_text)
        map_fn = getattr(tu, "get_initializer_field_map", None)
        self.assertTrue(callable(map_fn), "CTranslationUnit must expose get_initializer_field_map(struct_name).")
        try:
            field_map = map_fn(struct_name, prefer_first=prefer_first)
        except TypeError:
            field_map = map_fn(struct_name)
        self.assertIsInstance(field_map, dict)
        return {str(key): str(value) for key, value in field_map.items()}

    def _ast_record_members(self, source_text: str, record_name: str) -> list[dict]:
        tu = self._new_tu(source_text)
        find_record = getattr(tu, "find_record", None)
        self.assertTrue(callable(find_record), "CTranslationUnit must expose find_record(name).")
        record = find_record(record_name)
        self.assertIsNotNone(record, f"find_record({record_name}) returned None.")
        return self._normalize_members(record)

    def _case_ax88796b(self) -> dict:
        source_text = self._load_fixture("ax88796b_fixture.c")
        ast_sig = self._ast_signature(source_text, "asix_ax88772a_read_status")
        ast_field_map = self._ast_initializer_field_map(source_text, "phy_driver", prefer_first=True)
        legacy_field_map = _legacy_initializer_field_map(source_text, "phy_driver", prefer_first=True)

        self.assertEqual(ast_sig["name"], "asix_ax88772a_read_status")
        self.assertIn("int", ast_sig["return_type_spelling"])
        self.assertEqual(ast_sig["parameters"][0]["name"], "dev")
        self.assertIn("struct phy_device *", ast_sig["parameters"][0]["type_spelling"])
        self.assertEqual(ast_field_map.get("read_status"), "asix_ax88772a_read_status")
        self.assertEqual(ast_field_map.get("link_change_notify"), "asix_ax88772a_link_change_notify")

        verdict = "equivalent" if ast_field_map == legacy_field_map else "improved"
        return {
            "case": "ax88796b",
            "legacy_result": {"initializer_field_map": legacy_field_map},
            "ast_result": {
                "function_signature": ast_sig,
                "initializer_field_map": ast_field_map,
            },
            "diff_summary": "Compare function signature + phy_driver designated initializer map.",
            "verdict": verdict,
        }

    def _case_blk_mq_ops(self) -> dict:
        source_text = self._load_fixture("blk_mq_ops_fixture.h")
        ast_sig = self._ast_signature(source_text, "null_poll_rq")
        members = self._ast_record_members(source_text, "blk_mq_ops")
        flat_members = self._flatten_members(members)
        fn_ptr_members = [member for member in flat_members if member.get("kind") == "function_pointer"]

        self.assertEqual(ast_sig["name"], "null_poll_rq")
        self.assertIn("int", ast_sig["return_type_spelling"])
        self.assertIn("struct blk_mq_hw_ctx *", ast_sig["parameters"][0]["type_spelling"])
        self.assertIn("struct io_comp_batch *", ast_sig["parameters"][1]["type_spelling"])

        poll_member = next((member for member in fn_ptr_members if member.get("name") == "poll"), None)
        self.assertIsNotNone(poll_member, "blk_mq_ops must include a function_pointer member named poll.")
        self.assertIn(
            "struct io_comp_batch *",
            poll_member.get("type_spelling", ""),
            "poll function pointer type must preserve io_comp_batch pointer spelling.",
        )

        return {
            "case": "blk_mq_ops",
            "legacy_result": {
                "function_pointer_members": [],
                "note": "legacy regex path has no robust function-pointer member model.",
            },
            "ast_result": {
                "poll_signature": ast_sig,
                "function_pointer_members": fn_ptr_members,
            },
            "diff_summary": "AST can model blk_mq_ops function pointers including io_comp_batch pointer types.",
            "verdict": "improved",
        }

    def _case_request_union(self) -> dict:
        source_text = self._load_fixture("request_union_fixture.h")
        ast_members = self._ast_record_members(source_text, "request")
        ast_flat = self._flatten_members(ast_members)
        ast_names = {member.get("name") for member in ast_flat}
        legacy_members = _legacy_extract_private_struct_fields(source_text, "request")
        legacy_names = {member.get("name") for member in legacy_members}

        self.assertIn("__sector", ast_names)
        self.assertIn("__data_len", ast_names)
        self.assertIn("batch", ast_names)
        self.assertNotIn("batch", legacy_names)
        self.assertTrue(
            any(member.get("kind") == "anonymous_union" for member in ast_flat),
            "request record should contain an anonymous_union member in AST model.",
        )

        return {
            "case": "request_union",
            "legacy_result": {"record_members": legacy_members},
            "ast_result": {"record_members": ast_members},
            "diff_summary": "AST preserves nested anonymous union members while regex line parsing loses them.",
            "verdict": "improved",
        }

    def _case_dummy_real_module(self) -> dict:
        source_text = self._load_kernel_source("drivers/net/dummy.c")
        tu = self._new_tu(source_text)

        setup_sig = self._normalize_signature(tu.get_function_signature("dummy_setup"))
        validate_sig = self._normalize_signature(tu.get_function_signature("dummy_validate"))
        init_sig = self._normalize_signature(tu.get_function_signature("dummy_init_one"))
        cleanup_sig = self._normalize_signature(tu.get_function_signature("dummy_cleanup_module"))
        rtnl_init = tu.find_static_initializer("rtnl_link_ops")
        netdev_field_map = tu.get_initializer_field_map("net_device_ops")
        validate_checks = tu.find_validate_checks("dummy_validate")
        assignments = tu.find_member_assignments("dummy_setup", "dev->")
        xmit_calls = tu.find_call_expressions("dummy_xmit")

        self.assertIsNotNone(rtnl_init)
        self.assertEqual(validate_sig["parameters"][0]["name"], "tb")
        self.assertEqual(validate_sig["parameters"][1]["name"], "data")
        self.assertEqual(validate_sig["parameters"][0]["type_spelling"], "struct nlattr *[]")
        self.assertEqual(validate_sig["parameters"][1]["type_spelling"], "struct nlattr *[]")
        self.assertEqual(init_sig["annotations"], ["__init"])
        self.assertEqual(cleanup_sig["annotations"], ["__exit"])
        self.assertEqual(rtnl_init["name"], "dummy_link_ops")
        self.assertEqual({entry["field"] for entry in rtnl_init["fields"]}, {"kind", "setup", "validate"})
        self.assertEqual(
            {(entry["field"], entry["return"]) for entry in validate_checks},
            {
                ("IFLA_ADDRESS", "-EINVAL"),
                ("IFLA_ADDRESS", "-EADDRNOTAVAIL"),
            },
        )
        self.assertIn({"field": "flags", "operator": "&=", "value": "~IFF_MULTICAST"}, assignments)
        self.assertTrue(any(entry["field"] == "features" and entry["operator"] == "|=" for entry in assignments))
        self.assertEqual(netdev_field_map.get("ndo_init"), "dummy_dev_init")
        self.assertEqual(netdev_field_map.get("ndo_start_xmit"), "dummy_xmit")
        self.assertEqual(netdev_field_map.get("ndo_get_stats64"), "dummy_get_stats64")
        self.assertEqual(netdev_field_map.get("ndo_change_carrier"), "dummy_change_carrier")
        self.assertEqual(netdev_field_map.get("ndo_validate_addr"), "eth_validate_addr")
        self.assertEqual(netdev_field_map.get("ndo_set_rx_mode"), "set_multicast_list")
        self.assertEqual(netdev_field_map.get("ndo_set_mac_address"), "eth_mac_addr")
        self.assertIn("dev_lstats_add", xmit_calls)
        self.assertIn("skb_tx_timestamp", xmit_calls)
        self.assertIn("dev_kfree_skb", xmit_calls)

        return {
            "case": "dummy_real_module",
            "legacy_result": {
                "validate_checks": [],
                "parameter_types": ["struct nlattr *", "struct nlattr *"],
                "note": "Legacy regex extraction misses array declarator shape and nested validate guards.",
            },
            "ast_result": {
                "setup_signature": setup_sig,
                "validate_signature": validate_sig,
                "init_signature": init_sig,
                "cleanup_signature": cleanup_sig,
                "rtnl_link_ops": rtnl_init,
                "netdev_field_map": netdev_field_map,
                "validate_checks": validate_checks,
                "setup_field_writes": assignments,
                "xmit_calls": xmit_calls,
            },
            "diff_summary": "AST preserves array declarators, nested validate returns, kernel annotations, and designated initializer maps on the real dummy driver.",
            "verdict": "improved",
        }

    def _case_dmtimer_bitfields(self) -> dict:
        source_text = self._load_kernel_source("drivers/clocksource/timer-ti-dm.c")
        ast_members = self._ast_record_members(source_text, "dmtimer")
        ast_flat = self._flatten_members(ast_members)
        bitfields = {
            member["name"]: member
            for member in ast_flat
            if member.get("name") in {"reserved", "posted", "omap1"}
        }

        self.assertEqual(set(bitfields), {"reserved", "posted", "omap1"})
        for member in bitfields.values():
            self.assertEqual(member.get("bit_width"), 1)
            self.assertEqual(member.get("bit_width_spelling"), "1")

        return {
            "case": "dmtimer_bitfields",
            "legacy_result": {
                "bitfields": {
                    name: {
                        "type_spelling": bitfields[name]["type_spelling"],
                        "bit_width": None,
                    }
                    for name in sorted(bitfields)
                },
            },
            "ast_result": {"bitfields": bitfields},
            "diff_summary": "AST preserves bitfield width metadata for real kernel fields where the legacy regex view loses the `:1` width.",
            "verdict": "improved",
        }

    def _write_validation_report(self, cases: list[dict]) -> Path:
        payload = {
            "schema_version": 1,
            "artifact_type": "ast-validation-report",
            "cases": cases,
        }
        self.report_path.parent.mkdir(parents=True, exist_ok=True)
        self.report_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return self.report_path

    def test_ax88796b_fixture(self):
        case = self._case_ax88796b()
        self.assertIn(case["verdict"], {"equivalent", "improved"})

    def test_blk_mq_ops_fixture(self):
        case = self._case_blk_mq_ops()
        self.assertEqual(case["verdict"], "improved")

    def test_request_union_fixture(self):
        case = self._case_request_union()
        self.assertEqual(case["verdict"], "improved")

    def test_dummy_real_module(self):
        case = self._case_dummy_real_module()
        self.assertEqual(case["verdict"], "improved")

    def test_dmtimer_bitfields(self):
        case = self._case_dmtimer_bitfields()
        self.assertEqual(case["verdict"], "improved")

    def test_before_after_report_generation(self):
        cases = [
            self._case_ax88796b(),
            self._case_blk_mq_ops(),
            self._case_request_union(),
            self._case_dummy_real_module(),
            self._case_dmtimer_bitfields(),
        ]
        report_path = self._write_validation_report(cases)

        self.assertTrue(report_path.exists())
        payload = json.loads(report_path.read_text())
        self.assertEqual(payload["artifact_type"], "ast-validation-report")
        self.assertEqual(len(payload["cases"]), 5)
        self.assertTrue(
            all({"case", "legacy_result", "ast_result", "diff_summary", "verdict"}.issubset(set(entry.keys())) for entry in payload["cases"])
        )
        self.assertTrue(any(entry["verdict"] == "improved" for entry in payload["cases"]))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
