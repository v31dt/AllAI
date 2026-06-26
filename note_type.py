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

RECOGNITION_TEMPLATE_NAME = "Recognition"
RECOGNITION_QUESTION_FORMAT = "{{" + TARGET_FIELD + "}}"
RECOGNITION_ANSWER_FORMAT = (
    "{{FrontSide}}<hr id=answer>{{"
    + NATIVE_FIELD
    + "}}<br>{{"
    + EXAMPLE_FIELD
    + "}}"
)
PRODUCTION_TEMPLATE_NAME = "Production"
PRODUCTION_QUESTION_FORMAT = "{{" + NATIVE_FIELD + "}}"
PRODUCTION_ANSWER_FORMAT = (
    "{{FrontSide}}<hr id=answer>{{"
    + TARGET_FIELD
    + "}}<br>{{"
    + EXAMPLE_FIELD
    + "}}"
)

TEMPLATES = {
    RECOGNITION_TEMPLATE_NAME: {
        "qfmt": RECOGNITION_QUESTION_FORMAT,
        "afmt": RECOGNITION_ANSWER_FORMAT,
    },
    PRODUCTION_TEMPLATE_NAME: {
        "qfmt": PRODUCTION_QUESTION_FORMAT,
        "afmt": PRODUCTION_ANSWER_FORMAT,
    },
}


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
    for template_name, formats in TEMPLATES.items():
        template = col.models.new_template(template_name)
        template["qfmt"] = formats["qfmt"]
        template["afmt"] = formats["afmt"]
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
    updated = False

    if len(templates) == 1 and templates[0]["name"] not in TEMPLATES:
        templates[0]["name"] = RECOGNITION_TEMPLATE_NAME
        updated = True

    templates_by_name = {template["name"]: template for template in templates}
    for template_name, formats in TEMPLATES.items():
        template = templates_by_name.get(template_name)
        if template is None:
            template = col.models.new_template(template_name)
            col.models.add_template(notetype, template)
            updated = True
        if template.get("qfmt") != formats["qfmt"]:
            template["qfmt"] = formats["qfmt"]
            updated = True
        if template.get("afmt") != formats["afmt"]:
            template["afmt"] = formats["afmt"]
            updated = True

    return updated
