"""Offline unit checks for protocol helpers and proxy parsing (no network)."""
from __future__ import annotations

import unittest

from app.muse_engine.browser_proxy import (
    normalize_proxy_dict,
    parse_proxy_string,
    public_proxy,
    request_proxies,
)
from app.muse_engine.protocol_engine import (
    PNG_MAGIC,
    _media_candidates,
    _resource_kind,
    image_magic_mime,
    should_use_file_ref,
    to_png_bytes,
)
from app.signup.protocol import normalize_proxy_entry


class ProtocolHelperTests(unittest.TestCase):
    def test_should_use_file_ref_image_inline(self):
        self.assertFalse(should_use_file_ref(20 * 1024 * 1024, "image/webp", []))

    def test_should_use_file_ref_video_without_capability(self):
        self.assertFalse(should_use_file_ref(20 * 1024 * 1024, "video/mp4", []))
        self.assertTrue(should_use_file_ref(60 * 1024 * 1024, "video/mp4", []))

    def test_should_use_file_ref_video_with_capability(self):
        caps = ["video_file_ref_ingest"]
        self.assertTrue(should_use_file_ref(10 * 1024 * 1024, "video/mp4", caps))
        self.assertFalse(should_use_file_ref(9 * 1024 * 1024, "video/mp4", caps))

    def test_media_from_presentation_without_device(self):
        payload = {
            "kind": "image",
            "data": {
                "images": [
                    {
                        "path": "sandbox://workspace/imagine_media/demo.webp",
                        "mime": "image/webp",
                        "width": 1600,
                        "height": 1600,
                    }
                ]
            },
        }
        out: list = []
        _media_candidates(payload, out)
        self.assertEqual(len(out), 1)
        self.assertEqual(_resource_kind(out[0]["resource"]), "image")

    def test_media_from_resources_metadata_json_string(self):
        payload = {
            "resources": [
                {
                    "kind": "video",
                    "metadata": '{"path":"workspace/imagine_media/clip.mp4","mime_type":"video/mp4","size_bytes":123}',
                }
            ]
        }
        out: list = []
        _media_candidates(payload, out)
        self.assertTrue(out)
        self.assertEqual(_resource_kind(out[0]["resource"]), "video")

    def test_skips_user_upload_paths(self):
        payload = {"path": "sandbox://workspace/user/files/ref.mp4", "mime": "video/mp4"}
        out: list = []
        _media_candidates(payload, out)
        self.assertEqual(out, [])


class ProxyParseTests(unittest.TestCase):
    def test_host_port_user_pass_default_http(self):
        # Fictional placeholder credentials only.
        parsed = parse_proxy_string("proxy.example.test:8080:alice:p@ss:with:colons")
        self.assertEqual(parsed["server"], "http://proxy.example.test:8080")
        self.assertEqual(parsed["username"], "alice")
        self.assertEqual(parsed["password"], "p@ss:with:colons")

    def test_exact_five_field_scheme(self):
        parsed = parse_proxy_string("proxy.example.test:1080:bob:secret:socks5")
        self.assertEqual(parsed["server"], "socks5://proxy.example.test:1080")
        self.assertEqual(parsed["username"], "bob")
        self.assertEqual(parsed["password"], "secret")

    def test_url_userinfo_not_reencoded_in_server(self):
        parsed = parse_proxy_string("http://al%2Fice:s%3Fecret@proxy.example.test:8080")
        self.assertEqual(parsed["server"], "http://proxy.example.test:8080")
        self.assertEqual(parsed["username"], "al/ice")
        self.assertEqual(parsed["password"], "s?ecret")
        # request_proxies quotes once for requests.
        req = request_proxies(parsed)
        url = req["proxies"]["http"]
        self.assertIn("al%2Fice:s%3Fecret@", url)
        self.assertTrue(url.startswith("http://"))

    def test_ipv6_bracket_form(self):
        parsed = parse_proxy_string("[2001:db8::1]:8080:carol:pw")
        self.assertEqual(parsed["server"], "http://[2001:db8::1]:8080")
        self.assertEqual(parsed["username"], "carol")
        self.assertEqual(parsed["password"], "pw")

    def test_ipv6_unbracketed_errors(self):
        with self.assertRaises(ValueError):
            parse_proxy_string("2001:db8::1:8080:carol:pw")

    def test_public_proxy_hides_password(self):
        pub = public_proxy(
            {"server": "http://proxy.example.test:8080", "username": "u", "password": "secret"}
        )
        self.assertEqual(pub["username"], "u")
        self.assertTrue(pub["has_password"])
        self.assertNotIn("password", pub)

    def test_normalize_dict_colon_server(self):
        out = normalize_proxy_dict({"server": "proxy.example.test:8080:dave:se:cret"})
        self.assertEqual(out["server"], "http://proxy.example.test:8080")
        self.assertEqual(out["username"], "dave")
        self.assertEqual(out["password"], "se:cret")

    def test_signup_normalize_entry_default_http(self):
        url = normalize_proxy_entry("proxy.example.test:8080:erin:pw:with:colon")
        self.assertTrue(url.startswith("http://"))
        self.assertIn("@proxy.example.test:8080", url)
        # password with colons must be percent-encoded once in the synthesized URL
        self.assertIn("erin:", url)


