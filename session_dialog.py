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
    QLabel,
    QPushButton,
    QScrollArea,
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
        build_search_query,
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
        build_search_query,
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

        self.target_label = QLabel(row.target)
        self.target_label.setMinimumWidth(120)
        self.target_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.target_label, 1)

        self.reveal_button = QPushButton("Reveal")
        self.reveal_button.clicked.connect(self.toggle_reveal)
        layout.addWidget(self.reveal_button)

        self.native_label = QLabel(row.native)
        self.native_label.hide()
        self.native_label.setMinimumWidth(140)
        layout.addWidget(self.native_label)

        self.buttons: dict[str, QPushButton] = {}
        for rating in ("again", "hard", "good", "easy"):
            button = QPushButton(rating.capitalize())
            button.setEnabled(False)
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
        for button in self.buttons.values():
            button.setEnabled(revealed)
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
        return not self.reveal_button.isVisible()

    def apply_shortcut_rating(self, rating: str) -> bool:
        if not self.is_revealed() or rating not in self.buttons:
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

        self.setWindowTitle("AllAI Session")
        self.resize(880, 520)
        self._build_ui()
        try:
            self._setup_session_deck()
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

    def _load_config(self) -> dict[str, Any]:
        current = self.mw.addonManager.getConfig(__name__) or {}
        return deep_merge_config(DEFAULT_CONFIG, current)

    def _setup_session_deck(self) -> None:
        deck_name = choose_session_deck_name(self.mw.col.decks.all())
        existing = self.mw.col.decks.by_name(deck_name)

        existing_id = int(existing["id"]) if existing is not None else 0
        deck = self.mw.col.sched.get_or_create_filtered_deck(existing_id)
        deck.name = deck_name
        deck.allow_empty = True
        config = deck.config
        config.reschedule = True
        del config.delays[:]
        del config.search_terms[:]
        term = config.search_terms.add()
        term.search = build_search_query(
            self.config.get("decks", []),
            bool(self.config["session"]["include_new_cards"]),
        )
        term.limit = max(1000, int(self.config["session"]["words_per_sentence"]) * 250)
        term.order = FILTERED_ORDER_DUE
        out = self.mw.col.sched.add_or_update_filtered_deck(deck)
        self.session_deck_id = int(out.id)
        self.mw.col.decks.select(self.session_deck_id)
        self.mw.reset()

    def _load_next_round(self, *, show_empty_message: bool) -> None:
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()
        try:
            self.current_round = self.runner.prepare_next_round()
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
            f"Round {round_data.round_index} · {round_data.reviewed_words_before_round} words reviewed"
        )
        self.sentence_label.setText(round_data.sentence_html)
        self.status_label.setText("Reveal each word before rating it.")
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
        ready = all(widget.is_revealed() and widget.rating for widget in self.row_widgets)
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

    def keyPressEvent(self, event: Any) -> None:
        direction = NAVIGATION_KEYS.get(Qt.Key(event.key()))
        if direction is not None:
            self._set_active_row_index(active_row_index_for_direction(self.row_widgets, self.active_row_index, direction))
            event.accept()
            return
        if is_reveal_toggle_key(event.key()):
            if self.active_row_index is not None:
                self.row_widgets[self.active_row_index].toggle_reveal_with_shortcut()
            event.accept()
            return
        rating = rating_for_key(event.key())
        if rating is None:
            super().keyPressEvent(event)
            return
        if self.active_row_index is None:
            event.accept()
            return
        widget = self.row_widgets[self.active_row_index]
        if widget.apply_shortcut_rating(rating):
            self._advance_active_row()
        event.accept()

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
