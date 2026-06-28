from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any, Protocol, Sequence

NOTE_TYPE_NAME = "LangCard"
RECOGNITION_DIRECTION = "recognition"
PRODUCTION_DIRECTION = "production"
CARD_MODE_BOTH = "both"
CARD_MODE_RECOGNITION = RECOGNITION_DIRECTION
CARD_MODE_PRODUCTION = PRODUCTION_DIRECTION
RECOGNITION_CARD_TEMPLATE_NAME = "Recognition"
PRODUCTION_CARD_TEMPLATE_NAME = "Production"
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
        "card_mode": CARD_MODE_BOTH,
        "max_llm_retries": 1,
        "max_skip_attempts_per_card": 3,
    },
}

RECOGNITION_PROMPT_TEMPLATE = """
Generate ONE natural sentence using ALL of these words: {words}.

Rules:
- Maximum 15 words total.
- The entire sentence must be in the same target language as the provided words.
- Inflect, conjugate, or modify words as needed for natural grammar.
- Sound like something a native speaker would actually say in everyday life.
- Use common target-language vocabulary for the connecting words.
- Never switch into English or another language for filler words, grammar, or phrasing.
- Never translate the words into English inside the sentence.
- Only output an English sentence if every provided word is already English.
- Do not translate or explain.

Return ONLY JSON:
{{"sentence": "...", "words_used": [{{"target": "original target 1", "surface": "surface form 1"}}, ...]}}

Rules for words_used:
- target must exactly copy one of the provided words.
- surface must be the actual form that appears in the sentence.

If you cannot fit all words naturally, use the largest natural subset and list
only those in words_used. If you are unsure about the language, prefer a smaller
subset rather than switching languages.
""".strip()

PRODUCTION_PROMPT_TEMPLATE = """
Generate ONE natural English sentence using ALL of these English cues: {words}.

Rules:
- Maximum 15 words total.
- The entire sentence must be in English.
- Sound natural and everyday.
- Use each cue as written when possible; minor grammar changes are allowed.
- Do not translate the cues into another language inside the sentence.
- Do not explain.

Return ONLY JSON:
{{"sentence": "...", "words_used": [{{"target": "original cue 1", "surface": "surface form 1"}}, ...]}}

Rules for words_used:
- target must exactly copy one of the provided cues.
- surface must be the actual form that appears in the sentence.

If you cannot fit all cues naturally, use the largest natural subset and list
only those in words_used.
""".strip()

PROMPT_TEMPLATES = {
    RECOGNITION_DIRECTION: RECOGNITION_PROMPT_TEMPLATE,
    PRODUCTION_DIRECTION: PRODUCTION_PROMPT_TEMPLATE,
}

CARD_TEMPLATE_BY_DIRECTION = {
    RECOGNITION_DIRECTION: RECOGNITION_CARD_TEMPLATE_NAME,
    PRODUCTION_DIRECTION: PRODUCTION_CARD_TEMPLATE_NAME,
}
CARD_ORD_BY_DIRECTION = {
    RECOGNITION_DIRECTION: 0,
    PRODUCTION_DIRECTION: 1,
}
VALID_CARD_MODES = {CARD_MODE_BOTH, CARD_MODE_RECOGNITION, CARD_MODE_PRODUCTION}

_PUNCTUATION_RE = re.compile(r"^[^\w]+|[^\w]+$")
_EXAMPLE_TRANSLATION_RE = re.compile(r'^(?P<source>.+?)\s+[–-]\s+"(?P<translation>.+)"\s*$')
_DUTCH_ARTICLES = {"de", "het", "een"}


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
    note_id: int
    card_ord: int
    target: str
    native: str
    direction: str
    prompt_text: str
    answer_text: str
    example: str
    due: int
    queue: int | None = None
    card_type: int | None = None


@dataclass(frozen=True)
class WordUsage:
    target: str
    surface: str


@dataclass
class RoundRow:
    card_id: int
    target: str
    native: str
    direction: str
    prompt_text: str
    answer_text: str
    example: str
    surface_form: str
    card: Any


@dataclass
class RoundData:
    round_index: int
    reviewed_words_before_round: int
    direction: str
    direction_label: str
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


def normalize_card_mode(value: Any) -> str:
    mode = str(value or CARD_MODE_BOTH).strip().casefold()
    return mode if mode in VALID_CARD_MODES else CARD_MODE_BOTH


def direction_label(direction: str) -> str:
    if direction == PRODUCTION_DIRECTION:
        return "Production"
    return "Recognition"


def configured_directions(card_mode: str) -> list[str]:
    mode = normalize_card_mode(card_mode)
    if mode == CARD_MODE_PRODUCTION:
        return [PRODUCTION_DIRECTION]
    if mode == CARD_MODE_RECOGNITION:
        return [RECOGNITION_DIRECTION]
    return [RECOGNITION_DIRECTION, PRODUCTION_DIRECTION]


