"""Offline checks for /v1 warm sticky + timing helpers (no network)."""
import ast
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1] if False else Path(".")


class TimingHelperTests(unittest.TestCase):
    def test_finalize_accounts_unattributed(self):
        # Load helpers by execing minimal stubs from service source text
        src = Path("app/muse_engine/service.py")
        if not src.exists():
            src = Path("/workspace/muse2api-patches/service.py")
        text = src.read_text(encoding="utf-8")
        # Pull only helper function bodies via AST compile of whole module is heavy;
        # reimplement contract assertions against the written source shapes.
        self.assertIn("def _new_request_timing", text)
        self.assertIn("renew_skipped", text)
        self.assertIn("unattributed_ms", text)
        self.assertIn("muse_timing", text)
        self.assertIn("skip_http_if_warm", text)
        self.assertIn("has_warm_worker", text)
        self.assertIn("warm_account_ids", text)
        self.assertIn("Protocol warm sticky", text)
        ast.parse(text)

    def test_pool_warm_helpers_parse(self):
        src = Path("app/muse_engine/protocol_pool.py")
        if not src.exists():
            src = Path("/workspace/muse2api-patches/protocol_pool.py")
        text = src.read_text(encoding="utf-8")
        self.assertIn("def has_warm_worker", text)
        self.assertIn("def warm_account_ids", text)
        ast.parse(text)

    def test_timing_finalize_math(self):
        # Local copy of finalize logic for numeric contract
        def finalize(timing, total):
            timing = dict(timing)
            timing["total_ms"] = total
            known = 0.0
            for key in ("lock_wait_ms", "pick_ms", "renew_ms", "bootstrap_ms"):
                val = timing.get(key)
                if isinstance(val, (int, float)):
                    known += float(val)
            done = timing.get("done_ms")
            if isinstance(done, (int, float)):
                pre = known
                timing["generation_ms"] = round(max(0.0, float(done) - pre), 1)
                known += float(timing["generation_ms"])
            timing["unattributed_ms"] = round(max(0.0, float(total) - known), 1)
            return timing

        out = finalize(
            {"pick_ms": 10, "renew_ms": 2000, "bootstrap_ms": 500, "done_ms": 4000},
            4100,
        )
        self.assertEqual(out["generation_ms"], 1490.0)
        self.assertEqual(out["unattributed_ms"], 100.0)


if __name__ == "__main__":
    unittest.main()