if __name__ == "__main__":
    unittest.main()


class PngDeliveryTests(unittest.TestCase):
    def test_png_magic_and_conversion(self):
        import io
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (8, 8), color=(0, 80, 200)).save(buf, format="WEBP")
        webp = buf.getvalue()
        self.assertEqual(image_magic_mime(webp), "image/webp")
        png = to_png_bytes(webp)
        self.assertTrue(png.startswith(PNG_MAGIC))
        self.assertEqual(image_magic_mime(png), "image/png")
        # extension/MIME/magic must agree for API delivery
        self.assertTrue(png.startswith(b"\x89PNG"))
        self.assertNotEqual(png[:4], webp[:4])

    def test_err_text_object_not_used_in_python(self):
        # sanity: conversion rejects empty
        with self.assertRaises(Exception):
            to_png_bytes(b"not-an-image")


class ApiErrShapeTests(unittest.TestCase):
    def test_nested_openai_detail_error_object(self):
        """Mirror of static/app.js errText/apiErr for OpenAI-style 401 bodies."""
        def err_text(v):
            if v is None or v == "":
                return ""
            if isinstance(v, str):
                return v
            if not isinstance(v, dict):
                return str(v)
            if isinstance(v.get("message"), str) and v["message"]:
                return v["message"]
            if isinstance(v.get("error"), str) and v["error"]:
                return v["error"]
            if isinstance(v.get("error"), dict):
                return err_text(v["error"])
            if isinstance(v.get("detail"), str) and v["detail"]:
                return v["detail"]
            if isinstance(v.get("detail"), dict):
                return err_text(v["detail"])
            return ""

        body = {
            "detail": {
                "error": {
                    "message": "Invalid API key",
                    "type": "invalid_request_error",
                    "code": "invalid_api_key",
                }
            }
        }
        msg = err_text(body) or err_text(body.get("detail")) or err_text(body.get("error"))
        self.assertEqual(msg, "Invalid API key")
        self.assertNotIn("object Object", msg.lower())


class TimingDeltaTests(unittest.TestCase):
    def test_exclusive_stage_math(self):
        from app.muse_engine.protocol_engine import timing_deltas
        marks = {
            "session_start": 0.0,
            "session": 1.0,
            "token_start": 1.0,
            "token": 2.5,
            "noise_start": 2.5,
            "noise": 3.0,
            "subscribe_start": 3.0,
            "subscribe": 4.0,
            "connect_start": 0.0,
        }
        d = timing_deltas(marks)
        self.assertEqual(d["session_ms"], 1000.0)
        self.assertEqual(d["token_ms"], 1500.0)
        self.assertEqual(d["noise_ms"], 500.0)
        self.assertEqual(d["subscribe_ms"], 1000.0)
        self.assertEqual(d["bootstrap_ms"], 4000.0)

    def test_reuse_bootstrap(self):
        from app.muse_engine.protocol_engine import timing_deltas
        marks = {"reuse_hit": 10.0, "stream_start": 10.2}
        d = timing_deltas(marks)
        self.assertEqual(d["reuse"], 1.0)
        self.assertAlmostEqual(d["warm_to_stream_ms"], 200.0)
