import os
import unittest

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
            "http://example.invalid/p/PhotoBackup/test.NEF?sign=x", 403
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


if __name__ == "__main__":
    unittest.main()