def next_direction_order(card_mode: str, last_direction: str | None) -> list[str]:
    directions = configured_directions(card_mode)
    if len(directions) < 2:
        return directions
    if last_direction == RECOGNITION_DIRECTION:
        return [PRODUCTION_DIRECTION, RECOGNITION_DIRECTION]
    if last_direction == PRODUCTION_DIRECTION:
        return [RECOGNITION_DIRECTION, PRODUCTION_DIRECTION]
    return directions


def build_search_query(
    decks: Sequence[str],
    include_new_cards: bool,
    direction: str = RECOGNITION_DIRECTION,
) -> str:
    usable_decks = [deck.strip() for deck in decks if deck.strip()]
    if not usable_decks:
        raise ValueError("At least one deck must be configured.")
    deck_filter = build_deck_filter(usable_decks)
    state = build_state_query(include_new_cards)
    template = CARD_TEMPLATE_BY_DIRECTION.get(direction, RECOGNITION_CARD_TEMPLATE_NAME)
    return f'{state} ({deck_filter}) note:{NOTE_TYPE_NAME} card:"{template}"'


def build_deck_filter(decks: Sequence[str]) -> str:
    usable_decks = [deck.strip() for deck in decks if deck.strip()]
    if not usable_decks:
        raise ValueError("At least one deck must be configured.")
    return " OR ".join(f'deck:"{deck}"' for deck in usable_decks)


def build_state_query(include_new_cards: bool) -> str:
    return "(is:due OR is:new)" if include_new_cards else "(is:due -is:new)"


def build_generation_prompt(words: Sequence[str], direction: str = RECOGNITION_DIRECTION) -> str:
    rendered_words = ", ".join(words)
    template = PROMPT_TEMPLATES.get(direction, RECOGNITION_PROMPT_TEMPLATE)
    return template.format(words=rendered_words)


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
    return _highlight_latin_text(sentence, cleaned_targets)


def _highlight_latin_text(sentence: str, targets: Sequence[str]) -> str:
    spans = _find_latin_highlight_spans(sentence, targets)
    if not spans:
        return html.escape(sentence)
    return _render_highlighted_spans(sentence, spans)


def _find_latin_highlight_spans(sentence: str, targets: Sequence[str]) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    occupied: set[int] = set()
    for term in _highlight_terms(targets):
        pattern = _latin_term_pattern(term)
        if pattern is None:
            continue
        for match in pattern.finditer(sentence):
            start, end = _expand_span_to_adjacent_punctuation(sentence, match.start(), match.end())
            if any(position in occupied for position in range(start, end)):
                continue
            spans.append((start, end))
            occupied.update(range(start, end))
    return sorted(spans)


def _highlight_terms(targets: Sequence[str]) -> list[str]:
    terms: set[str] = set()
    for target in targets:
        stripped = _PUNCTUATION_RE.sub("", target).strip()
        if not stripped:
            continue
        terms.add(stripped)
        parts = stripped.split()
        if len(parts) > 1 and parts[0].casefold() in _DUTCH_ARTICLES:
            terms.add(" ".join(parts[1:]))
    return sorted(terms, key=len, reverse=True)


def _latin_term_pattern(term: str) -> re.Pattern[str] | None:
    parts = term.split()
    if not parts:
        return None
    escaped_parts = [re.escape(part) for part in parts]
    return re.compile(r"(?<!\w)" + r"\s+".join(escaped_parts) + r"(?!\w)", re.IGNORECASE)


def _expand_span_to_adjacent_punctuation(sentence: str, start: int, end: int) -> tuple[int, int]:
    while start > 0 and not sentence[start - 1].isalnum() and not sentence[start - 1].isspace():
        start -= 1
    while end < len(sentence) and not sentence[end].isalnum() and not sentence[end].isspace():
        end += 1
    return start, end


def _render_highlighted_spans(sentence: str, spans: Sequence[tuple[int, int]]) -> str:
    rendered: list[str] = []
    cursor = 0
    for start, end in spans:
        rendered.append(html.escape(sentence[cursor:start]))
        rendered.append(f"<b>{html.escape(sentence[start:end])}</b>")
        cursor = end
    rendered.append(html.escape(sentence[cursor:]))
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
    return _render_highlighted_spans(sentence, sorted(spans))


def build_card_payload(card: Any, direction: str = RECOGNITION_DIRECTION) -> CardPayload:
    note = card.note()
    target = note[TARGET_FIELD].strip()
    native = note[NATIVE_FIELD].strip()
    prompt_text = native if direction == PRODUCTION_DIRECTION else target
    answer_text = target if direction == PRODUCTION_DIRECTION else native
    return CardPayload(
        card=card,
        card_id=int(card.id),
        note_id=int(getattr(card, "nid", 0)),
        card_ord=int(getattr(card, "ord", -1)),
        target=target,
        native=native,
        direction=direction,
        prompt_text=prompt_text,
        answer_text=answer_text,
        example=note[EXAMPLE_FIELD].strip(),
        due=int(getattr(card, "due", 0)),
        queue=int(getattr(card, "queue")) if hasattr(card, "queue") else None,
        card_type=int(getattr(card, "type")) if hasattr(card, "type") else None,
    )


