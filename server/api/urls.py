"""공통 계약 게임 API 열 개의 등록."""
from django.urls import path
from . import views
from . import interactions

urlpatterns = [
    path("games", views.start_game, name="game-start"),
    path("games/<str:game_id>", views.game_detail, name="game-detail"),
    path("games/<str:game_id>/commands", views.submit_command, name="game-command"),
    path("games/<str:game_id>/utterances", interactions.parse_utterance, name="game-utterance"),
    path("games/<str:game_id>/utterances/<str:attempt_id>", interactions.resolve_utterance, name="game-utterance-resolve"),
    path("games/<str:game_id>/renders/<str:job_id>", interactions.poll_render, name="game-render"),
    path("games/<str:game_id>/notebook", interactions.save_notebook, name="game-notebook"),
    path("games/<str:game_id>/survey", interactions.submit_survey, name="game-survey"),
    path("games/<str:game_id>/survey/leave", interactions.leave_survey, name="game-survey-leave"),
    path("games/<str:game_id>/review", views.review, name="game-review"),
    path("players/me/progress", views.progress, name="player-progress"),
]
