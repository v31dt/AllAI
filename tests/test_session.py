from __future__ import annotations

from types import SimpleNamespace
import unittest

from session import (
    LLMUnavailableError,
    RoundCommitError,
    SentenceGenerationError,
    SessionRunner,
    build_card_payload,
    build_deck_filter,
    build_generation_prompt,
    build_search_query,
    build_state_query,
    find_payload_issue,
    highlight_sentence,
    match_words_to_payloads,
    parse_generation_payload,
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
    def __init__(
        self,
        col: "FakeCollection",
        log: list[tuple[str, int | str]],
        *,
        fail_on_answer_number: int | None = None,
    ) -> None:
        self.col = col
        self.log = log
        self.fail_on_answer_number = fail_on_answer_number
        self.answer_calls = 0

    def get_queued_cards(self, fetch_limit: int = 1, intraday_learning_only: bool = False) -> SimpleNamespace:
        self.log.append(("queue_fetch", fetch_limit))
        cards = [
            SimpleNamespace(card=SimpleNamespace(id=card_id))
            for card_id in self.col.queue_order[:fetch_limit]
        ]
        return SimpleNamespace(cards=cards)

    def answerCard(self, card: FakeCard, ease: int) -> None:
        self.answer_calls += 1
        if self.fail_on_answer_number == self.answer_calls:
            raise AssertionError("forced answer failure")
        if not self.col.queue_order or self.col.queue_order[0] != card.id:
            raise AssertionError("not at top of queue")
        self.col.queue_order.pop(0)
        self.col._pending_undo_card_id = card.id
        self.log.append(("answer", card.id))


class FakeCollection:
    def __init__(
        self,
        cards: list[FakeCard],
        log: list[tuple[str, int | str]],
        *,
        fail_on_answer_number: int | None = None,
        fail_undo: bool = False,
    ) -> None:
        self.cards_by_id = {card.id: card for card in cards}
        self.card_order = [card.id for card in cards]
        self.queue_order = [card.id for card in cards]
        self.log = log
        self.sched = FakeSched(self, log, fail_on_answer_number=fail_on_answer_number)
        self.fail_undo = fail_undo
        self._pending_undo_card_id: int | None = None
        self._undo_entries: dict[int, list[int]] = {}
        self._next_undo_entry = 1

    def find_cards(self, query: str) -> list[int]:
        self.log.append(("query", query))
        return list(self.card_order)

    def get_card(self, card_id: int) -> FakeCard:
        return self.cards_by_id[card_id]

    def add_custom_undo_entry(self, name: str) -> int:
        entry = self._next_undo_entry
        self._next_undo_entry += 1
        self._undo_entries[entry] = []
        self.log.append(("undo_entry", name))
        return entry

    def merge_undo_entries(self, target: int) -> None:
        if self._pending_undo_card_id is not None:
            self._undo_entries[target].append(self._pending_undo_card_id)
            self._pending_undo_card_id = None
        self.log.append(("merge_undo", target))

    def undo(self) -> None:
        if self.fail_undo:
            raise AssertionError("forced undo failure")
        if not self._undo_entries:
            return
        target = max(self._undo_entries)
        for card_id in reversed(self._undo_entries[target]):
            self.queue_order.insert(0, card_id)
        self.log.append(("undo", target))


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
            '(is:due OR is:new) (deck:"Dutch") note:LangCard card:"Recognition"',
        )
        self.assertEqual(
            build_search_query(["Dutch", "Chinese"], False),
            '(is:due -is:new) (deck:"Dutch" OR deck:"Chinese") note:LangCard card:"Recognition"',
        )
        self.assertEqual(build_state_query(True), "(is:due OR is:new)")
        self.assertEqual(build_deck_filter(["Dutch", "Chinese"]), 'deck:"Dutch" OR deck:"Chinese"')

    def test_build_generation_prompt_forbids_switching_to_english(self) -> None:
        prompt = build_generation_prompt(["rok", "plein", "weet"])
        self.assertIn("The entire sentence must be in the same target language", prompt)
        self.assertIn("Never switch into English or another language", prompt)
        self.assertIn("Only output an English sentence if every provided word is already English", prompt)

    def test_match_words_to_payloads_uses_exact_casefold_and_punctuation_matching(self) -> None:
        payloads = [
            build_card_payload(FakeCard(1, "dokter", "doctor")),
            build_card_payload(FakeCard(2, "Afspraak", "appointment")),
            build_card_payload(FakeCard(3, "fiets", "bike")),
        ]
        _, words_used = parse_generation_payload(
            {
                "sentence": "Mijn dokter plant een afspraak met de fiets.",
                "words_used": ["dokter", "AFSPRAAK", "fiets."],
            }
        )
        rows, unmatched = match_words_to_payloads(payloads, words_used)
        self.assertEqual([row.card_id for row in rows], [1, 2, 3])
        self.assertEqual(unmatched, [])

    def test_parse_generation_payload_accepts_target_surface_mapping(self) -> None:
        sentence, words_used = parse_generation_payload(
            {
                "sentence": "Ik sprak gisteren met de dokter.",
                "words_used": [{"target": "spreken", "surface": "sprak"}, {"target": "dokter", "surface": "dokter"}],
            }
        )
        self.assertEqual(sentence, "Ik sprak gisteren met de dokter.")
        self.assertEqual(words_used[0].target, "spreken")
        self.assertEqual(words_used[0].surface, "sprak")

    def test_match_words_to_payloads_prefers_explicit_target_mapping_for_inflection(self) -> None:
        payloads = [
            build_card_payload(FakeCard(1, "spreken", "to speak")),
            build_card_payload(FakeCard(2, "dokter", "doctor")),
        ]
        _, words_used = parse_generation_payload(
            {
                "sentence": "Ik sprak met de dokter.",
                "words_used": [
                    {"target": "spreken", "surface": "sprak"},
                    {"target": "dokter", "surface": "dokter"},
                ],
            }
        )
        rows, unmatched = match_words_to_payloads(payloads, words_used)
        self.assertEqual([row.card_id for row in rows], [1, 2])
        self.assertEqual([row.surface_form for row in rows], ["sprak", "dokter"])
        self.assertEqual(unmatched, [])

    def test_find_payload_issue_flags_identical_target_native_with_mismatched_example_language(self) -> None:
        payload = build_card_payload(
            FakeCard(
                1,
                "airplane",
                "airplane",
                'Het vliegtuig vliegt hoog. – "The airplane flies high."',
            )
        )
        issue = find_payload_issue(payload)
        self.assertIsNotNone(issue)
        assert issue is not None
        self.assertIn('Target and Native are both "airplane"', issue)

    def test_prepare_next_round_stops_on_malformed_lead_card(self) -> None:
        log: list[tuple[str, int | str]] = []
        col = FakeCollection(
            [FakeCard(1, "airplane", "airplane", 'Het vliegtuig vliegt hoog. – "The airplane flies high."')],
            log,
        )
        runner = SessionRunner(col, {"decks": ["Dutch"]}, FakeLLM([]))
        round_data = runner.prepare_next_round()
        self.assertIsNone(round_data)
        self.assertIn("looks malformed", runner.drain_messages()[0])

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
            ]
        )
        runner = SessionRunner(col, {"decks": ["Dutch"], "session": {"max_skip_attempts_per_card": 1}}, llm)
        round_data = runner.prepare_next_round()
        self.assertIsNone(round_data)
        self.assertEqual(runner.skip_counts[1], 1)

    def test_prepare_next_round_reduces_batch_until_queue_prefix_is_schedulable(self) -> None:
        log: list[tuple[str, int | str]] = []
        col = FakeCollection(
            [
                FakeCard(1, "dokter", "doctor", due=1),
                FakeCard(2, "afspraak", "appointment", due=2),
            ],
            log,
        )
        llm = FakeLLM(
            [
                {"sentence": "Mijn dokter komt.", "words_used": ["dokter"]},
                {"sentence": "Mijn dokter komt.", "words_used": ["dokter"]},
            ]
        )
        runner = SessionRunner(col, {"decks": ["Dutch"]}, llm)
        round_data = runner.prepare_next_round()
        assert round_data is not None
        self.assertEqual([row.card_id for row in round_data.rows], [1])
        self.assertEqual(llm.calls, 2)

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

        queue_fetch_indexes = [index for index, entry in enumerate(log) if entry == ("queue_fetch", 4)]
        answer_indexes = [index for index, entry in enumerate(log) if entry[0] == "answer"]
        self.assertEqual(queue_fetch_indexes[0], 0)
        self.assertEqual([log[index] for index in answer_indexes], [("answer", 1), ("answer", 2)])
        self.assertTrue(all(index < queue_fetch_indexes[1] for index in answer_indexes))

    def test_commit_round_rolls_back_partial_answers_when_later_answer_fails(self) -> None:
        log: list[tuple[str, int | str]] = []
        cards = [
            FakeCard(1, "dokter", "doctor", due=1),
            FakeCard(2, "afspraak", "appointment", due=2),
        ]
        col = FakeCollection(cards, log, fail_on_answer_number=2)
        llm = FakeLLM(
            [
                {
                    "sentence": "Mijn dokter heeft een afspraak.",
                    "words_used": [
                        {"target": "dokter", "surface": "dokter"},
                        {"target": "afspraak", "surface": "afspraak"},
                    ],
                }
            ]
        )
        runner = SessionRunner(col, {"decks": ["Dutch"]}, llm)
        round_data = runner.prepare_next_round()
        assert round_data is not None

        with self.assertRaises(RoundCommitError) as exc:
            runner.commit_round(round_data, {1: "good", 2: "easy"})

        self.assertEqual(col.queue_order, [1, 2])
        self.assertEqual(runner.reviewed_words, 0)
        self.assertEqual(runner.completed_rounds, 0)
        self.assertTrue(exc.exception.rollback_succeeded)
        self.assertEqual(exc.exception.committed_count, 0)
        self.assertIn(("undo", 1), log)

    def test_commit_round_reports_when_partial_answers_cannot_be_rolled_back(self) -> None:
        log: list[tuple[str, int | str]] = []
        cards = [
            FakeCard(1, "dokter", "doctor", due=1),
            FakeCard(2, "afspraak", "appointment", due=2),
        ]
        col = FakeCollection(cards, log, fail_on_answer_number=2, fail_undo=True)
        llm = FakeLLM(
            [
                {
                    "sentence": "Mijn dokter heeft een afspraak.",
                    "words_used": [
                        {"target": "dokter", "surface": "dokter"},
                        {"target": "afspraak", "surface": "afspraak"},
                    ],
                }
            ]
        )
        runner = SessionRunner(col, {"decks": ["Dutch"]}, llm)
        round_data = runner.prepare_next_round()
        assert round_data is not None

        with self.assertRaises(RoundCommitError) as exc:
            runner.commit_round(round_data, {1: "good", 2: "easy"})

        self.assertEqual(col.queue_order, [2])
        self.assertFalse(exc.exception.rollback_succeeded)
        self.assertEqual(exc.exception.committed_count, 1)

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
            '(is:due OR is:new) (deck:"dutch cursus") note:LangCard card:"Recognition"': [],
        }
        col = ExplainCollection([], log, responses)
        runner = SessionRunner(col, {"decks": ["dutch cursus"]}, FakeLLM([]))
        message = runner.explain_why_no_cards()
        self.assertIn("none of them use the LangCard note type", message)

    def test_explain_why_no_cards_mentions_reverse_only_langcards(self) -> None:
        log: list[tuple[str, int | str]] = []
        responses = {
            '(is:due OR is:new) (deck:"dutch cursus")': [1, 2, 3],
            '(is:due OR is:new) (deck:"dutch cursus") note:LangCard': [1, 2, 3],
            '(is:due OR is:new) (deck:"dutch cursus") note:LangCard card:"Recognition"': [],
        }
        col = ExplainCollection([], log, responses)
        runner = SessionRunner(col, {"decks": ["dutch cursus"]}, FakeLLM([]))
        message = runner.explain_why_no_cards()
        self.assertIn("none are Recognition cards for AllAI", message)


if __name__ == "__main__":
    unittest.main()