def find_payload_issue(payload: CardPayload) -> str | None:
    target = payload.target.strip()
    native = payload.native.strip()
    if not target:
        return "missing a Target field"

    if normalize_surface_form(target) != normalize_surface_form(native):
        return None

    example = payload.example.strip()
    if not example:
        return f'Target and Native are both "{target}"'

    match = _EXAMPLE_TRANSLATION_RE.match(example)
    if match is None:
        return f'Target and Native are both "{target}"'

    target_token = normalize_surface_form(target)
    source = match.group("source")
    translation = match.group("translation")
    if _contains_normalized_token(translation, target_token) and not _contains_normalized_token(source, target_token):
        return (
            f'Target and Native are both "{target}", and the example sentence uses a different source-language word'
        )
    return f'Target and Native are both "{target}"'


def _contains_normalized_token(text: str, token: str) -> bool:
    if not token:
        return False
    return token in {normalize_surface_form(part) for part in text.split()}


def find_direction_ord_issue(payloads: Sequence[CardPayload], direction: str) -> str | None:
    expected_ord = CARD_ORD_BY_DIRECTION.get(direction)
    if expected_ord is None:
        return None
    unexpected = [payload for payload in payloads if payload.card_ord != expected_ord]
    if not unexpected:
        return None
    return (
        "Stopped because the temporary AllAI deck mixed Recognition and Production cards. "
        "Start a new session so AllAI can rebuild the filtered deck."
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
            direction=payload.direction,
            prompt_text=payload.prompt_text,
            answer_text=payload.answer_text,
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
            if strategy(payload.prompt_text.strip()) == probe:
                return payload
    for strategy in strategies:
        probe = strategy(usage.surface.strip())
        for payload in payloads:
            if strategy(payload.prompt_text.strip()) == probe:
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
        self.card_mode = normalize_card_mode(self.config["session"].get("card_mode"))
        self.last_direction: str | None = None
        self.reviewed_words = 0
        self.completed_rounds = 0
        self.skip_counts: dict[int, int] = {}
        self._messages: list[str] = []

    def choose_next_direction(self) -> str | None:
        for direction in next_direction_order(self.card_mode, self.last_direction):
            if self.count_matching_cards(direction) > 0:
                return direction
        return None

    def count_matching_cards(self, direction: str) -> int:
        query = build_search_query(
            self.config.get("decks", []),
            bool(self.config["session"]["include_new_cards"]),
            direction,
        )
        return len(self.col.find_cards(query))

    def prepare_next_round(self, direction: str | None = None) -> RoundData | None:
        active_direction = direction or self.choose_next_direction()
        if active_direction is None:
            return None

        batch = self._select_batch()
        if not batch:
            return None

        payloads = [build_card_payload(card, active_direction) for card in batch]
        if not payloads:
            return None
        ord_issue = find_direction_ord_issue(payloads, active_direction)
        if ord_issue is not None:
            self._messages.append(ord_issue)
            return None
        issue = find_payload_issue(payloads[0])
        if issue is not None:
            self._messages.append(f"Stopped because the next queued card looks malformed: {issue}.")
            return None

        attempt = self._prepare_attempt(payloads)
        if attempt is None:
            return None

        matched_targets = _highlight_targets_for_rows(attempt.rows)
        self.last_direction = active_direction
        return RoundData(
            round_index=self.completed_rounds + 1,
            reviewed_words_before_round=self.reviewed_words,
            direction=active_direction,
            direction_label=direction_label(active_direction),
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
        any_langcard_query = f"{state_query} ({deck_filter}) note:{NOTE_TYPE_NAME}"
        direction_queries = [
            build_search_query(decks, include_new_cards, direction)
            for direction in configured_directions(self.card_mode)
        ]

        any_matching = len(self.col.find_cards(any_cards_query))
        any_langcard_matching = len(self.col.find_cards(any_langcard_query))
        session_langcard_matching = sum(len(self.col.find_cards(query)) for query in direction_queries)
        if any_matching > 0 and any_langcard_matching == 0:
            return (
                "The configured deck has due/new cards, but none of them use the LangCard note type. "
                "Run Tools -> AllAI -> Migrate notes first, or import cards as LangCard."
            )
        if any_langcard_matching > 0 and session_langcard_matching == 0:
            return (
                "The configured deck has due/new LangCard cards, but none match the selected AllAI card mode. "
                "Switch the AllAI card mode or wait until matching cards are due."
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

    def _generate_sentence_with_retry(self, words: Sequence[str], direction: str) -> dict[str, Any]:
        max_retries = int(self.config["session"]["max_llm_retries"])
        prompt = build_generation_prompt(words, direction)
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
            words = [payload.prompt_text for payload in subset]
            try:
                response = self._generate_sentence_with_retry(words, subset[0].direction)
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


def _highlight_targets_for_rows(rows: Sequence[RoundRow]) -> list[str]:
    targets: list[str] = []
    for row in rows:
        targets.append(row.prompt_text)
        if row.surface_form and normalize_surface_form(row.surface_form) != normalize_surface_form(row.prompt_text):
            targets.append(row.surface_form)
    return targets


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
