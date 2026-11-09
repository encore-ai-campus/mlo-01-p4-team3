"""계정 폼과 Django 세션 인증. 인증 입력은 응답·로그에 되비추지 않는다."""
import secrets
import uuid
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
from server.api.consent import player_context
from server.app.usecases import StartUnavailable
from server.paldo_web.composition import GameServiceUnavailable, get_game_service, get_session_service
from .lockout import login_user
from .models import AccountProfile
from .validators import normalize_login_id

CONSENT_TEXT = "플레이 기록(선택, 입력한 문장, 수첩 값, 설문)을 이름 없이 저장하고, 게임 속 인물의 행동과 입력 해석 모델을 개선하는 데 씁니다. 단, 설문 점수는 개인별이 아니라 여러 판을 모은 값으로만 분석과 게임 속 인물의 전략 조정에 씁니다. 로그인 ID와 비밀번호(복원할 수 없는 형태로 변환)는 로그인에만 쓰고 플레이 기록과 따로 보관합니다. 로그인 ID에도 이름이나 연락처를 쓰지 마세요. 개인정보(이름, 연락처 등)는 입력하지 마세요. 입력하더라도 자동으로 가립니다. 기록은 2026년 12월 12일까지 보관하며, 화면 아래의 ID로 삭제를 요청할 수 있습니다."


@require_GET
def home(request):
    """계정의 가명 ID, 동의한 사용자의 새 게임(일반 판)·이어 하기와 배정된 고정 판을 표시한다."""
    player = player_context(request.user)
    context = {"player_id": player[0] if player else None, "assignments": None}
    if player and player[1]:
        loaded = {}
        def load_game(game_id):
            if game_id not in loaded:
                loaded[game_id] = get_game_service().repository.load(game_id)
            return loaded[game_id]
        context.update(_open_game_context(player[0], load_game))
        try:
            service = get_session_service()
            context["assignments"] = service.assigned(player[0], load_game)
            context["test_round_pending"] = any(item["plan"] is not None for item in context["assignments"])
        except StartUnavailable:
            context["assignments"] = []   # 회차 저장소가 없어도 일반 판은 시작할 수 있다(E-08-14)
        except GameServiceUnavailable:
            context["assignments_unavailable"] = True
    response = render(request, "accounts/home.html", context)
    response["Cache-Control"] = "private, no-store"
    return response


def _open_game_context(player, load_game=None):
    """일반 판 시작 폼(요청마다 새 시작 UUID)과 진행 중인 일반 판 목록(E-08-14).

    시작 정책이 일반 판을 지원하지 않는 조립(미리보기의 녹화 판 회차 정책 등)에서는 시작 폼만 숨기고,
    이미 있는 일반 판의 이어 하기는 시작 정책과 무관하므로 그대로 보인다.
    """
    from server.app.open_start import StartPolicy, open_preset_ids, unfinished_open_games
    context = {"open_presets": [], "open_start_token": str(uuid.uuid4()), "open_games": []}
    try:
        context['open_presets'] = open_preset_ids()
        from server.api.story_view import preset_choices
        context['open_base_presets'], context['open_challenges'] = preset_choices(context['open_presets'])
        service = get_game_service()
        progress = service.progress.load(player)
        from server.api.story_view import ending_options
        context['ending_options'] = ending_options(progress)
        # 설문 없이 떠난 끝난 판을 건너뜀으로 채운다(B-09-07 9번). 같은 요청의 진척·판 조회를 그대로 쓴다.
        from server.api.participant_forms import backfill_surveys
        backfill_surveys(service, player, progress.game_ids, load_game or service.repository.load)
        if not isinstance(service.prepare_start, StartPolicy):
            context["open_presets"] = []
        context["open_games"] = unfinished_open_games(progress.game_ids, load_game or service.repository.load)
    except (GameServiceUnavailable, StartUnavailable):
        context["open_unavailable"] = True
    return context


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
            previous_key = request.session.session_key
            login(request, user)
            # Django는 같은 계정이 다시 로그인하면 기존 세션 키를 유지한다.
            if previous_key is not None and request.session.session_key == previous_key:
                request.session.cycle_key()
            return redirect("home")
        return render(request, "accounts/login.html", {"error": "로그인할 수 없습니다. 입력을 확인하거나 잠시 후 다시 시도해 주세요."}, status=400)
    return render(request, "accounts/login.html")


@require_POST
def signout(request):
    """Django 세션을 폐기한다. GET 로그아웃은 허용하지 않는다."""
    logout(request)
    return redirect("accounts:login")
