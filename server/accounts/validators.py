"""가입과 관리자 비밀번호 초기화가 공유하는 계정 검증."""
import re
import unicodedata
from django.core.exceptions import ValidationError


def normalize_login_id(value):
    """Django와 같은 유니코드 정규화 뒤 소문자로 저장·비교한다."""
    return unicodedata.normalize("NFKC", value).strip().lower()


class PasswordRulesValidator:
    """privacy.md의 길이·소문자·숫자·특수문자 조건."""

    def validate(self, password, user=None):
        """모든 비밀번호 변경 경로에서 같은 규칙을 적용한다."""
        if not 8 <= len(password) <= 128 or not re.search(r"[a-z]", password) or not re.search(r"[0-9]", password) or not re.search(r"[^a-zA-Z0-9\s]", password):
            raise ValidationError(self.get_help_text(), code="password_rules")

    def get_help_text(self):
        """입력 값을 포함하지 않는 안내를 반환한다."""
        return "비밀번호는 8~128자로 소문자·숫자·특수문자를 각각 포함해야 합니다."
