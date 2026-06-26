from __future__ import annotations

import unittest

from note_type import ensure_langcard_notetype


class FakeModels:
    def __init__(self, existing: dict | None = None) -> None:
        self.existing = existing
        self.saved = False
        self.added = False

    def by_name(self, name: str) -> dict | None:
        if self.existing and self.existing["name"] == name:
            return self.existing
        return None

    def new(self, name: str) -> dict:
        return {"name": name, "flds": [], "tmpls": [], "css": "", "sortf": 0}

    def new_field(self, name: str) -> dict:
        return {"name": name}

    def add_field(self, notetype: dict, field: dict) -> None:
        notetype["flds"].append(field)

    def new_template(self, name: str) -> dict:
        return {"name": name, "qfmt": "", "afmt": ""}

    def add_template(self, notetype: dict, template: dict) -> None:
        notetype["tmpls"].append(template)

    def add(self, notetype: dict) -> None:
        self.existing = notetype
        self.added = True

    def save(self, notetype: dict) -> None:
        self.existing = notetype
        self.saved = True


class FakeCollection:
    def __init__(self, existing: dict | None = None) -> None:
        self.models = FakeModels(existing)


class NoteTypeTests(unittest.TestCase):
    def test_ensure_langcard_notetype_creates_recognition_and_production_templates(self) -> None:
        col = FakeCollection()
        notetype = ensure_langcard_notetype(col)
        self.assertTrue(col.models.added)
        self.assertEqual([field["name"] for field in notetype["flds"]], ["Target", "Native", "Example"])
        self.assertEqual([template["name"] for template in notetype["tmpls"]], ["Recognition", "Production"])
        self.assertEqual(notetype["tmpls"][0]["qfmt"], "{{Target}}")
        self.assertEqual(notetype["tmpls"][1]["qfmt"], "{{Native}}")
        self.assertIn("{{Target}}", notetype["tmpls"][1]["afmt"])

    def test_ensure_langcard_notetype_adds_production_to_existing_one_way_type(self) -> None:
        existing = {
            "name": "LangCard",
            "flds": [{"name": "Target"}, {"name": "Native"}, {"name": "Example"}],
            "tmpls": [{"name": "Recognition", "qfmt": "{{Target}}", "afmt": "{{FrontSide}}<hr id=answer>{{Native}}<br>{{Example}}"}],
            "css": "",
        }
        col = FakeCollection(existing)
        notetype = ensure_langcard_notetype(col)
        self.assertTrue(col.models.saved)
        self.assertEqual([template["name"] for template in notetype["tmpls"]], ["Recognition", "Production"])
        self.assertEqual(notetype["tmpls"][1]["qfmt"], "{{Native}}")
        self.assertIn("{{Target}}", notetype["tmpls"][1]["afmt"])


if __name__ == "__main__":
    unittest.main()
