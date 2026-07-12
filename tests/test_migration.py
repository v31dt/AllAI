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
    parse_mandarin_front_back,
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

    def test_parse_mandarin_prime_note_with_example_and_links(self) -> None:
        parsed = parse_mandarin_front_back(
            "机场 ",
            ' airport <br> 我们去机场接他。- We’re going to the airport to pick him up. <br> '
            '<a href="https://www.strokeorder.com/chinese/机场">Order</a> '
            '<a href="https://www.mdbg.net/chinese/dictionary?wdqb=机场">Audio</a> ',
            " jīchǎng",
        )
        self.assertEqual(parsed.target, "机场")
        self.assertEqual(parsed.native, "airport")
        self.assertEqual(parsed.example, "我们去机场接他。- We’re going to the airport to pick him up.")
        self.assertEqual(parsed.reading, "jīchǎng")

    def test_parse_mandarin_note_without_example(self) -> None:
        parsed = parse_mandarin_front_back(
            "自 ",
            ' self <a href="https://www.strokeorder.com/chinese/自">Order</a> '
            '<a href="https://www.mdbg.net/chinese/dictionary?wdqb=自">Audio</a> ',
            " zì",
        )
        self.assertEqual(parsed.target, "自")
        self.assertEqual(parsed.native, "self")
        self.assertEqual(parsed.example, "")
        self.assertEqual(parsed.reading, "zì")

    def test_parse_mandarin_note_with_reading_packed_in_back(self) -> None:
        parsed = parse_mandarin_front_back(
            "星 ",
            ' xīng - star (used in 星期) <br> '
            '<a href="https://www.strokeorder.com/chinese/星" target="_blank">Order</a> '
            '<a href="https://www.mdbg.net/chinese/dictionary?wdqb=星" target="_blank">Audio</a>',
            "",
        )
        self.assertEqual(parsed.target, "星")
        self.assertEqual(parsed.native, "star (used in 星期)")
        self.assertEqual(parsed.reading, "xīng")

    def test_parse_mandarin_reversed_note(self) -> None:
        parsed = parse_mandarin_front_back("tomorrow", "明天 (míngtiān)", "")
        self.assertEqual(parsed.target, "明天")
        self.assertEqual(parsed.native, "tomorrow")
        self.assertEqual(parsed.reading, "míngtiān")

    def test_parse_mandarin_reversed_note_with_quotes(self) -> None:
        parsed = parse_mandarin_front_back('"I, me"', '"我 (wǒ)"', "")
        self.assertEqual(parsed.target, "我")
        self.assertEqual(parsed.native, "I, me")
        self.assertEqual(parsed.reading, "wǒ")

    def test_parse_mandarin_grammar_note_with_term_reading_and_explanation(self) -> None:
        parsed = parse_mandarin_front_back(
            "了 (le) as a sentence-final particle indicates a change of state",
            "下雨了 (Xià yǔ le) <br> It’s raining now",
            "",
        )
        self.assertEqual(parsed.target, "了")
        self.assertEqual(parsed.reading, "le")
        self.assertEqual(parsed.native, "as a sentence-final particle indicates a change of state")
        self.assertEqual(parsed.example, "下雨了 (Xià yǔ le) <br> It’s raining now")

    def test_parse_mandarin_radical_note_with_reading_in_front(self) -> None:
        parsed = parse_mandarin_front_back("氵- shuǐ", "water", "")
        self.assertEqual(parsed.target, "氵")
        self.assertEqual(parsed.reading, "shuǐ")
        self.assertEqual(parsed.native, "water")

    def test_parse_mandarin_radical_note_with_toneless_reading_prefix(self) -> None:
        parsed = parse_mandarin_front_back("们", "men - plural suffix", "")
        self.assertEqual(parsed.target, "们")
        self.assertEqual(parsed.reading, "men")
        self.assertEqual(parsed.native, "plural suffix")

    def test_parse_mandarin_radical_note_with_bare_pinyin_back(self) -> None:
        parsed = parse_mandarin_front_back("羊", "yáng", "")
        self.assertEqual(parsed.target, "羊")
        self.assertEqual(parsed.reading, "yáng")
        self.assertEqual(parsed.native, "")

    def test_parse_mandarin_keeps_ambiguous_toneless_back_as_native(self) -> None:
        parsed = parse_mandarin_front_back("人", "Person", "")
        self.assertEqual(parsed.target, "人")
        self.assertEqual(parsed.reading, "")
        self.assertEqual(parsed.native, "Person")

    def test_parse_mandarin_note_with_packed_example_suffix(self) -> None:
        parsed = parse_mandarin_front_back(
            "Snowflake; Example: I saw a snowflake.",
            "雪花 (xuě huā); Example: 我看到了一片雪花。 (Wǒ kàn dàole yī piàn xuě huā.)",
            "",
        )
        self.assertEqual(parsed.target, "雪花")
        self.assertEqual(parsed.reading, "xuě huā")
        self.assertEqual(parsed.native, "Snowflake")
        self.assertEqual(
            parsed.example,
            '我看到了一片雪花。 (Wǒ kàn dàole yī piàn xuě huā.) – "I saw a snowflake."',
        )

    def test_parse_mandarin_rejects_sentence_notes(self) -> None:
        with self.assertRaises(ValueError):
            parse_mandarin_front_back(
                "妹妹喜欢穿新衣服上学。<br>Mèimei xǐhuān chuān xīn yīfu shàngxué..",
                "Younger sister likes to wear new clothes to school.<br><br>妹妹 (mèimei): younger sister",
                "",
            )

    def test_extract_langcard_data_routes_cjk_notes_to_mandarin_parser(self) -> None:
        note = FakeNote(
            Front="骑 ",
            Back=' to ride <br> 他骑自行车上学。- He rides a bike to school. <br> '
            '<a href="https://www.strokeorder.com/chinese/骑">Order</a> '
            '<a href="https://www.mdbg.net/chinese/dictionary?wdqb=骑">Audio</a> ',
            Hint=" qí",
        )
        parsed = extract_langcard_data(note)
        self.assertEqual(parsed.target, "骑")
        self.assertEqual(parsed.native, "to ride")
        self.assertEqual(parsed.example, "他骑自行车上学。- He rides a bike to school.")
        self.assertEqual(parsed.reading, "qí")

    def test_extract_langcard_data_keeps_dutch_notes_on_plain_parser(self) -> None:
        note = FakeNote(Front="dokter", Back="doctor", Hint="")
        parsed = extract_langcard_data(note)
        self.assertEqual(parsed.target, "dokter")
        self.assertEqual(parsed.native, "doctor")
        self.assertEqual(parsed.reading, "")

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
