from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from typing import Any, Protocol, Sequence

NOTE_TYPE_NAME = "LangCard"
TARGET_FIELD = "Target"
NATIVE_FIELD = "Native"
EXAMPLE_FIELD = "Example"

EASE = {"again": 1, "hard": 2, "good": 3, "easy": 4}

DEFAULT_CONFIG: dict[str, Any] = {
    "llm": {
        "base_url": "https://api.openai.com/v1",
        "api_key": "",
        "model": "gpt-4o-mini",
    },
    "decks": ["Dutch"],
    "session": {
        "words_per_sentence": 4,
        "include_new_cards": True,
        "max_llm_retries": 1,
        "max_skip_attempts_per_card": 3,
    },
}

PROMPT_TEMPLATE = """
Generate ONE natural sentence using ALL of these words: {words}.

Rules:
- Maximum 15 words total.
- Inflect, conjugate, or modify words as needed for natural grammar.
- Sound like something a native speaker would actually say in everyday life.
- Use common vocabulary for the connecting words.
- Do not translate or explain.

Return ONLY JSON:
{{"sentence": "...", "words_used": ["surface form 1", "surface form 2", ...]}}

If you cannot fit all words naturally, use the largest natural subset and list
only those in words_used.
""".strip()

_PUNCTUATION_RE = re.compile(r"^[^\w]+|[^\w]+$")
_TOKEN_SPLIT_RE = re.compile(r"(\s+)")


class SentenceGenerationError(Exception):
    pass


class LLMUnavailableError(Exception):
    pass


class SentenceGenerator(Protocol):
    def generate_sentence(self, prompt: str) -> dict[str, Any]:
        ...


@dataclass
class CardPayload:
    card: Any
    card_id: int
    target: str
    native: str
    example: str
    due: int


@dataclass
class RoundRow:
    card_id: int
    target: str
    native: str
    example: str
    surface_form: str
    card: Any


@dataclass
class RoundData:
    round_index: int
    reviewed_words_before_round: int
    sentence: str
    sentence_html: str
    rows: list[RoundRow]


def deep_merge_config(defaults: dict[str, Any], overrides: dict[str, Any] | None) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    source = overrides or {}
    for key, value in defaults.items():
        if isinstance(value, dict):
            merged[key] = deep_merge_config(value, source.get(key))
        elif key in source:
            merged[key] = source[key]
        else:
            merged[key] = value
    for key, value in source.items():
        if key not in merged:
            merged[key] = value
    return merged


def build_search_query(decks: Sequence[str], include_new_cards: bool) -> str:
    usable_decks = [deck.strip() for deck in decks if deck.strip()]
    if not usable_decks:
        raise ValueError("At least one deck must be configured.")
    deck_filter = " OR ".join(f'deck:"{deck}"' for deck in usable_decks)
    state = "(is:due OR is:new)" if include_new_cards else "(is:due -is:new)"
    return f"{state} ({deck_filter}) note:{NOTE_TYPE_NAME}"


def build_generation_prompt(words: Sequence[str]) -> str:
    rendered_words = ", ".join(words)
    return PROMPT_TEMPLATE.format(words=rendered_words)


def normalize_surface_form(value: str) -> str:
    return _PUNCTUATION_RE.sub("", value).casefold().strip()


def contains_cjk(text: str) -> bool:
    return any(
        (
            "\u3400" <= char <= "\u4dbf"
            or "\u4e00" <= char <= "\u9fff"
            or "\u3000" <= char <= "\u303f"
            or "\uf900" <= char <= "\ufaff"
        )
        for char in text
    )


def highlight_sentence(sentence: str, targets: Sequence[str]) -> str:
    if not sentence:
        return ""

    cleaned_targets = [target for target in targets if target]
    if not cleaned_targets:
        return html.escape(sentence)

    if contains_cjk(cleaned_targets[0]):
        return _highlight_cjk(sentence, cleaned_targets)
    return _highlight_whitespace_tokens(sentence, cleaned_targets)


