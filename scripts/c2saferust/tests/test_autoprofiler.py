#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0

import sys
from pathlib import Path
import unittest


REPO_ROOT = Path(__file__).resolve().parents[3]
TOOL_DIR = REPO_ROOT / "scripts" / "c2saferust"
if str(TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(TOOL_DIR))

import autoprofiler  # noqa: E402
import profiles  # noqa: E402


class AutoProfilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.kernel_tree = REPO_ROOT.parent / "linux"

    def _require_kernel_source(self, relative_path: str) -> None:
        source_path = self.kernel_tree / relative_path
        if not source_path.exists():
            raise unittest.SkipTest(f"Kernel source fixture is missing: {source_path}")

    def test_family_registry_loads_signature_structs(self):
        registry = profiles.load_family_registry()
        signature_to_families = registry["signature_to_families"]

        self.assertIn("net-link-type", registry["families_by_id"])
        self.assertEqual(signature_to_families["rtnl_link_ops"], ["net-link-type"])
        self.assertEqual(signature_to_families["phy_driver"], ["net-phy"])
        self.assertIn("pci-miscdevice", signature_to_families["pci_driver"])
        self.assertIn("block-null", signature_to_families["blk_mq_ops"])

    def test_autoprofiler_detects_dummy_known_family(self):
        self._require_kernel_source("drivers/net/dummy.c")
        payload = autoprofiler.discover_auto_profile(
            "drivers/net/dummy.c",
            repo_root=self.kernel_tree,
        )

        self.assertEqual(payload["selected_family_id"], "net-link-type")
        self.assertEqual(payload["selection_mode"], "known-family")
        report = payload["report"]
        self.assertIn("dummy_init_module", report["entry_functions"])
        self.assertTrue(
            {"rtnl_link_ops", "net_device_ops"}.issubset(
                {entry["type_name"] for entry in report["candidate_tables"]}
            )
        )
        required_fields = {entry["field"] for entry in report["required_callback_fields"]}
        self.assertTrue({"setup", "validate", "ndo_start_xmit"}.issubset(required_fields))

    def test_autoprofiler_falls_back_on_softdog(self):
        self._require_kernel_source("drivers/watchdog/softdog.c")
        payload = autoprofiler.discover_auto_profile(
            "drivers/watchdog/softdog.c",
            repo_root=self.kernel_tree,
        )

        self.assertEqual(payload["selected_family_id"], "generic-unknown")
        self.assertEqual(payload["selection_mode"], "generic-unknown")
        report = payload["report"]
        self.assertIn("softdog_init", report["entry_functions"])
        self.assertIn("softdog_ops", {entry["name"] for entry in report["candidate_tables"]})
        self.assertTrue(
            {"start", "stop"}.issubset({entry["field"] for entry in report["required_callback_fields"]})
        )


if __name__ == "__main__":
    unittest.main()
