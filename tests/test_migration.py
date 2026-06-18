from __future__ import annotations

import unittest

from migration import parse_pipe_text


class MigrationParserTests(unittest.TestCase):
    def test_parse_pipe_text_with_example(self) -> None:
        parsed = parse_pipe_text("doen | to do (Wat ben je aan het doen?)")
        self.assertEqual(parsed.target, "doen")
        self.assertEqual(parsed.native, "to do")
        self.assertEqual(parsed.example, "Wat ben je aan het doen?")

    def test_parse_pipe_text_without_example(self) -> None:
        parsed = parse_pipe_text("dokter | doctor")
        self.assertEqual(parsed.target, "dokter")
        self.assertEqual(parsed.native, "doctor")
        self.assertEqual(parsed.example, "")

    def test_parse_pipe_text_rejects_missing_pipe(self) -> None:
        with self.assertRaises(ValueError):
            parse_pipe_text("dokter doctor")


if __name__ == "__main__":
    unittest.main()