def _highlight_whitespace_tokens(sentence: str, targets: Sequence[str]) -> str:
    normalized_targets = {normalize_surface_form(target) for target in targets}
    parts = _TOKEN_SPLIT_RE.split(sentence)
    rendered: list[str] = []
    for part in parts:
        escaped = html.escape(part)
        if not part or part.isspace():
            rendered.append(escaped)
            continue
        if normalize_surface_form(part) in normalized_targets:
            rendered.append(f"<b>{escaped}</b>")
        else:
            rendered.append(escaped)
    return "".join(rendered)


def _highlight_cjk(sentence: str, targets: Sequence[str]) -> str:
    spans: list[tuple[int, int]] = []
    occupied: set[int] = set()
    for target in sorted(set(targets), key=len, reverse=True):
        start = 0
        while True:
            index = sentence.find(target, start)
            if index == -1:
                break
            end = index + len(target)
            if not any(position in occupied for position in range(index, end)):
                spans.append((index, end))
                occupied.update(range(index, end))
            start = index + 1
    starts = {start for start, _ in spans}
    ends = {end for _, end in spans}
    rendered: list[str] = []
    for index, char in enumerate(sentence):
        if index in starts:
            rendered.append("<b>")
        rendered.append(html.escape(char))
        if index + 1 in ends:
            rendered.append("</b>")
    return "".join(rendered)


def build_card_payload(card: Any) -> CardPayload:
    note = card.note()
    return CardPayload(
        card=card,
        card_id=int(card.id),
        target=note[TARGET_FIELD].strip(),
        native=note[NATIVE_FIELD].strip(),
        example=note[EXAMPLE_FIELD].strip(),
        due=int(getattr(card, "due", 0)),
    )


def match_words_to_payloads(
    payloads: Sequence[CardPayload], words_used: Sequence[str]
) -> tuple[list[RoundRow], list[CardPayload]]:
    remaining = {payload.card_id: payload for payload in payloads}
    matched_surfaces: dict[int, str] = {}
    for surface in words_used:
        match = _find_matching_payload(surface, remaining.values())
        if match is None:
            continue
        matched_surfaces[match.card_id] = surface
        remaining.pop(match.card_id, None)
    rows = [
        RoundRow(
            card_id=payload.card_id,
            target=payload.target,
            native=payload.native,
            example=payload.example,
            surface_form=matched_surfaces[payload.card_id],
            card=payload.card,
        )
        for payload in payloads
        if payload.card_id in matched_surfaces
    ]
    return rows, list(remaining.values())


def _find_matching_payload(surface: str, payloads: Sequence[CardPayload]) -> CardPayload | None:
    strategies = (
        lambda value: value,
        lambda value: value.casefold(),
        normalize_surface_form,
    )
    for strategy in strategies:
        probe = strategy(surface.strip())
        for payload in payloads:
            if strategy(payload.target.strip()) == probe:
                return payload
    return None


def parse_generation_payload(payload: dict[str, Any]) -> tuple[str, list[str]]:
    sentence = payload.get("sentence")
    words_used = payload.get("words_used")
    if not isinstance(sentence, str) or not sentence.strip():
        raise SentenceGenerationError("LLM response is missing a sentence.")
    if not isinstance(words_used, list) or not all(isinstance(item, str) for item in words_used):
        raise SentenceGenerationError("LLM response is missing words_used.")
    return sentence.strip(), [item.strip() for item in words_used if item.strip()]


