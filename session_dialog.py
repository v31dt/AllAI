from __future__ import annotations

from functools import partial
from typing import Any

from aqt.qt import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QKeySequence,
    QLabel,
    QPushButton,
    QScrollArea,
    QShortcut,
    QSizePolicy,
    Qt,
    QVBoxLayout,
    QWidget,
)
from aqt.utils import showInfo, showWarning, tooltip

try:  # pragma: no cover - import mode depends on Anki loader vs local tests
    from .llm_client import OpenAICompatibleClient
    from .session import (
        DEFAULT_CONFIG,
        LLMUnavailableError,
        RoundCommitError,
        RoundData,
        SessionRunner,
        build_filtered_deck_searches,
        deep_merge_config,
    )
except ImportError:  # pragma: no cover
    from llm_client import OpenAICompatibleClient
    from session import (
        DEFAULT_CONFIG,
        LLMUnavailableError,
        RoundCommitError,
        RoundData,
        SessionRunner,
        build_filtered_deck_searches,
        deep_merge_config,
    )

FILTERED_DECK_NAME = "AllAI Session"
FILTERED_ORDER_DUE = 6
ALL_CONFIGURED_DECKS = "__all_configured__"
RATING_SHORTCUTS = {
    Qt.Key.Key_1: "again",
    Qt.Key.Key_2: "hard",
    Qt.Key.Key_3: "good",
    Qt.Key.Key_4: "easy",
}
REVEAL_TOGGLE_KEYS = {Qt.Key.Key_Space}
NAVIGATION_KEYS = {
    Qt.Key.Key_Up: -1,
    Qt.Key.Key_Down: 1,
}


def choose_session_deck_name(existing_decks: list[dict[str, Any]], base_name: str = FILTERED_DECK_NAME) -> str:
    decks_by_name = {deck["name"]: deck for deck in existing_decks}
    if base_name not in decks_by_name or decks_by_name[base_name].get("dyn"):
        return base_name
    suffix = 2
    while True:
        candidate = f"{base_name} {suffix}"
        if candidate not in decks_by_name or decks_by_name[candidate].get("dyn"):
            return candidate
        suffix += 1


def rating_for_key(key: int) -> str | None:
    return RATING_SHORTCUTS.get(Qt.Key(key))


def is_reveal_toggle_key(key: int) -> bool:
    return Qt.Key(key) in REVEAL_TOGGLE_KEYS


def active_row_index_for_direction(row_widgets: list[Any], current_index: int | None, direction: int) -> int | None:
    if not row_widgets:
        return None
    if current_index is None:
        return 0 if direction >= 0 else len(row_widgets) - 1
    return max(0, min(len(row_widgets) - 1, current_index + direction))


def choose_next_active_row_index(row_widgets: list[Any], current_index: int | None) -> int | None:
    if not row_widgets:
        return None

    start = 0 if current_index is None else current_index + 1
    for index in range(start, len(row_widgets)):
        widget = row_widgets[index]
        if not widget.is_revealed() or widget.rating is None:
            return index

    for index, widget in enumerate(row_widgets):
        if not widget.is_revealed() or widget.rating is None:
            return index

    return None


class SessionLaunchDialog(QDialog):
    def __init__(self, mw: Any) -> None:
        super().__init__(mw)
        self.mw = mw
        self.config = self._load_config()
        self.deck_combo = QComboBox()
        self.setWindowTitle("Start AllAI Session")
        self.resize(420, 120)
        self._build_ui()

    def _load_config(self) -> dict[str, Any]:
        current = self.mw.addonManager.getConfig(__name__) or {}
        return deep_merge_config(DEFAULT_CONFIG, current)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        form = QFormLayout()
        configured = [deck for deck in self.config.get("decks", []) if deck]
        if len(configured) > 1:
            self.deck_combo.addItem("All configured decks", ALL_CONFIGURED_DECKS)
        for deck_name in configured:
            self.deck_combo.addItem(deck_name, deck_name)
        if not configured:
            for deck in sorted(self.mw.col.decks.all(), key=lambda item: item["name"].casefold()):
                self.deck_combo.addItem(deck["name"], deck["name"])
        form.addRow("Run session for", self.deck_combo)
        layout.addLayout(form)

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def selected_decks(self) -> list[str]:
        value = self.deck_combo.currentData()
        if value == ALL_CONFIGURED_DECKS:
            return list(self.config.get("decks", []))
        return [value] if value else list(self.config.get("decks", []))


