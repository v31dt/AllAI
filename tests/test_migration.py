from __future__ import annotations

import unittest

from migration import MODE_COPY_KEEP, MODE_COPY_SUSPEND, mode_suspends_originals, parse_front_back_fields, parse_pipe_text


class MigrationParserTests(unittest.TestCase):
    def test_parse_pipe_text_with_example(self) -> None:
        parsed = parse_pipe_text("doen | to do (Wat ben je aan het doen?)")
        self.assertEqual(parsed.target, "doen")
        self.assertEqual(parsed.native, "to do")
        self.assertEqual(parsed.example, "Wat ben je aan het doen?")

    def test_parse_pipe_text_with_html_break_example(self) -> None:
        parsed = parse_pipe_text(
            'bijzin | subordinate clause; the finite verb moves to the end <br> '
            'Ik doe een cursus omdat ik Nederlands wil leren. – "I am taking a course because I want to learn Dutch."'
        )
        self.assertEqual(parsed.target, "bijzin")
        self.assertEqual(parsed.native, "subordinate clause; the finite verb moves to the end")
        self.assertEqual(
            parsed.example,
            'Ik doe een cursus omdat ik Nederlands wil leren. – "I am taking a course because I want to learn Dutch."',
        )

    def test_parse_pipe_text_without_example(self) -> None:
        parsed = parse_pipe_text("dokter | doctor")
        self.assertEqual(parsed.target, "dokter")
        self.assertEqual(parsed.native, "doctor")
        self.assertEqual(parsed.example, "")

    def test_parse_pipe_text_rejects_missing_pipe(self) -> None:
        with self.assertRaises(ValueError):
            parse_pipe_text("dokter doctor")

    def test_parse_front_back_fields(self) -> None:
        parsed = parse_front_back_fields(
            "gesneden",
            'sliced <br> Het brood is gesneden. – "The bread is sliced."',
        )
        self.assertEqual(parsed.target, "gesneden")
        self.assertEqual(parsed.native, "sliced")
        self.assertEqual(parsed.example, 'Het brood is gesneden. – "The bread is sliced."')

    def test_mode_suspends_originals(self) -> None:
        self.assertTrue(mode_suspends_originals(MODE_COPY_SUSPEND))
        self.assertFalse(mode_suspends_originals(MODE_COPY_KEEP))


if __name__ == "__main__":
    unittest.main()
