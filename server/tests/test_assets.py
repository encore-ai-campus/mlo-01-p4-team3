"""manifest 전체 파일·누락 대체·경로 탈출·동일 URL 에셋 교체를 검사한다."""
import json
import struct
import tempfile
import zlib
from html.parser import HTMLParser
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase

from server.api.assets import asset_url
from server.app.dto import StartGameInput
from server.paldo_web.composition import configure_game_service
from .support import install_test_profiles, make_service


def _png_bytes(red, green, blue):
    """외부 이미지 라이브러리 없이 서로 다른 1픽셀 PNG 시험 자료를 만든다."""
    def chunk(kind, data):
        payload = kind + data
        return struct.pack("!I", len(data)) + payload + struct.pack("!I", zlib.crc32(payload))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack("!2I5B", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes((0, red, green, blue))))
            + chunk(b"IEND", b""))


class _ImageSources(HTMLParser):
    """실제 렌더된 HTML에서 이미지 URL의 순서를 읽는다."""

    def __init__(self, html):
        super().__init__()
        self.sources = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        """img의 src만 수집한다."""
        if tag == "img":
            self.sources.append(dict(attrs).get("src"))


class Assets(SimpleTestCase):
    """고정 수량 대신 manifest의 모든 항목을 순회한다."""

    def test_every_manifest_url_is_an_image(self):
        """현재 manifest에 등록된 이미지 전체의 응답을 검사한다."""
        manifest = json.loads(settings.ASSET_MANIFEST.read_text(encoding="utf-8"))
        for item in manifest:
            with self.subTest(url=item["url"]):
                response = self.client.get(item["url"])
                try:
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response["Content-Type"], "image/png")
                    self.assertEqual(b"".join(response.streaming_content), (settings.BASE_DIR / item["path"]).read_bytes())
                finally:
                    response.close()
        ads = sum(item["url"].startswith("/static/ads/") for item in manifest)
        print(f"Manifest images verified: {len(manifest)} ({ads} ads)", flush=True)

    def test_unknown_id_has_fallback_and_no_path_escape(self):
        """누락은 대체하고 정적 경로 밖 파일은 차단한다."""
        with self.assertLogs("paldo.web", level="WARNING"):
            url = asset_url("portrait:missing-character")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        response.close()
        self.assertEqual(self.client.get("/static/../server/manage.py").status_code, 404)


class AssetReplacement(TestCase):
    """저장소 에셋을 건드리지 않고 실제 게임 화면의 교체 가능성을 확인한다."""

    def test_same_manifest_urls_serve_replaced_image_set(self):
        """ID·URL·파일명을 유지한 두 PNG 세트가 같은 화면에서 서로 다른 바이트로 제공된다."""
        manifest = json.loads(Path(settings.ASSET_MANIFEST).read_text(encoding="utf-8"))
        manifest_text = json.dumps(manifest, ensure_ascii=False)
        user = User.objects.create_user(username="asset-replacement-player")
        profile = install_test_profiles(self)(user)
        self.client.force_login(user)
        service = make_service()
        configure_game_service(service)
        self.addCleanup(configure_game_service, None)
        player = str(profile.player_pseudonym)
        game = service.start(StartGameInput(player, "base6", "test-session", "f"), player, True)
        game_url = f"/games/{game.view.game_id}"
        first_png, second_png = _png_bytes(240, 40, 40), _png_bytes(40, 40, 240)
        self.assertNotEqual(first_png, second_png)

        with tempfile.TemporaryDirectory(prefix="paldo-asset-replacement-") as directory:
            root = Path(directory)
            manifest_path = root / "content/images/manifest.json"
            manifest_path.parent.mkdir(parents=True)
            manifest_path.write_text(manifest_text, encoding="utf-8")
            image_paths = {root / item["path"] for item in manifest}
            for path in image_paths:
                self.assertTrue(path.resolve().is_relative_to((root / "content/images").resolve()))
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(first_png)

            # TEMPLATES는 저장소의 실제 경로를 유지하고 제공할 에셋 루트만 바꾼다.
            with self.settings(BASE_DIR=root, ASSET_MANIFEST=manifest_path):
                first_page = self.client.get(game_url, HTTP_ACCEPT="text/html")
                self.assertEqual(first_page.status_code, 200)
                self.assertTemplateUsed(first_page, "game.html")
                self.assertTemplateUsed(first_page, "meeting.html")
                first_urls = _ImageSources(first_page.content.decode()).sources
                self.assertTrue(first_urls)
                self.assertTrue(set(first_urls).issubset({item["url"] for item in manifest}))
                for url in first_urls:
                    with self.subTest(stage="before", url=url):
                        response = self.client.get(url)
                        try:
                            self.assertEqual(response.status_code, 200)
                            self.assertEqual(response["Content-Type"], "image/png")
                            self.assertEqual(b"".join(response.streaming_content), first_png)
                        finally:
                            response.close()

                for path in image_paths:
                    path.write_bytes(second_png)

                second_page = self.client.get(game_url, HTTP_ACCEPT="text/html")
                self.assertEqual(second_page.status_code, 200)
                self.assertTemplateUsed(second_page, "game.html")
                self.assertTemplateUsed(second_page, "meeting.html")
                self.assertEqual(_ImageSources(second_page.content.decode()).sources, first_urls)
                self.assertEqual(manifest_path.read_text(encoding="utf-8"), manifest_text)
                for url in first_urls:
                    with self.subTest(stage="after", url=url):
                        response = self.client.get(url)
                        try:
                            self.assertEqual(response.status_code, 200)
                            self.assertEqual(response["Content-Type"], "image/png")
                            self.assertEqual(b"".join(response.streaming_content), second_png)
                        finally:
                            response.close()
