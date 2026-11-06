"""계정 폼과 Django 세션 인증. 인증 입력은 응답·로그에 되비추지 않는다."""
import secrets
from django.conf import settings
from django.contrib.auth import get_user_model, login, logout
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from .lockout import login_user
from .models import AccountProfile
from .validators import normalize_login_id

CONSENT_TEXT = "플레이 기록(선택, 입력한 문장, 수첩 값, 설문)을 이름 없이 저장하고, 게임 속 인물의 행동과 입력 해석 모델을 개선하는 데 씁니다. 단, 설문 점수는 개인별이 아니라 여러 판을 모은 값으로만 분석과 게임 속 인물의 전략 조정에 씁니다. 로그인 ID와 비밀번호(복원할 수 없는 형태로 변환)는 로그인에만 쓰고 플레이 기록과 따로 보관합니다. 로그인 ID에도 이름이나 연락처를 쓰지 마세요. 개인정보(이름, 연락처 등)는 입력하지 마세요. 입력하더라도 자동으로 가립니다. 기록은 2026년 12월 12일까지 보관하며, 화면 아래의 ID로 삭제를 요청할 수 있습니다."


@require_GET
def home(request):
    """가입·로그인 후 공개 화면에는 자기 가명만 표시한다."""
    profile = getattr(request.user, "paldo_profile", None)
    response = render(request, "accounts/home.html", {"player_id": profile.player_pseudonym if profile else None})
    response["Cache-Control"] = "private, no-store"
    return response


@sensitive_post_parameters()
@require_http_methods(["GET", "POST"])
def signup(request):
    """동의·비밀번호 검증 후 계정과 가명 ID를 함께 저장한다."""
    context = {"consent_text": CONSENT_TEXT, "invite_required": bool(settings.SIGNUP_INVITE_CODE)}
    if request.method == "POST":
        username = normalize_login_id(request.POST.get("username", ""))
        password = request.POST.get("password", "")
        user = get_user_model()(username=username)
        try:
            if not username or len(username) > 150:
                raise ValidationError("로그인 ID를 확인해 주세요.")
            UnicodeUsernameValidator()(username)
            if request.POST.get("consent") != "on":
                raise ValidationError("기록 수집에 동의해야 가입할 수 있습니다.")
            if password != request.POST.get("password_confirm"):
                raise ValidationError("비밀번호 확인이 일치하지 않습니다.")
            if settings.SIGNUP_INVITE_CODE and not secrets.compare_digest(settings.SIGNUP_INVITE_CODE.encode(), request.POST.get("invite_code", "").encode()):
                raise ValidationError("초대 코드를 확인해 주세요.")
            validate_password(password, user)
            with transaction.atomic():
                user.set_password(password)
                user.save()
                AccountProfile.objects.create(user=user, consented_at=timezone.now())
        except ValidationError as exc:
            context["error"] = " ".join(exc.messages)
            return render(request, "accounts/signup.html", context, status=400)
        except IntegrityError:
            context["error"] = "사용할 수 없는 로그인 ID입니다."
            return render(request, "accounts/signup.html", context, status=400)
        login(request, user)
        return redirect("home")
    return render(request, "accounts/signup.html", context)


@sensitive_post_parameters()
@require_http_methods(["GET", "POST"])
def signin(request):
    """실패·없는 계정·잠금 모두 같은 응답을 보낸다."""
    if request.method == "POST":
        user = login_user(request, request.POST.get("username", ""), request.POST.get("password", ""))
        if user is not None:
            login(request, user)
            return redirect("home")
        return render(request, "accounts/login.html", {"error": "로그인할 수 없습니다. 입력을 확인하거나 잠시 후 다시 시도해 주세요."}, status=400)
    return render(request, "accounts/login.html")


@require_POST
def signout(request):
    """Django 세션을 폐기한다. GET 로그아웃은 허용하지 않는다."""
    logout(request)
    return redirect("accounts:login")
