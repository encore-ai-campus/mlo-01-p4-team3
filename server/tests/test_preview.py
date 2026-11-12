"""개발용 미리보기 서버의 템플릿 설정 시험."""
import copy
from pathlib import Path

from django.conf import settings
from django.template import TemplateDoesNotExist
from django.template.backends.django import DjangoTemplates
from django.test import SimpleTestCase

from server.preview import add_preview_templates

ROOT = Path(__file__).resolve().parents[2]


def _engine(config):
    """설정 사본으로 독립 템플릿 엔진을 만든다."""
    params = copy.deepcopy(config)
    params.pop("BACKEND", None)
    params.setdefault("NAME", "preview-test")
    return DjangoTemplates(params)


class PreviewTemplateTests(SimpleTestCase):
    """미리보기 로그인 템플릿을 찾고 운영 템플릿 순서는 그대로인지 본다."""

    def setUp(self):
        """운영 설정의 TEMPLATES 사본을 준비한다."""
        self.config = copy.deepcopy(settings.TEMPLATES[0])

    def test_dirs_append_alone_does_not_find_preview_login(self):
        """고치기 전 방식(DIRS에만 추가)은 UI 로더가 보지 않아 찾지 못한다."""
        self.config["DIRS"] = [*self.config["DIRS"], ROOT / "server" / "tests" / "templates"]
        with self.assertRaises(TemplateDoesNotExist):
            _engine(self.config).get_template("preview_login.html")

    def test_preview_login_found_after_add(self):
        """미리보기 폴더를 더하면 로그인 템플릿을 찾는다."""
        add_preview_templates(self.config, ROOT / "server" / "tests" / "templates")
        template = _engine(self.config).get_template("preview_login.html")
        self.assertIn("preview_login.html", template.origin.name)

    def test_existing_templates_keep_priority(self):
        """기존 화면 템플릿은 여전히 UI 로더에서 먼저 찾는다."""
        before = _engine(self.config).get_template("meeting.html").origin.name
        add_preview_templates(self.config, ROOT / "server" / "tests" / "templates")
        after = _engine(self.config).get_template("meeting.html").origin.name
        self.assertEqual(before, after)
        inner = self.config["OPTIONS"]["loaders"][0][1]
        self.assertEqual(inner[-1][0], "django.template.loaders.filesystem.Loader")
