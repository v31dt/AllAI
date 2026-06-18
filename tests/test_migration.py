from __future__ import annotations

import unittest

from migration import (
    MODE_COPY_KEEP,
    MODE_COPY_SUSPEND,
    build_migration_card_query,
    collect_note_ids_for_migration,
    detect_migration_source,
    extract_langcard_data,
    mode_suspends_originals,
    parse_front_back_fields,
    parse_pipe_text,
)


class FakeNote(dict):
    def has_tag(self, tag: str) -> bool:
        tags = self.get("tags", "")
        return tag in tags.split()


class FakeCard:
    def __init__(self, card_id: int, note_id: int) -> None:
        self.id = card_id
        self.nid = note_id


class FakeCollection:
    def __init__(self, query_results: dict[str, list[int]], cards: dict[int, FakeCard]) -> None:
        self.query_results = query_results
        self.cards = cards

    def find_cards(self, query: str) -> list[int]:
        return list(self.query_results.get(query, []))

    def get_card(self, card_id: int) -> FakeCard:
        return self.cards[card_id]


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

    def test_extract_langcard_data_from_front_back_note(self) -> None:
        note = FakeNote(
            Front="bijzin",
            Back='subordinate clause; the finite verb moves to the end <br> '
            'Ik doe een cursus omdat ik Nederlands wil leren. <br> Extra line',
            Hint="",
        )
        parsed = extract_langcard_data(note)
        self.assertEqual(parsed.target, "bijzin")
        self.assertEqual(parsed.native, "subordinate clause; the finite verb moves to the end")
        self.assertEqual(parsed.example, "Ik doe een cursus omdat ik Nederlands wil leren. <br> Extra line")

    def test_detect_migration_source_prefers_front_back(self) -> None:
        note = FakeNote(Front="woord", Back="meaning <br> example", Hint="")
        source = detect_migration_source(note)
        self.assertEqual(source.kind, "front_back")

    def test_build_migration_card_query(self) -> None:
        self.assertEqual(
            build_migration_card_query("dutch cursus", "Basic (and reversed card)"),
            'deck:"dutch cursus" note:"Basic (and reversed card)"',
        )

    def test_collect_note_ids_for_migration_dedupes_reversed_cards(self) -> None:
        query = 'deck:"dutch cursus" note:"Basic (and reversed card)"'
        col = FakeCollection(
            query_results={query: [1, 2, 3]},
            cards={
                1: FakeCard(1, 100),
                2: FakeCard(2, 100),
                3: FakeCard(3, 101),
            },
        )
        self.assertEqual(collect_note_ids_for_migration(col, "dutch cursus", "Basic (and reversed card)"), [100, 101])


if __name__ == "__main__":
    unittest.main()
