import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / path) for path in ("inference", "agent")]
from knowledge import Knowledge, chunks
from department import MAX_ANSWER, brief, build_messages, search_query


def vectorize(texts):
    # Deterministic shape for exercising retrieval and the database without weights.
    return np.array([[1, 0] if "петров" in text.casefold() else [0, 1] for text in texts], dtype=np.float32)


def doc(title, content="Справочный текст", category="teacher", **fields):
    return dict(title=title, content=content, category=category, **fields)


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "rag.sqlite"
        self.db = Knowledge(self.path, vectorize, "test")

    def test_insert_update_import_and_persistence(self):
        record = doc("Петров")
        saved, = self.db.write([record])
        self.assertEqual(self.db.search("О Петрове")[0]["title"], "Петров")
        self.assertEqual(self.db.write([dict(record, content="Новый кабинет")], upsert=True), [saved])
        reopened = Knowledge(self.path, vectorize, "test")
        self.assertEqual(reopened.search("Петров")[0]["text"], "Новый кабинет")
        reopened.write([doc("Сидоров")], doc_id=saved)
        self.assertEqual(reopened.search("Петров"), [])
        self.assertTrue(reopened.delete(saved))
        self.assertEqual(reopened.list(), [])

    def test_expired_documents_and_bad_bulk_import(self):
        with self.assertRaises(ValueError):
            self.db.write([doc("Петров"), doc("Сидоров", valid_from="2026-12-08", valid_until="2026-12-01")], upsert=True)
        self.assertEqual(self.db.list(), [])
        self.db.write([doc("Петров", valid_from="2026-09-01", valid_until="2026-11-15")])
        self.assertTrue(self.db.search("Петров", today="2026-11-15"))
        self.assertEqual(self.db.search("Петров", today="2026-11-16"), [])

    def test_multi_chunk_body_and_chunk_replacement(self):
        text = "Первые данные. " + "одна строка текста " * 75 + "Последняя строка."
        self.assertGreater(len(list(chunks(text))), 1)
        entry, = self.db.write([doc("Петров", text)])
        self.assertLessEqual(len(self.db.search("Петров")), 2)
        self.db.write([doc("Петров", "Новый текст")], doc_id=entry)
        self.assertEqual([row["text"] for row in self.db.search("Петров")], ["Новый текст"])

    def test_model_change_is_reported(self):
        with self.assertRaises(RuntimeError):
            Knowledge(self.path, vectorize, "changed")


class ReplyTests(unittest.TestCase):
    def test_reply_has_no_markup_or_internal_text(self):
        self.assertEqual(brief("<think>Скрытый текст</think> **Кабинет 315.**"), "Кабинет 315.")
        self.assertLessEqual(len(brief("Длинное слово " * 80)), MAX_ANSWER)
        self.assertEqual(brief("Открываемся в 9:00. Следующая фраза", hit_token_limit=True),
                         "Открываемся в 9:00.")

    def test_query_and_sources_are_included(self):
        history = [("user", "Кто такой Петров?"), ("assistant", "Преподаватель."), ("user", "А где его найти?")]
        self.assertIn("Петров", search_query(history))
        messages = build_messages(history, [{"title": "Петров", "text": "Аудитория 312"}])
        self.assertIn("Аудитория 312", messages[0]["content"])
        self.assertEqual(messages[-1]["content"], history[-1][1])
