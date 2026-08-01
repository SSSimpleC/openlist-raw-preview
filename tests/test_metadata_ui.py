import os
import unittest

os.environ["RAW_PREVIEW_OPENLIST_ORIGINS"] = "http://openlist.example.test:5244"
os.environ["RAW_PREVIEW_SOURCE_MOUNTS"] = '{"Photos":"/sources/Photos"}'

from app.server import html_page


class MetadataUiTests(unittest.TestCase):
    def test_view_contains_safe_metadata_panel(self):
        source = "http://openlist.example.test:5244/p/Photos/test.NEF?sign=sample"
        body = html_page(source).decode("utf-8")
        self.assertIn("照片信息", body)
        self.assertIn("metadata?src=", body)
        self.assertNotIn('src="/image?src=', body)
        self.assertIn("fetch(metadataUrl,", body)
        self.assertIn("textContent", body)
        self.assertNotIn("innerHTML", body)


if __name__ == "__main__":
    unittest.main()
