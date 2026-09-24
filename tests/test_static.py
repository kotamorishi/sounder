"""静的配信（Web UI・同梱フォント）のテスト。音は出さない。"""

import sys
import tempfile
import threading
import unittest
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sounder import config, server  # noqa: E402

WEB = Path(server.__file__).resolve().parent.parent / "web"
WEIGHTS = ("Light", "Regular", "Medium", "Bold")


class TestStatic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        home = Path(cls.tmp.name)
        app = server.App.__new__(server.App)  # 内蔵サウンドの生成を省く
        app.home = home
        app.web_dir = WEB
        app.log = server.EventLog(home / "data" / "events.log")
        app.store = config.Store(home / "data" / "config.json")
        app.token = None
        app.started_at = datetime.now()
        handler = type("H", (server.Handler,), {"app": app})
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def fetch(self, path):
        with urlopen(self.base + path, timeout=5) as r:
            return r.headers, r.read()

    def test_fonts_are_bundled(self):
        for w in WEIGHTS:
            self.assertTrue((WEB / "fonts" / f"MPLUSRounded1c-{w}.woff2").is_file(), w)
        self.assertIn("Open Font License", (WEB / "fonts" / "OFL.txt").read_text())

    def test_font_served_as_woff2_with_long_cache(self):
        headers, body = self.fetch("/fonts/MPLUSRounded1c-Regular.woff2")
        self.assertEqual(headers["Content-Type"], "font/woff2")
        self.assertEqual(headers["Cache-Control"], "public, max-age=31536000, immutable")
        self.assertEqual(body[:4], b"wOF2")
        self.assertEqual(int(headers["Content-Length"]), len(body))

    def test_font_mimetypes_registered(self):
        for name, ctype in (("a.woff2", "font/woff2"), ("a.woff", "font/woff"), ("a.ttf", "font/ttf")):
            self.assertEqual(server.mimetypes.guess_type(name)[0], ctype)

    def test_other_files_stay_no_store(self):
        for path in ("/", "/style.css", "/app.js", "/apple-touch-icon.png"):
            headers, _ = self.fetch(path)
            self.assertEqual(headers["Cache-Control"], "no-store", path)

    def test_license_text_is_served(self):
        headers, body = self.fetch("/fonts/OFL.txt")
        self.assertTrue(headers["Content-Type"].startswith("text/plain"))
        self.assertIn(b"SIL Open Font License", body)

    def test_traversal_through_fonts_does_not_get_font_cache(self):
        headers, _ = self.fetch("/fonts/../style.css")
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_missing_font_is_404(self):
        with self.assertRaises(HTTPError) as cm:
            self.fetch("/fonts/nope.woff2")
        self.assertEqual(cm.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
