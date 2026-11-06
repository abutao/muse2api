"""Invite library helpers. The SQLite round-trip uses the local app database and deletes its rows."""

import unittest

from app.db import get_db, init_db
from app.signup.invites import (
    burn,
    claim,
    classify_redeem_error,
    csrf_from_cookies,
    parse_invite_payload,
    release,
    remember,
    slots,
)


class InviteParseTests(unittest.TestCase):
    def test_csrf_header_is_the_cookie_prefix(self):
        token = "a" * 43
        cookie = token + "." + ("b" * 43)
        self.assertEqual(csrf_from_cookies({"hatch_native_auth_csrf": cookie}), token)
        self.assertEqual(csrf_from_cookies({}), "")

    def test_parse_create_payload(self):
        parsed = parse_invite_payload(
            {"code": "c9o594", "max_uses": 30, "use_count": 0}
        )
        self.assertEqual(parsed["code"], "C9O594")
        self.assertEqual(parsed["uses_remaining"], 30)

    def test_parse_missing_code(self):
        self.assertIsNone(parse_invite_payload({"has_redeemed_invite_code": False, "code": None}))

    def test_classify(self):
        self.assertEqual(classify_redeem_error(400, "invalid_invite", {}), "dead")
        self.assertEqual(classify_redeem_error(200, "already redeemed", {}), "already")
        self.assertEqual(classify_redeem_error(0, "transport_error:ReadTimeout", {}), "retry")


class InviteStoreTests(unittest.TestCase):
    def setUp(self):
        init_db()
        self.codes = ["ZZTEST1", "ZZTEST2"]

    def tearDown(self):
        with get_db() as conn:
            conn.execute(
                "DELETE FROM invite_codes WHERE code IN ('ZZTEST1','ZZTEST2')"
            )

    def test_claim_skips_own_code_and_releases(self):
        remember(
            {"code": "ZZTEST1", "max_uses": 30, "use_count": 0, "uses_remaining": 100000},
            owner_email="owner@example.test",
            owner_ref="owner-1",
        )
        remember(
            {"code": "ZZTEST2", "max_uses": 30, "use_count": 0, "uses_remaining": 99999},
            owner_email="other@example.test",
            owner_ref="owner-2",
        )
        own = claim("owner@example.test")
        self.assertEqual(own, "ZZTEST2")
        release(own)
        self.assertGreaterEqual(slots("owner@example.test"), 99999)
        burn("ZZTEST2")
        again = claim("stranger@example.test")
        self.assertEqual(again, "ZZTEST1")
        release(again)


if __name__ == "__main__":
    unittest.main()
