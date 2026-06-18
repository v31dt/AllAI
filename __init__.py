from __future__ import annotations

try:
    from aqt import gui_hooks, mw
    from aqt.qt import QAction, QMenu, qconnect
except ImportError:  # pragma: no cover - allows pure-Python tests
    gui_hooks = None
    mw = None
    QAction = None
    QMenu = None
    qconnect = None

try:  # pragma: no cover - import mode depends on Anki loader vs local tests
    from .migration import MigrationDialog
    from .note_type import ensure_langcard_notetype
    from .session_dialog import SessionDialog, SessionLaunchDialog
except ImportError:  # pragma: no cover
    from migration import MigrationDialog
    from note_type import ensure_langcard_notetype
    from session_dialog import SessionDialog, SessionLaunchDialog

MENU_TITLE = "AllAI"


def _show_session_dialog() -> None:
    if mw is None or mw.col is None:
        return
    launch = SessionLaunchDialog(mw)
    if launch.exec() != 1:
        return
    dialog = SessionDialog(mw, decks_override=launch.selected_decks())
    dialog.exec()


def _show_migration_dialog() -> None:
    if mw is None or mw.col is None:
        return
    dialog = MigrationDialog(mw)
    dialog.exec()


def _register_menu() -> None:
    if mw is None or getattr(mw, "form", None) is None:
        return

    existing_menu = getattr(mw, "_allai_menu", None)
    if existing_menu is not None:
        return

    menu = QMenu(MENU_TITLE, mw)
    start_action = QAction("Start session", mw)
    migrate_action = QAction("Migrate notes", mw)
    qconnect(start_action.triggered, _show_session_dialog)
    qconnect(migrate_action.triggered, _show_migration_dialog)
    menu.addAction(start_action)
    menu.addAction(migrate_action)
    mw.form.menuTools.addMenu(menu)
    mw._allai_menu = menu


def _on_profile_open() -> None:
    if mw is None or mw.col is None:
        return
    ensure_langcard_notetype(mw.col)


if gui_hooks is not None:
    gui_hooks.main_window_did_init.append(_register_menu)
    gui_hooks.profile_did_open.append(_on_profile_open)