class WordRowWidget(QWidget):
    def __init__(self, row: Any, on_change: Any, on_activate: Any) -> None:
        super().__init__()
        self.row = row
        self._on_change = on_change
        self._on_activate = on_activate
        self._rating: str | None = None
        self._active = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 6)

        self.target_label = QLabel(row.prompt_text)
        self.target_label.setMinimumWidth(120)
        self.target_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.target_label, 1)

        self.reveal_button = QPushButton("Reveal")
        self.reveal_button.clicked.connect(self.toggle_reveal)
        layout.addWidget(self.reveal_button)

        self.native_label = QLabel(row.answer_text)
        self.native_label.hide()
        self.native_label.setMinimumWidth(140)
        layout.addWidget(self.native_label)

        self.buttons: dict[str, QPushButton] = {}
        for rating in ("again", "hard", "good", "easy"):
            button = QPushButton(rating.capitalize())
            button.clicked.connect(partial(self._set_rating, rating))
            self.buttons[rating] = button
            layout.addWidget(button)

    @property
    def rating(self) -> str | None:
        return self._rating

    def set_active(self, active: bool) -> None:
        self._active = active
        if active:
            self.setFocus(Qt.FocusReason.OtherFocusReason)
        self._apply_style()

    def toggle_reveal(self) -> None:
        self._on_activate(self)
        revealed = not self.is_revealed()
        self.reveal_button.setText("Hide" if revealed else "Reveal")
        self.native_label.setVisible(revealed)
        self._on_change()

    def _set_rating(self, rating: str) -> None:
        self._on_activate(self)
        self._rating = rating
        for name, button in self.buttons.items():
            button.setProperty("allai-active", name == rating)
            button.style().unpolish(button)
            button.style().polish(button)
        self._apply_style()
        self._on_change()

    def is_revealed(self) -> bool:
        return self.native_label.isVisible()

    def apply_shortcut_rating(self, rating: str) -> bool:
        if rating not in self.buttons:
            return False
        self._set_rating(rating)
        return True

    def toggle_reveal_with_shortcut(self) -> bool:
        self.toggle_reveal()
        return True

    def mousePressEvent(self, event: Any) -> None:
        self._on_activate(self)
        super().mousePressEvent(event)

    def _apply_style(self) -> None:
        background = "transparent"
        if self._rating in ("again", "hard"):
            background = "rgba(192, 57, 43, 0.18)"
        elif self._rating in ("good", "easy"):
            background = "rgba(39, 174, 96, 0.18)"
        border = "2px solid #2d8cff" if self._active else "2px solid transparent"
        self.setStyleSheet(f"background-color: {background}; border-radius: 6px; border: {border};")


