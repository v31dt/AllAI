from __future__ import annotations

import unittest
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import patch

from aqt.qt import Qt

from session_dialog import (
    active_row_index_for_direction,
    choose_next_active_row_index,
    choose_session_deck_name,
    is_reveal_toggle_key,
    provider_settings_error,
    rating_for_key,
    SessionDialog,
)


class SessionDialogTests(unittest.TestCase):
    def test_choose_session_deck_name_avoids_normal_deck_collision(self) -> None:
        decks = [
            {"name": "AllAI Session", "dyn": False},
            {"name": "AllAI Session 2", "dyn": False},
            {"name": "Dutch", "dyn": False},
        ]
        self.assertEqual(choose_session_deck_name(decks), "AllAI Session 3")

    def test_choose_session_deck_name_can_reuse_filtered_deck_name(self) -> None:
        decks = [{"name": "AllAI Session", "dyn": True}]
        self.assertEqual(choose_session_deck_name(decks), "AllAI Session")

    def test_rating_for_key_maps_numeric_shortcuts(self) -> None:
        self.assertEqual(rating_for_key(Qt.Key.Key_1), "again")
        self.assertEqual(rating_for_key(Qt.Key.Key_2), "hard")
        self.assertEqual(rating_for_key(Qt.Key.Key_3), "good")
        self.assertEqual(rating_for_key(Qt.Key.Key_4), "easy")
        self.assertIsNone(rating_for_key(Qt.Key.Key_5))

    def test_is_reveal_toggle_key_maps_space(self) -> None:
        self.assertTrue(is_reveal_toggle_key(Qt.Key.Key_Space))
        self.assertFalse(is_reveal_toggle_key(Qt.Key.Key_Return))

    def test_provider_settings_accept_openai_compatible_urls(self) -> None:
        self.assertIsNone(provider_settings_error("https://openrouter.ai/api/v1", "openai/gpt-4o-mini"))
        self.assertIsNone(provider_settings_error("http://localhost:11434/v1", "llama3.2"))

    def test_provider_settings_require_valid_url_and_model(self) -> None:
        self.assertEqual(provider_settings_error("", "model"), "Base URL is required.")
        self.assertEqual(
            provider_settings_error("localhost:11434/v1", "model"),
            "Base URL must be a valid HTTP or HTTPS URL.",
        )
        self.assertEqual(provider_settings_error("https://example.com/v1", ""), "Model is required.")

    def test_stale_audio_failure_does_not_change_current_round(self) -> None:
        failure_calls: list[Exception] = []
        dialog = SimpleNamespace(audio_request_id=3, _show_tts_failure=failure_calls.append)
        future: Future[object] = Future()
        future.set_exception(RuntimeError("old request failed"))

        SessionDialog._on_round_audio_ready(dialog, 2, future)

        self.assertEqual(failure_calls, [])

    def test_stop_round_audio_only_stops_playback_owned_by_session(self) -> None:
        dialog = SimpleNamespace(audio_request_id=4, current_audio_path="/tmp/round.wav")

        with patch("session_dialog.av_player") as player:
            SessionDialog._stop_round_audio(dialog)

        self.assertEqual(dialog.audio_request_id, 5)
        self.assertIsNone(dialog.current_audio_path)
        player.stop_and_clear_queue_if_caller.assert_called_once_with(dialog)
        player.stop_and_clear_queue.assert_not_called()

    def test_active_row_index_for_direction_moves_and_clamps(self) -> None:
        row_widgets = [_FakeRowWidget(revealed=False, rating=None) for _ in range(3)]
        self.assertEqual(active_row_index_for_direction(row_widgets, None, 1), 0)
        self.assertEqual(active_row_index_for_direction(row_widgets, None, -1), 2)
        self.assertEqual(active_row_index_for_direction(row_widgets, 1, 1), 2)
        self.assertEqual(active_row_index_for_direction(row_widgets, 1, -1), 0)
        self.assertEqual(active_row_index_for_direction(row_widgets, 0, -1), 0)
        self.assertEqual(active_row_index_for_direction(row_widgets, 2, 1), 2)

    def test_choose_next_active_row_index_prefers_next_incomplete_row(self) -> None:
        row_widgets = [
            _FakeRowWidget(revealed=True, rating="good"),
            _FakeRowWidget(revealed=True, rating=None),
            _FakeRowWidget(revealed=False, rating=None),
        ]
        self.assertEqual(choose_next_active_row_index(row_widgets, 0), 1)
        self.assertEqual(choose_next_active_row_index(row_widgets, 1), 2)

    def test_choose_next_active_row_index_wraps_and_finishes(self) -> None:
        row_widgets = [
            _FakeRowWidget(revealed=True, rating="good"),
            _FakeRowWidget(revealed=True, rating="easy"),
            _FakeRowWidget(revealed=False, rating=None),
        ]
        self.assertEqual(choose_next_active_row_index(row_widgets, 2), 2)
        row_widgets[2] = _FakeRowWidget(revealed=True, rating="hard")
        self.assertIsNone(choose_next_active_row_index(row_widgets, 2))


class _FakeRowWidget:
    def __init__(self, *, revealed: bool, rating: str | None) -> None:
        self._revealed = revealed
        self.rating = rating

    def is_revealed(self) -> bool:
        return self._revealed


if __name__ == "__main__":
    unittest.main()
