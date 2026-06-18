from __future__ import annotations

from functools import partial
from typing import Any

from aqt.qt import (
    QApplication,
    QDialog,
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
        RoundData,
        SessionRunner,
        build_search_query,
        deep_merge_config,
    )
except ImportError:  # pragma: no cover
    from llm_client import OpenAICompatibleClient
    from session import DEFAULT_CONFIG, LLMUnavailableError, RoundData, SessionRunner, build_search_query, deep_merge_config

FILTERED_DECK_NAME = "AllAI Session"
FILTERED_ORDER_DUE = 6


class WordRowWidget(QWidget):
    def __init__(self, row: Any, on_change: Any) -> None:
        super().__init__()
        self.row = row
        self._on_change = on_change
        self._rating: str | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 6)

        self.target_label = QLabel(row.target)
        self.target_label.setMinimumWidth(120)
        self.target_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.target_label, 1)

        self.reveal_button = QPushButton("Reveal")
        self.reveal_button.clicked.connect(self._reveal)
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

    def _reveal(self) -> None:
        self.reveal_button.hide()
        self.native_label.show()
        for button in self.buttons.values():
            button.setEnabled(True)
        self._on_change()

    def _set_rating(self, rating: str) -> None:
        self._rating = rating
        for name, button in self.buttons.items():
            button.setProperty("allai-active", name == rating)
            button.style().unpolish(button)
            button.style().polish(button)
        if rating in ("again", "hard"):
            self.setStyleSheet("background-color: rgba(192, 57, 43, 0.18); border-radius: 6px;")
        else:
            self.setStyleSheet("background-color: rgba(39, 174, 96, 0.18); border-radius: 6px;")
        self._on_change()

    def is_revealed(self) -> bool:
        return not self.reveal_button.isVisible()


class SessionDialog(QDialog):
    def __init__(self, mw: Any) -> None:
        super().__init__(mw)
        self.mw = mw
        self.config = self._load_config()
        self.previous_deck_id = int(self.mw.col.decks.selected())
        self.session_deck_id: int | None = None
        self._session_cleaned_up = False
        self.runner = SessionRunner(mw.col, self.config, OpenAICompatibleClient.from_config(self.config))
        self.current_round: RoundData | None = None
        self.row_widgets: list[WordRowWidget] = []

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
        existing = self.mw.col.decks.by_name(FILTERED_DECK_NAME)
        if existing is not None and not existing.get("dyn"):
            raise RuntimeError(f'Deck "{FILTERED_DECK_NAME}" already exists and is not filtered.')

        existing_id = int(existing["id"]) if existing is not None else 0
        deck = self.mw.col.sched.get_or_create_filtered_deck(existing_id)
        deck.name = FILTERED_DECK_NAME
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
            widget = WordRowWidget(row, self._update_next_state)
            self.row_widgets.append(widget)
            self.rows_layout.addWidget(widget)
        self.rows_layout.addStretch(1)

    def _update_next_state(self) -> None:
        if not self.row_widgets:
            self.next_button.setEnabled(False)
            return
        ready = all(widget.is_revealed() and widget.rating for widget in self.row_widgets)
        self.next_button.setEnabled(ready)

    def _commit_round(self) -> None:
        if self.current_round is None:
            return
        ratings = {
            widget.row.card_id: widget.rating
            for widget in self.row_widgets
            if widget.rating is not None
        }
        self.runner.commit_round(self.current_round, ratings)
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
        self.mw.reset()

    def reject(self) -> None:
        self._cleanup_session_deck()
        super().reject()