class SessionRunner:
    def __init__(self, col: Any, config: dict[str, Any], llm_client: SentenceGenerator) -> None:
        self.col = col
        self.config = deep_merge_config(DEFAULT_CONFIG, config)
        self.llm_client = llm_client
        self.reviewed_words = 0
        self.completed_rounds = 0
        self.deferred_card_ids: set[int] = set()
        self.dropped_card_ids: set[int] = set()
        self.skip_counts: dict[int, int] = {}
        self._messages: list[str] = []

    def prepare_next_round(self) -> RoundData | None:
        while True:
            batch = self._select_batch()
            if not batch:
                return None

            payloads = [build_card_payload(card) for card in batch]
            words = [payload.target for payload in payloads if payload.target]
            if not words:
                self._defer_cards(batch)
                self._messages.append("Skipped an empty batch.")
                continue

            for payload in payloads:
                if hasattr(payload.card, "start_timer"):
                    payload.card.start_timer()

            try:
                response = self._generate_sentence_with_retry(words)
            except SentenceGenerationError:
                self._defer_cards(batch)
                continue
            sentence, words_used = parse_generation_payload(response)
            rows, unmatched = match_words_to_payloads(payloads, words_used)
            self._register_unmatched(unmatched)
            if not rows:
                self._messages.append("Skipped a batch because no generated words could be matched.")
                continue

            matched_targets = [row.surface_form or row.target for row in rows]
            self._clear_deferred([row.card_id for row in rows])
            return RoundData(
                round_index=self.completed_rounds + 1,
                reviewed_words_before_round=self.reviewed_words,
                sentence=sentence,
                sentence_html=highlight_sentence(sentence, matched_targets),
                rows=rows,
            )

    def commit_round(self, round_data: RoundData, ratings: dict[int, str]) -> None:
        missing = [row.card_id for row in round_data.rows if row.card_id not in ratings]
        if missing:
            raise ValueError("All words in the round must be rated before commit.")

        for row in round_data.rows:
            rating = ratings[row.card_id]
            if rating not in EASE:
                raise ValueError(f"Unknown rating: {rating}")
            self.col.sched.answerCard(row.card, EASE[rating])

        self.reviewed_words += len(round_data.rows)
        self.completed_rounds += 1
        self._clear_deferred([row.card_id for row in round_data.rows])

    def drain_messages(self) -> list[str]:
        messages = list(self._messages)
        self._messages.clear()
        return messages

    def _select_batch(self) -> list[Any]:
        batch_size = int(self.config["session"]["words_per_sentence"])
        include_new_cards = bool(self.config["session"]["include_new_cards"])
        decks = self.config.get("decks", [])
        query = build_search_query(decks, include_new_cards)

        cards = self._query_cards(query, batch_size)
        if cards:
            return cards
        if self.deferred_card_ids:
            self.deferred_card_ids.clear()
            return self._query_cards(query, batch_size)
        return []

    def _query_cards(self, query: str, batch_size: int) -> list[Any]:
        card_ids = self.col.find_cards(query)
        cards = []
        for card_id in card_ids:
            if int(card_id) in self.deferred_card_ids or int(card_id) in self.dropped_card_ids:
                continue
            cards.append(self.col.get_card(card_id))
        cards.sort(key=lambda card: int(getattr(card, "due", 0)))
        return cards[:batch_size]

    def _generate_sentence_with_retry(self, words: Sequence[str]) -> dict[str, Any]:
        max_retries = int(self.config["session"]["max_llm_retries"])
        prompt = build_generation_prompt(words)
        attempts = max_retries + 1
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                return self.llm_client.generate_sentence(prompt)
            except LLMUnavailableError:
                raise
            except SentenceGenerationError as exc:
                last_error = exc
                if attempt == attempts - 1:
                    break
        self._messages.append("Skipped a batch after malformed LLM output.")
        if last_error is None:
            raise SentenceGenerationError("No LLM response was produced.")
        raise last_error

    def _register_unmatched(self, unmatched: Sequence[CardPayload]) -> None:
        limit = int(self.config["session"]["max_skip_attempts_per_card"])
        for payload in unmatched:
            current = self.skip_counts.get(payload.card_id, 0) + 1
            self.skip_counts[payload.card_id] = current
            if current >= limit:
                self.dropped_card_ids.add(payload.card_id)
                self.deferred_card_ids.discard(payload.card_id)
            else:
                self.deferred_card_ids.add(payload.card_id)

    def _defer_cards(self, cards: Sequence[Any]) -> None:
        for card in cards:
            self.deferred_card_ids.add(int(card.id))

    def _clear_deferred(self, card_ids: Sequence[int]) -> None:
        for card_id in card_ids:
            self.deferred_card_ids.discard(int(card_id))
            self.dropped_card_ids.discard(int(card_id))
