from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

try:  # pragma: no cover - import mode depends on Anki loader vs local tests
    from .note_type import ensure_langcard_notetype
    from .session import EXAMPLE_FIELD, NATIVE_FIELD, NOTE_TYPE_NAME, TARGET_FIELD
except ImportError:  # pragma: no cover
    from note_type import ensure_langcard_notetype
    from session import EXAMPLE_FIELD, NATIVE_FIELD, NOTE_TYPE_NAME, TARGET_FIELD

try:
    from aqt.qt import (
        QCheckBox,
        QComboBox,
        QDialog,
        QDialogButtonBox,
        QFormLayout,
        QLabel,
        QVBoxLayout,
    )
    from aqt.utils import showInfo, showWarning
except ImportError:  # pragma: no cover - allows parser tests
    QCheckBox = None
    QComboBox = None
    QDialog = object
    QDialogButtonBox = None
    QFormLayout = None
    QLabel = None
    QVBoxLayout = None
    showInfo = None
    showWarning = None

MIGRATION_TAG = "allai:migrated"
MODE_COPY = "copy"
MODE_IN_PLACE = "in_place"
_HTML_BREAK_RE = re.compile(r"(?i)<br\s*/?>")


@dataclass
class ParsedPipeNote:
    target: str
    native: str
    example: str


@dataclass
class MigrationResult:
    migrated: int = 0
    skipped: int = 0
    failures: int = 0


def parse_pipe_text(text: str) -> ParsedPipeNote:
    if "|" not in text:
        raise ValueError("Missing pipe delimiter.")
    left, right = text.split("|", 1)
    target = left.strip()
    right = right.strip()
    if not target or not right:
        raise ValueError("Missing target or meaning.")

    native, example = _split_native_and_example(right)
    return ParsedPipeNote(target=target, native=native.strip(), example=example.strip())


def parse_front_back_fields(front: str, back: str) -> ParsedPipeNote:
    target = front.strip()
    if not target:
        raise ValueError("Missing front/target text.")

    native, example = _split_native_and_example(back)
    if not native:
        raise ValueError("Missing back/native text.")
    return ParsedPipeNote(target=target, native=native, example=example)


def extract_langcard_data(note: Any, source_field_name: str) -> ParsedPipeNote:
    raw_value = note[source_field_name]
    if "|" in raw_value:
        return parse_pipe_text(raw_value)

    if source_field_name == "Front" and "Back" in note:
        return parse_front_back_fields(note["Front"], note["Back"])

    raise ValueError("Unsupported note format for migration.")


def _split_trailing_parenthetical(text: str) -> tuple[str, str]:
    if not text.endswith(")"):
        return text, ""

    depth = 0
    for index in range(len(text) - 1, -1, -1):
        char = text[index]
        if char == ")":
            depth += 1
        elif char == "(":
            depth -= 1
            if depth == 0:
                native = text[:index].rstrip()
                example = text[index + 1 : -1].strip()
                if native and example:
                    return native, example
                break
    return text, ""


def _split_native_and_example(text: str) -> tuple[str, str]:
    parts = [part.strip() for part in _HTML_BREAK_RE.split(text) if part.strip()]
    if len(parts) >= 2:
        return parts[0], parts[1]
    return _split_trailing_parenthetical(text)


def ensure_deck_id(col: Any, deck_name: str) -> int:
    existing = col.decks.by_name(deck_name)
    if existing is not None:
        return int(existing["id"])
    return int(col.decks.add_normal_deck_with_name(deck_name).id)


def migrate_notes(
    mw: Any,
    *,
    source_notetype_name: str,
    source_field_name: str,
    destination_deck_name: str,
    mode: str,
    suspend_originals: bool,
) -> MigrationResult:
    result = MigrationResult()
    note_ids = mw.col.find_notes(f'note:"{source_notetype_name}" -tag:{MIGRATION_TAG}')
    langcard_notetype = ensure_langcard_notetype(mw.col)
    destination_deck_id = ensure_deck_id(mw.col, destination_deck_name)

    for note_id in note_ids:
        note = mw.col.get_note(note_id)
        if source_field_name not in note:
            result.skipped += 1
            continue
        try:
            parsed = extract_langcard_data(note, source_field_name)
        except ValueError:
            result.skipped += 1
            continue

        try:
            if mode == MODE_IN_PLACE:
                if not _can_convert_in_place(note):
                    result.skipped += 1
                    continue
                note[TARGET_FIELD] = parsed.target
                note[NATIVE_FIELD] = parsed.native
                note[EXAMPLE_FIELD] = parsed.example
                note.add_tag(MIGRATION_TAG)
                mw.col.update_note(note)
            else:
                _copy_note_to_langcard(
                    mw=mw,
                    source_note=note,
                    langcard_notetype=langcard_notetype,
                    destination_deck_id=destination_deck_id,
                    parsed=parsed,
                    suspend_originals=suspend_originals,
                )
            result.migrated += 1
        except Exception:
            result.failures += 1

    return result


