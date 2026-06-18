from __future__ import annotations

import unittest

from session import (
    LLMUnavailableError,
    SentenceGenerationError,
    SessionRunner,
    build_card_payload,
    build_deck_filter,
    build_search_query,
    build_state_query,
    highlight_sentence,
    match_words_to_payloads,
)


class FakeNote(dict):
    pass


class FakeCard:
    def __init__(self, card_id: int, target: str, native: str, example: str = "", due: int = 0) -> None:
        self.id = card_id
        self.due = due
        self._note = FakeNote(Target=target, Native=native, Example=example)
        self.started = False

    def note(self) -> FakeNote:
        return self._note

    def start_timer(self) -> None:
        self.started = True


class FakeSched:
    def __init__(self, log: list[tuple[str, int | str]]) -> None:
        self.log = log

    def answerCard(self, card: FakeCard, ease: int) -> None:
        self.log.append(("answer", card.id))


class FakeCollection:
    def __init__(self, cards: list[FakeCard], log: list[tuple[str, int | str]]) -> None:
        self.cards_by_id = {card.id: card for card in cards}
        self.card_order = [card.id for card in cards]
        self.log = log
        self.sched = FakeSched(log)

    def find_cards(self, query: str) -> list[int]:
        self.log.append(("query", query))
        return list(self.card_order)

    def get_card(self, card_id: int) -> FakeCard:
        return self.cards_by_id[card_id]


class ExplainCollection(FakeCollection):
    def __init__(
        self,
        cards: list[FakeCard],
        log: list[tuple[str, int | str]],
        responses: dict[str, list[int]],
    ) -> None:
        super().__init__(cards, log)
        self.responses = responses

    def find_cards(self, query: str) -> list[int]:
        self.log.append(("query", query))
        return list(self.responses.get(query, []))


class FakeLLM:
    def __init__(self, responses: list[dict | Exception]) -> None:
        self.responses = responses
        self.calls = 0

    def generate_sentence(self, prompt: str) -> dict:
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class SessionTests(unittest.TestCase):
    def test_build_search_query_with_and_without_new_cards(self) -> None:
        self.assertEqual(
            build_search_query(["Dutch"], True),
            '(is:due OR is:new) (deck:"Dutch") note:LangCard',
        )
        self.assertEqual(
            build_search_query(["Dutch", "Chinese"], False),
            '(is:due -is:new) (deck:"Dutch" OR deck:"Chinese") note:LangCard',
        )
        self.assertEqual(build_state_query(True), "(is:due OR is:new)")
        self.assertEqual(build_deck_filter(["Dutch", "Chinese"]), 'deck:"Dutch" OR deck:"Chinese"')

    def test_match_words_to_payloads_uses_exact_casefold_and_punctuation_matching(self) -> None:
        payloads = [
            build_card_payload(FakeCard(1, "dokter", "doctor")),
            build_card_payload(FakeCard(2, "Afspraak", "appointment")),
            build_card_payload(FakeCard(3, "fiets", "bike")),
        ]
        rows, unmatched = match_words_to_payloads(payloads, ["dokter", "AFSPRAAK", "fiets."])
        self.assertEqual([row.card_id for row in rows], [1, 2, 3])
        self.assertEqual(unmatched, [])

    def test_highlight_sentence_handles_whitespace_and_cjk_text(self) -> None:
        self.assertEqual(
            highlight_sentence("Ik heb morgen een afspraak.", ["afspraak"]),
            "Ik heb morgen een <b>afspraak.</b>",
        )
        self.assertEqual(
            highlight_sentence("我喜欢学习中文", ["中文", "学习"]),
            "我喜欢<b>学习</b><b>中文</b>",
        )

    def test_prepare_next_round_retries_once_after_malformed_output(self) -> None:
        log: list[tuple[str, int | str]] = []
        col = FakeCollection([FakeCard(1, "dokter", "doctor")], log)
        llm = FakeLLM(
            [
                SentenceGenerationError("bad json"),
                {"sentence": "Mijn dokter komt.", "words_used": ["dokter"]},
            ]
        )
        runner = SessionRunner(col, {"decks": ["Dutch"]}, llm)
        round_data = runner.prepare_next_round()
        self.assertIsNotNone(round_data)
        self.assertEqual(llm.calls, 2)

    def test_prepare_next_round_drops_unmatchable_card_after_three_attempts(self) -> None:
        log: list[tuple[str, int | str]] = []
        col = FakeCollection([FakeCard(1, "dokter", "doctor")], log)
        llm = FakeLLM(
            [
                {"sentence": "Een zin zonder match.", "words_used": ["onbekend"]},
                {"sentence": "Nog steeds fout.", "words_used": ["anders"]},
                {"sentence": "Nog een keer fout.", "words_used": ["weer"]},
            ]
        )
        runner = SessionRunner(col, {"decks": ["Dutch"]}, llm)
        round_data = runner.prepare_next_round()
        self.assertIsNone(round_data)
        self.assertIn(1, runner.dropped_card_ids)

    def test_answer_calls_complete_before_next_query(self) -> None:
        log: list[tuple[str, int | str]] = []
        cards = [
            FakeCard(1, "dokter", "doctor", due=1),
            FakeCard(2, "afspraak", "appointment", due=2),
        ]
        col = FakeCollection(cards, log)
        llm = FakeLLM(
            [
                {"sentence": "Mijn dokter heeft een afspraak.", "words_used": ["dokter", "afspraak"]},
                {"sentence": "Reservezin.", "words_used": ["dokter"]},
            ]
        )
        runner = SessionRunner(col, {"decks": ["Dutch"]}, llm)
        round_data = runner.prepare_next_round()
        assert round_data is not None

        runner.commit_round(round_data, {1: "good", 2: "easy"})
        runner.prepare_next_round()

        self.assertEqual(log[0][0], "query")
        self.assertEqual(log[1:3], [("answer", 1), ("answer", 2)])
        self.assertEqual(log[3][0], "query")

    def test_llm_unavailable_bubbles_up(self) -> None:
        log: list[tuple[str, int | str]] = []
        col = FakeCollection([FakeCard(1, "dokter", "doctor")], log)
        llm = FakeLLM([LLMUnavailableError("offline")])
        runner = SessionRunner(col, {"decks": ["Dutch"]}, llm)
        with self.assertRaises(LLMUnavailableError):
            runner.prepare_next_round()

    def test_explain_why_no_cards_mentions_langcard_mismatch(self) -> None:
        log: list[tuple[str, int | str]] = []
        responses = {
            '(is:due OR is:new) (deck:"dutch cursus")': [1, 2, 3],
            '(is:due OR is:new) (deck:"dutch cursus") note:LangCard': [],
        }
        col = ExplainCollection([], log, responses)
        runner = SessionRunner(col, {"decks": ["dutch cursus"]}, FakeLLM([]))
        message = runner.explain_why_no_cards()
        self.assertIn("none of them use the LangCard note type", message)


if __name__ == "__main__":
    unittest.main()