class SessionDialog(QDialog):
    def __init__(self, mw: Any, decks_override: list[str] | None = None) -> None:
        super().__init__(mw)
        self.mw = mw
        self.config = self._load_config()
        if decks_override:
            self.config["decks"] = decks_override
        self.previous_deck_id = int(self.mw.col.decks.selected())
        self.session_deck_id: int | None = None
        self._session_cleaned_up = False
        self.runner = SessionRunner(mw.col, self.config, OpenAICompatibleClient.from_config(self.config))
        self.current_round: RoundData | None = None
        self.row_widgets: list[WordRowWidget] = []
        self.active_row_index: int | None = None
        self._shortcuts: list[QShortcut] = []

        self.setWindowTitle("AllAI Session")
        self.resize(880, 520)
        self._build_ui()
        self._setup_shortcuts()
        try:
            self._load_next_round(show_empty_message=True)
        except Exception as exc:
            self._cleanup_session_deck()
            showWarning(str(exc))
            super().reject()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        self.header_label = QLabel("Round 1 · 0 words reviewed")
        root.addWidget(self.header_label)

        self.sentence_label = QLabel("")
        self.sentence_label.setWordWrap(True)
        self.sentence_label.setTextFormat(Qt.TextFormat.RichText)
        self.sentence_label.setStyleSheet("font-size: 22px; padding: 8px 0;")
        # Let the sentence be highlighted with the mouse and copied (Ctrl+C or the
        # right-click menu). Mouse-only so it doesn't swallow the rating/navigation
        # keyboard shortcuts.
        self.sentence_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.sentence_label.setCursor(Qt.CursorShape.IBeamCursor)
        root.addWidget(self.sentence_label)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.rows_container = QWidget()
        self.rows_layout = QVBoxLayout(self.rows_container)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(8)
        scroll.setWidget(self.rows_container)
        root.addWidget(scroll, 1)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.next_button = QPushButton("Next →")
        self.next_button.setEnabled(False)
        self.next_button.clicked.connect(self._commit_round)
        self.exit_button = QPushButton("Exit session")
        self.exit_button.clicked.connect(self._exit_session)
        actions.addWidget(self.next_button)
        actions.addWidget(self.exit_button)
        root.addLayout(actions)

    def _setup_shortcuts(self) -> None:
        self._register_shortcut(QKeySequence("1"), partial(self._rate_active_row, "again"))
        self._register_shortcut(QKeySequence("2"), partial(self._rate_active_row, "hard"))
        self._register_shortcut(QKeySequence("3"), partial(self._rate_active_row, "good"))
        self._register_shortcut(QKeySequence("4"), partial(self._rate_active_row, "easy"))
        self._register_shortcut(QKeySequence(Qt.Key.Key_Space), self._toggle_active_row_reveal)
        self._register_shortcut(QKeySequence(Qt.Key.Key_Up), partial(self._move_active_row, -1))
        self._register_shortcut(QKeySequence(Qt.Key.Key_Down), partial(self._move_active_row, 1))

    def _register_shortcut(self, sequence: QKeySequence, handler: Any) -> None:
        shortcut = QShortcut(sequence, self)
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(handler)
        self._shortcuts.append(shortcut)

    def _load_config(self) -> dict[str, Any]:
        current = self.mw.addonManager.getConfig(__name__) or {}
        return deep_merge_config(DEFAULT_CONFIG, current)

    def _setup_session_deck(self, direction: str) -> None:
        deck_name = choose_session_deck_name(self.mw.col.decks.all())
        existing = self.mw.col.decks.by_name(deck_name)

        existing_id = self.session_deck_id or (int(existing["id"]) if existing is not None else 0)
        deck = self.mw.col.sched.get_or_create_filtered_deck(existing_id)
        deck.name = deck_name
        deck.allow_empty = True
        config = deck.config
        config.reschedule = True
        del config.delays[:]
        del config.search_terms[:]
        search_terms = build_filtered_deck_searches(
            self.config.get("decks", []),
            self.runner.include_new_cards,
            direction,
            self.runner.excluded_card_ids(),
        )
        term_limit = max(1000, int(self.config["session"]["words_per_sentence"]) * 250)
        new_limit = self.runner.new_card_limit()
        for search in search_terms:
            # The new-card term is gathered only up to Anki's remaining daily new
            # allowance, so new words trickle in at Anki's pace instead of flooding.
            is_new_term = search.startswith("is:new")
            term = config.search_terms.add()
            term.search = search
            term.limit = new_limit if is_new_term else term_limit
            term.order = FILTERED_ORDER_DUE
        out = self.mw.col.sched.add_or_update_filtered_deck(deck)
        self.session_deck_id = int(out.id)
        self.mw.col.decks.select(self.session_deck_id)
        self.mw.reset()

    def _prepare_round_skipping_unusable(self) -> RoundData | None:
        # A card Anki puts at the top of the queue might not be turnable into a
        # sentence. Since Anki only lets us answer the top card, we can't reach
        # past it -- so skip it (it stays for normal review), rebuild the pile
        # without it, and try the next card instead of ending the session.
        max_attempts = 200
        for _ in range(max_attempts):
            if self.session_deck_id is not None:
                self.mw.col.sched.empty_filtered_deck(self.session_deck_id)
            direction = self.runner.choose_next_direction()
            if direction is None:
                return None
            skipped_before = len(self.runner.skipped_card_ids)
            self._setup_session_deck(direction)
            round_data = self.runner.prepare_next_round(direction)
            if round_data is not None:
                return round_data
            if len(self.runner.skipped_card_ids) == skipped_before:
                # Failed for a reason that isn't a skippable card (e.g. mixed-ord
                # deck or an empty batch) -- stop rather than spin.
                return None
            # A card was skipped; drop its tooltip noise and try the next one.
            self.runner.drain_messages()
        return None

    def _load_next_round(self, *, show_empty_message: bool) -> None:
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()
        try:
            self.current_round = self._prepare_round_skipping_unusable()
        except LLMUnavailableError as exc:
            self._restore_cursor()
            self._cleanup_session_deck()
            showWarning(str(exc))
            self._open_normal_reviewer()
            super().reject()
            return
        finally:
            self._restore_cursor()

        messages = self.runner.drain_messages()
        for message in messages:
            tooltip(message, parent=self)

        if self.current_round is None:
            self._cleanup_session_deck()
            if self.runner.completed_rounds == 0 and show_empty_message:
                showInfo(messages[-1] if messages else self.runner.explain_why_no_cards())
            else:
                self._show_summary()
            super().accept()
            return

        self._render_round(self.current_round)

    def _render_round(self, round_data: RoundData) -> None:
        self.header_label.setText(
            f"Round {round_data.round_index} · {round_data.direction_label} · "
            f"{round_data.reviewed_words_before_round} words reviewed"
        )
        self.sentence_label.setText(round_data.sentence_html)
        self.status_label.setText("Rate each word. Reveal is optional.")
        self.next_button.setEnabled(False)

        while self.rows_layout.count():
            item = self.rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.row_widgets.clear()

        for row in round_data.rows:
            widget = WordRowWidget(row, self._update_next_state, self._set_active_row)
            self.row_widgets.append(widget)
            self.rows_layout.addWidget(widget)
        self.rows_layout.addStretch(1)
        self._set_active_row_index(0 if self.row_widgets else None)

    def _update_next_state(self) -> None:
        if not self.row_widgets:
            self.next_button.setEnabled(False)
            return
        ready = all(widget.rating for widget in self.row_widgets)
        self.next_button.setEnabled(ready)

    def _set_active_row(self, widget: WordRowWidget) -> None:
        try:
            index = self.row_widgets.index(widget)
        except ValueError:
            return
        self._set_active_row_index(index)

    def _set_active_row_index(self, index: int | None) -> None:
        self.active_row_index = index
        for row_index, widget in enumerate(self.row_widgets):
            widget.set_active(index is not None and row_index == index)

    def _advance_active_row(self) -> None:
        self._set_active_row_index(choose_next_active_row_index(self.row_widgets, self.active_row_index))

    def _rate_active_row(self, rating: str) -> None:
        if self.active_row_index is None:
            return
        widget = self.row_widgets[self.active_row_index]
        if widget.apply_shortcut_rating(rating):
            self._advance_active_row()

    def _toggle_active_row_reveal(self) -> None:
        if self.active_row_index is None:
            return
        self.row_widgets[self.active_row_index].toggle_reveal_with_shortcut()

    def _move_active_row(self, direction: int) -> None:
        self._set_active_row_index(active_row_index_for_direction(self.row_widgets, self.active_row_index, direction))

    def _commit_round(self) -> None:
        if self.current_round is None:
            return
        ratings = {
            widget.row.card_id: widget.rating
            for widget in self.row_widgets
            if widget.rating is not None
        }
        try:
            self.runner.commit_round(self.current_round, ratings)
        except RoundCommitError as exc:
            self.mw.reset()
            self._cleanup_session_deck()
            showWarning(str(exc))
            super().reject()
            return
        self.mw.reset()
        self._load_next_round(show_empty_message=False)

    def _exit_session(self) -> None:
        self._show_summary()
        self.reject()

    def _show_summary(self) -> None:
        if self.runner.reviewed_words == 0:
            return
        showInfo(
            f"{self.runner.reviewed_words} words reviewed across {self.runner.completed_rounds} sentences."
        )

    def _open_normal_reviewer(self) -> None:
        for deck_name in self.config.get("decks", []):
            deck = self.mw.col.decks.by_name(deck_name)
            if deck is None:
                continue
            self.mw.col.decks.select(deck["id"])
            self.mw.onOverview()
            self.mw.moveToState("review")
            return
        if self.mw.state == "overview":
            self.mw.moveToState("review")
        else:
            self.mw.moveToState("deckBrowser")

    @staticmethod
    def _restore_cursor() -> None:
        if QApplication.overrideCursor() is not None:
            QApplication.restoreOverrideCursor()

    def _cleanup_session_deck(self) -> None:
        if self._session_cleaned_up:
            return
        self._session_cleaned_up = True
        if self.session_deck_id is not None:
            self.mw.col.sched.empty_filtered_deck(self.session_deck_id)
        self.mw.col.decks.select(self.previous_deck_id)
        if self.session_deck_id is not None:
            self.mw.col.decks.remove([self.session_deck_id])
        self.mw.reset()

    def reject(self) -> None:
        self._cleanup_session_deck()
        super().reject()
