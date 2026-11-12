"""공통 계약 게임 API 열 개의 등록."""
from django.urls import path
from . import views
from . import interactions
from . import npc_lines_view

urlpatterns = [
    path("games", views.start_game, name="game-start"),
    path("tutorial", views.start_tutorial, name="tutorial-start"),
    path("games/<str:game_id>", views.game_detail, name="game-detail"),
    path("games/<str:game_id>/commands", views.submit_command, name="game-command"),
    path("games/<str:game_id>/utterances", interactions.parse_utterance, name="game-utterance"),
    path("games/<str:game_id>/utterances/<str:attempt_id>", interactions.resolve_utterance, name="game-utterance-resolve"),
    path("games/<str:game_id>/renders/<str:job_id>", interactions.poll_render, name="game-render"),
    path("games/<str:game_id>/lines", npc_lines_view.npc_lines, name="game-npc-lines"),
    path("games/<str:game_id>/lines/<str:speech_id>/shown", npc_lines_view.npc_line_shown, name="game-npc-line-shown"),
    path("games/<str:game_id>/notebook", interactions.save_notebook, name="game-notebook"),
    path("games/<str:game_id>/survey", interactions.submit_survey, name="game-survey"),
    path("games/<str:game_id>/review", views.review, name="game-review"),
    path("games/<str:game_id>/review/narration", views.review_narration, name="game-review-narration"),
    path("players/me/progress", views.progress, name="player-progress"),
]