def _can_convert_in_place(note: Any) -> bool:
    return all(field_name in note for field_name in (TARGET_FIELD, NATIVE_FIELD, EXAMPLE_FIELD))


def _copy_note_to_langcard(
    *,
    mw: Any,
    source_note: Any,
    langcard_notetype: dict[str, Any],
    destination_deck_id: int,
    parsed: ParsedPipeNote,
    suspend_originals: bool,
) -> None:
    new_note = mw.col.new_note(langcard_notetype)
    new_note[TARGET_FIELD] = parsed.target
    new_note[NATIVE_FIELD] = parsed.native
    new_note[EXAMPLE_FIELD] = parsed.example
    new_note.add_tag("allai:langcard")
    mw.col.add_note(new_note, destination_deck_id)
    _copy_scheduling(source_note.cards(), new_note.cards(), mw.col)

    source_note.add_tag(MIGRATION_TAG)
    mw.col.update_note(source_note)
    mw.col.update_note(new_note)

    if suspend_originals:
        source_card_ids = [int(card.id) for card in source_note.cards()]
        if source_card_ids:
            mw.col.sched.suspend_cards(source_card_ids)


def _copy_scheduling(source_cards: list[Any], destination_cards: list[Any], col: Any) -> None:
    for source_card, destination_card in zip(source_cards, destination_cards):
        for attribute in (
            "type",
            "queue",
            "due",
            "ivl",
            "factor",
            "reps",
            "lapses",
            "left",
            "flags",
            "original_position",
            "custom_data",
            "memory_state",
            "desired_retention",
            "decay",
            "last_review_time",
        ):
            setattr(destination_card, attribute, getattr(source_card, attribute, getattr(destination_card, attribute, None)))
        col.update_card(destination_card)


class MigrationDialog(QDialog):
    def __init__(self, mw: Any) -> None:
        super().__init__(mw)
        self.mw = mw
        self.setWindowTitle("AllAI Migration")
        self.resize(560, 220)
        self.note_type_combo = QComboBox()
        self.field_combo = QComboBox()
        self.deck_combo = QComboBox()
        self.mode_combo = QComboBox()
        self.suspend_checkbox = QCheckBox("Suspend original cards after migration")
        self.suspend_checkbox.setChecked(True)
        self._build_ui()
        self._populate_note_types()
        self.note_type_combo.currentIndexChanged.connect(self._populate_fields)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Migrate notes into LangCard notes. "
            "Supported formats: `target | native (example)` in one field, or "
            "`Front=target` with `Back=native <br> example`."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        self.mode_combo.addItem("Create LangCard copies and suspend originals", MODE_COPY)
        self.mode_combo.addItem("Convert in place when Target/Native/Example already exist", MODE_IN_PLACE)
        form.addRow("Source note type", self.note_type_combo)
        form.addRow("Source field", self.field_combo)
        form.addRow("Destination deck", self.deck_combo)
        form.addRow("Mode", self.mode_combo)
        layout.addLayout(form)
        layout.addWidget(self.suspend_checkbox)

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(self._run_migration)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _populate_note_types(self) -> None:
        self.note_type_combo.clear()
        for notetype in sorted(self.mw.col.models.all(), key=lambda model: model["name"].casefold()):
            self.note_type_combo.addItem(notetype["name"], notetype["name"])
        self._populate_fields()
        self._populate_decks()

    def _populate_fields(self) -> None:
        self.field_combo.clear()
        notetype_name = self.note_type_combo.currentData()
        if not notetype_name:
            return
        notetype = self.mw.col.models.by_name(notetype_name)
        if not notetype:
            return
        for field in notetype["flds"]:
            self.field_combo.addItem(field["name"], field["name"])

    def _populate_decks(self) -> None:
        self.deck_combo.clear()
        for deck in sorted(self.mw.col.decks.all(), key=lambda deck_: deck_["name"].casefold()):
            self.deck_combo.addItem(deck["name"], deck["name"])

    def _run_migration(self) -> None:
        source_notetype_name = self.note_type_combo.currentData()
        source_field_name = self.field_combo.currentData()
        destination_deck_name = self.deck_combo.currentData()
        mode = self.mode_combo.currentData()
        suspend_originals = self.suspend_checkbox.isChecked()

        if not source_notetype_name or not source_field_name or not destination_deck_name:
            showWarning("Select a source note type, field, and destination deck.")
            return
        if source_notetype_name == NOTE_TYPE_NAME and mode == MODE_COPY:
            showWarning("Source note type is already LangCard.")
            return

        result = migrate_notes(
            self.mw,
            source_notetype_name=source_notetype_name,
            source_field_name=source_field_name,
            destination_deck_name=destination_deck_name,
            mode=mode,
            suspend_originals=suspend_originals,
        )
        showInfo(
            f"Migration finished.\n\nMigrated: {result.migrated}\nSkipped: {result.skipped}\nFailures: {result.failures}"
        )
        self.accept()
