from __future__ import annotations

import html
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
    "decks": [],
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
{{"sentence": "...", "words_used": [{{"target": "original target 1", "surface": "surface form 1"}}, ...]}}

Rules for words_used:
- target must exactly copy one of the provided words.
- surface must be the actual form that appears in the sentence.

If you cannot fit all words naturally, use the largest natural subset and list
only those in words_used.
""".strip()

_PUNCTUATION_RE = re.compile(r"^[^\w]+|[^\w]+$")
_TOKEN_SPLIT_RE = re.compile(r"(\s+)")


class SentenceGenerationError(Exception):
    pass


class LLMUnavailableError(Exception):
    pass


class RoundCommitError(Exception):
    def __init__(self, committed_count: int, rollback_succeeded: bool, original_error: Exception) -> None:
        self.committed_count = committed_count
        self.rollback_succeeded = rollback_succeeded
        self.original_error = original_error

        if committed_count == 0:
            message = (
                "Anki rejected the round before any answers were recorded. "
                f"No reviews from this round were applied. Original error: {original_error}"
            )
        elif rollback_succeeded:
            message = (
                "Anki rejected the round after some answers were applied, but AllAI rolled them back. "
                "No cards from this round were left half-reviewed. Start the session again."
            )
        else:
            message = (
                "Anki rejected the round after some answers were already applied, and automatic rollback failed. "
                "Check the affected cards in normal review before restarting the session. "
                f"Original error: {original_error}"
            )
        super().__init__(message)


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


@dataclass(frozen=True)
class WordUsage:
    target: str
    surface: str


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


@dataclass
class PreparedAttempt:
    sentence: str
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
    deck_filter = build_deck_filter(usable_decks)
    state = build_state_query(include_new_cards)
    return f"{state} ({deck_filter}) note:{NOTE_TYPE_NAME}"


def build_deck_filter(decks: Sequence[str]) -> str:
    usable_decks = [deck.strip() for deck in decks if deck.strip()]
    if not usable_decks:
        raise ValueError("At least one deck must be configured.")
    return " OR ".join(f'deck:"{deck}"' for deck in usable_decks)


def build_state_query(include_new_cards: bool) -> str:
    return "(is:due OR is:new)" if include_new_cards else "(is:due -is:new)"


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
    payloads: Sequence[CardPayload], words_used: Sequence[WordUsage]
) -> tuple[list[RoundRow], list[CardPayload]]:
    remaining = {payload.card_id: payload for payload in payloads}
    matched_surfaces: dict[int, str] = {}
    for usage in words_used:
        match = _find_matching_payload(usage, remaining.values())
        if match is None:
            continue
        matched_surfaces[match.card_id] = usage.surface
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


def _find_matching_payload(usage: WordUsage, payloads: Sequence[CardPayload]) -> CardPayload | None:
    strategies = (
        lambda value: value,
        lambda value: value.casefold(),
        normalize_surface_form,
    )
    for strategy in strategies:
        probe = strategy(usage.target.strip())
        for payload in payloads:
            if strategy(payload.target.strip()) == probe:
                return payload
    for strategy in strategies:
        probe = strategy(usage.surface.strip())
        for payload in payloads:
            if strategy(payload.target.strip()) == probe:
                return payload
    return None


def parse_generation_payload(payload: dict[str, Any]) -> tuple[str, list[WordUsage]]:
    sentence = payload.get("sentence")
    words_used = payload.get("words_used")
    if not isinstance(sentence, str) or not sentence.strip():
        raise SentenceGenerationError("LLM response is missing a sentence.")
    if not isinstance(words_used, list):
        raise SentenceGenerationError("LLM response is missing words_used.")
    usages: list[WordUsage] = []
    for item in words_used:
        if isinstance(item, str):
            surface = item.strip()
            if surface:
                usages.append(WordUsage(target=surface, surface=surface))
            continue
        if not isinstance(item, dict):
            raise SentenceGenerationError("LLM response has an invalid words_used entry.")
        target = item.get("target")
        surface = item.get("surface")
        if not isinstance(target, str) or not target.strip():
            raise SentenceGenerationError("LLM response has a words_used entry without a target.")
        if not isinstance(surface, str) or not surface.strip():
            raise SentenceGenerationError("LLM response has a words_used entry without a surface form.")
        usages.append(WordUsage(target=target.strip(), surface=surface.strip()))
    return sentence.strip(), usages


class SessionRunner:
    def __init__(self, col: Any, config: dict[str, Any], llm_client: SentenceGenerator) -> None:
        self.col = col
        self.config = deep_merge_config(DEFAULT_CONFIG, config)
        self.llm_client = llm_client
        self.reviewed_words = 0
        self.completed_rounds = 0
        self.skip_counts: dict[int, int] = {}
        self._messages: list[str] = []

    def prepare_next_round(self) -> RoundData | None:
        batch = self._select_batch()
        if not batch:
            return None

        payloads = [build_card_payload(card) for card in batch]
        if not payloads or not all(payload.target for payload in payloads):
            self._messages.append("Stopped because the next queued card is missing a Target field.")
            return None

        attempt = self._prepare_attempt(payloads)
        if attempt is None:
            return None

        matched_targets = [row.surface_form or row.target for row in attempt.rows]
        return RoundData(
            round_index=self.completed_rounds + 1,
            reviewed_words_before_round=self.reviewed_words,
            sentence=attempt.sentence,
            sentence_html=highlight_sentence(attempt.sentence, matched_targets),
            rows=attempt.rows,
        )

    def commit_round(self, round_data: RoundData, ratings: dict[int, str]) -> None:
        missing = [row.card_id for row in round_data.rows if row.card_id not in ratings]
        if missing:
            raise ValueError("All words in the round must be rated before commit.")

        undo_entry = None
        if hasattr(self.col, "add_custom_undo_entry") and hasattr(self.col, "merge_undo_entries"):
            undo_entry = self.col.add_custom_undo_entry("AllAI round")

        committed_count = 0
        for row in round_data.rows:
            rating = ratings[row.card_id]
            if rating not in EASE:
                raise ValueError(f"Unknown rating: {rating}")
            try:
                self.col.sched.answerCard(row.card, EASE[rating])
                committed_count += 1
                if undo_entry is not None:
                    self.col.merge_undo_entries(undo_entry)
            except Exception as exc:
                rollback_succeeded = False
                if undo_entry is not None and committed_count > 0 and hasattr(self.col, "undo"):
                    try:
                        self.col.undo()
                        rollback_succeeded = True
                        committed_count = 0
                    except Exception:
                        rollback_succeeded = False
                raise RoundCommitError(
                    committed_count=committed_count,
                    rollback_succeeded=rollback_succeeded,
                    original_error=exc,
                ) from exc

        self.reviewed_words += len(round_data.rows)
        self.completed_rounds += 1

    def drain_messages(self) -> list[str]:
        messages = list(self._messages)
        self._messages.clear()
        return messages

    def explain_why_no_cards(self) -> str:
        decks = self.config.get("decks", [])
        include_new_cards = bool(self.config["session"]["include_new_cards"])
        state_query = build_state_query(include_new_cards)
        deck_filter = build_deck_filter(decks)
        any_cards_query = f"{state_query} ({deck_filter})"
        langcard_query = f"{state_query} ({deck_filter}) note:{NOTE_TYPE_NAME}"

        any_matching = len(self.col.find_cards(any_cards_query))
        langcard_matching = len(self.col.find_cards(langcard_query))
        if any_matching > 0 and langcard_matching == 0:
            return (
                "The configured deck has due/new cards, but none of them use the LangCard note type. "
                "Run Tools -> AllAI -> Migrate notes first, or import cards as LangCard."
            )
        return "Nothing due."

    def _select_batch(self) -> list[Any]:
        batch_size = int(self.config["session"]["words_per_sentence"])
        queued = self.col.sched.get_queued_cards(fetch_limit=batch_size)
        cards: list[Any] = []
        for queued_card in queued.cards:
            card_id = int(queued_card.card.id)
            card = self.col.get_card(card_id)
            if hasattr(card, "start_timer"):
                card.start_timer()
            cards.append(card)
        return cards

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

    def _prepare_attempt(self, payloads: Sequence[CardPayload]) -> PreparedAttempt | None:
        for size in range(len(payloads), 0, -1):
            subset = list(payloads[:size])
            words = [payload.target for payload in subset]
            try:
                response = self._generate_sentence_with_retry(words)
            except SentenceGenerationError:
                continue
            sentence, words_used = parse_generation_payload(response)
            rows, _ = match_words_to_payloads(subset, words_used)
            schedulable_rows = _schedulable_prefix_rows(subset, rows)
            if len(schedulable_rows) != size:
                continue
            if size < len(payloads):
                self._messages.append(
                    f"Reduced this round to {size} word{'s' if size != 1 else ''} to keep Anki queue order valid."
                )
            return PreparedAttempt(sentence=sentence, rows=schedulable_rows)

        lead_card = payloads[0]
        current = self.skip_counts.get(lead_card.card_id, 0) + 1
        self.skip_counts[lead_card.card_id] = current
        limit = int(self.config["session"]["max_skip_attempts_per_card"])
        if current >= limit:
            self._messages.append(
                "Stopped because the next queued card could not be turned into a valid sentence. "
                "Finish it in normal review, then start a new session."
            )
        else:
            self._messages.append(
                "Could not generate a schedulable sentence for the next queued card. "
                "Try starting the session again or review that card normally first."
            )
        return None


def _schedulable_prefix_rows(
    payloads: Sequence[CardPayload], rows: Sequence[RoundRow]
) -> list[RoundRow]:
    rows_by_card_id = {row.card_id: row for row in rows}
    prefix: list[RoundRow] = []
    for payload in payloads:
        row = rows_by_card_id.get(payload.card_id)
        if row is None:
            break
        prefix.append(row)
    return prefix
