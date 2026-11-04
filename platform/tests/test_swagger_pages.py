import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("generate_openapi", ROOT / "platform/scripts/generate-openapi.py")
GENERATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GENERATOR)


class SwaggerPagesTests(unittest.TestCase):
    def test_pages_artifact_is_portable_and_read_only(self):
        document = json.loads((ROOT / "platform/data/openapi.json").read_text(encoding="utf-8"))
        with TemporaryDirectory() as temporary:
            destination = Path(temporary)
            GENERATOR.build_pages(document, destination)
            self.assertEqual({path.name for path in destination.iterdir()}, {"index.html", "openapi.json"})
            html = (destination / "index.html").read_text(encoding="utf-8")
            self.assertIn("url: './openapi.json'", html)
            self.assertIn("supportedSubmitMethods: []", html)
            published = json.loads((destination / "openapi.json").read_text(encoding="utf-8"))
            self.assertEqual(published["paths"], document["paths"])
            self.assertEqual(published["components"], document["components"])
            self.assertEqual(published["servers"][0]["url"], "http://127.0.0.1:18100/swagger/services")
        self.assertEqual(document["servers"], [{"url": "/swagger/services"}])
        self.assertNotIn("supportedSubmitMethods: []", (ROOT / "platform/swagger.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
