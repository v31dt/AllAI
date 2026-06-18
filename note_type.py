from __future__ import annotations

from typing import Any

try:  # pragma: no cover - import mode depends on Anki loader vs local tests
    from .session import EXAMPLE_FIELD, NATIVE_FIELD, NOTE_TYPE_NAME, TARGET_FIELD
except ImportError:  # pragma: no cover
    from session import EXAMPLE_FIELD, NATIVE_FIELD, NOTE_TYPE_NAME, TARGET_FIELD

NOTE_TYPE_CSS = """
.card {
  font-family: Arial, sans-serif;
  font-size: 24px;
  text-align: center;
  color: black;
  background: white;
}
""".strip()

TEMPLATE_NAME = "Recognition"
QUESTION_FORMAT = "{{" + TARGET_FIELD + "}}"
ANSWER_FORMAT = (
    "{{FrontSide}}<hr id=answer>{{"
    + NATIVE_FIELD
    + "}}<br>{{"
    + EXAMPLE_FIELD
    + "}}"
)


def ensure_langcard_notetype(col: Any) -> dict[str, Any]:
    existing = col.models.by_name(NOTE_TYPE_NAME)
    if existing is None:
        return _create_langcard_notetype(col)
    updated = False
    updated |= _ensure_fields(col, existing)
    updated |= _ensure_template(col, existing)
    if existing.get("css", "").strip() != NOTE_TYPE_CSS:
        existing["css"] = NOTE_TYPE_CSS
        updated = True
    if updated:
        col.models.save(existing)
    return existing


def _create_langcard_notetype(col: Any) -> dict[str, Any]:
    notetype = col.models.new(NOTE_TYPE_NAME)
    notetype["id"] = 0
    notetype["flds"] = []
    notetype["tmpls"] = []
    for field_name in (TARGET_FIELD, NATIVE_FIELD, EXAMPLE_FIELD):
        col.models.add_field(notetype, col.models.new_field(field_name))
    template = col.models.new_template(TEMPLATE_NAME)
    template["qfmt"] = QUESTION_FORMAT
    template["afmt"] = ANSWER_FORMAT
    col.models.add_template(notetype, template)
    notetype["css"] = NOTE_TYPE_CSS
    notetype["sortf"] = 0
    col.models.add(notetype)
    return notetype


def _ensure_fields(col: Any, notetype: dict[str, Any]) -> bool:
    field_names = {field["name"] for field in notetype["flds"]}
    updated = False
    for field_name in (TARGET_FIELD, NATIVE_FIELD, EXAMPLE_FIELD):
        if field_name in field_names:
            continue
        col.models.add_field(notetype, col.models.new_field(field_name))
        updated = True
    return updated


def _ensure_template(col: Any, notetype: dict[str, Any]) -> bool:
    templates = notetype["tmpls"]
    for template in templates:
        if template["name"] == TEMPLATE_NAME:
            updated = False
            if template.get("qfmt") != QUESTION_FORMAT:
                template["qfmt"] = QUESTION_FORMAT
                updated = True
            if template.get("afmt") != ANSWER_FORMAT:
                template["afmt"] = ANSWER_FORMAT
                updated = True
            return updated

    if len(templates) == 1:
        templates[0]["name"] = TEMPLATE_NAME
        templates[0]["qfmt"] = QUESTION_FORMAT
        templates[0]["afmt"] = ANSWER_FORMAT
        return True

    template = col.models.new_template(TEMPLATE_NAME)
    template["qfmt"] = QUESTION_FORMAT
    template["afmt"] = ANSWER_FORMAT
    col.models.add_template(notetype, template)
    return True
