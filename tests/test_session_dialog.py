from __future__ import annotations

import unittest

from aqt.qt import Qt

from session_dialog import (
    active_row_index_for_direction,
    choose_next_active_row_index,
    choose_session_deck_name,
    is_reveal_toggle_key,
    rating_for_key,
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
