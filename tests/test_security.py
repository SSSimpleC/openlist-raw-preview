import os
import unittest
from unittest.mock import patch

# Keep the tests deterministic and independent from a user's deployment.
os.environ["RAW_PREVIEW_OPENLIST_ORIGINS"] = "http://openlist.example.test:5244"
os.environ["RAW_PREVIEW_SOURCE_MOUNTS"] = '{"Photos":"/sources/Photos"}'

from app.server import PreviewError, parse_openlist_url


class OpenListUrlTests(unittest.TestCase):
    def test_valid_signed_path(self):
        url = (
            "http://openlist.example.test:5244/p/Photos/PICTURES/%E6%88%91/"
            "%E5%A4%A7%E5%AD%A6/test.NEF?sign=sample"
        )
        parsed = parse_openlist_url(url)
        self.assertEqual(parsed[0], url)
        self.assertEqual(parsed[2], "Photos")
        self.assertEqual(parsed[4], "nef")
        self.assertEqual(parsed[3][-1], "test.NEF")

    def assert_rejected(self, url: str, status: int):
        with self.assertRaises(PreviewError) as context:
            parse_openlist_url(url)
        self.assertEqual(context.exception.status, status)

    def test_rejects_missing_signature(self):
        self.assert_rejected(
            "http://openlist.example.test:5244/p/Photos/test.NEF", 403
        )

    def test_rejects_foreign_origin(self):
        self.assert_rejected(
            "http://example.invalid/p/Photos/test.NEF?sign=dummy", 403
        )

    def test_rejects_path_traversal(self):
        self.assert_rejected(
            "http://openlist.example.test:5244/p/Photos/%2E%2E/etc/passwd.NEF?sign=x",
            403,
        )

    def test_rejects_non_raw(self):
        self.assert_rejected(
            "http://openlist.example.test:5244/p/Photos/test.jpg?sign=x", 415
        )

    def test_source_mounts_are_configurable(self):
        from app.server import LOCAL_SOURCES

        self.assertEqual(LOCAL_SOURCES["Photos"].as_posix(), "/sources/Photos")

    def test_rewrite_keeps_signed_path_and_query(self):
        from app.server import fetch_url_for, parse_fetch_origin

        source = "http://openlist.example.test:5244/p/Photos/a.NEF?sign=a%2Bb"
        self.assertEqual(
            parse_fetch_origin("HTTPS://OpenList.Internal:5244/"),
            "https://openlist.internal:5244",
        )
        with patch("app.server.OPENLIST_FETCH_ORIGIN", "http://openlist.internal:5244"):
            self.assertEqual(
                fetch_url_for(source),
                "http://openlist.internal:5244/p/Photos/a.NEF?sign=a%2Bb",
            )

    def test_rejects_unsafe_fetch_origins(self):
        from app.server import parse_fetch_origin

        for value in (
            "file:///tmp/openlist",
            "http://user:pass@openlist.internal:5244",
            "http://openlist.internal:5244/path",
            "http://openlist.internal:5244?query=1",
        ):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                parse_fetch_origin(value)


if __name__ == "__main__":
    unittest.main()
