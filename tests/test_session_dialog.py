from __future__ import annotations

import unittest

from session_dialog import choose_session_deck_name


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


if __name__ == "__main__":
    unittest.main()
