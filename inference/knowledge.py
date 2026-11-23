"""Persistent documents and local hybrid retrieval."""
import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from threading import local
from zoneinfo import ZoneInfo

import numpy as np
import snowballstemmer

CATEGORIES = {"teacher", "schedule", "announcement", "other"}
MAX_BODY = 24_000
MAX_IMPORT = 200
FIELDS = ("title", "category", "content", "source", "valid_from", "valid_until")
_WORDS = re.compile(r"[a-zа-я0-9]+")
_stemmers = local()


def lex(text):
    words = _WORDS.findall(text.casefold().replace("ё", "е"))
    if not hasattr(_stemmers, "russian"):
        _stemmers.russian = snowballstemmer.stemmer("russian")
    return _stemmers.russian.stemWords(words)


def validate(value):
    if not isinstance(value, dict):
        raise ValueError("Каждая запись должна быть объектом JSON.")
    result = {}
    for field, limit in zip(FIELDS, (180, 20, MAX_BODY, 500, 10, 10)):
        item = value.get(field, "other" if field == "category" else "")
        if not isinstance(item, str) or len(item) > limit:
            raise ValueError(f"Недопустимое поле {field}. Максимум {limit} символов.")
        result[field] = item.strip()
    if not result["title"] or not result["content"]:
        raise ValueError("Заполните заголовок и текст.")
    if result["category"] not in CATEGORIES:
        raise ValueError("Неизвестная категория.")
    for field in ("valid_from", "valid_until"):
        if result[field]:
            try:
                result[field] = date.fromisoformat(result[field]).isoformat()
            except ValueError:
                raise ValueError("Укажите дату в формате ГГГГ-ММ-ДД.") from None
    if result["valid_from"] and result["valid_until"] and result["valid_from"] > result["valid_until"]:
        raise ValueError("Дата окончания раньше даты начала.")
    return result


def chunks(text, length=640, overlap=100):
    start = 0
    while start < len(text):
        end = min(len(text), start + length)
        if end < len(text):
            cut = max(text.rfind("\n", start + length // 2, end), text.rfind(" ", start + length // 2, end))
            if cut > start:
                end = cut
        part = text[start:end].strip()
        if part:
            yield part
        if end == len(text):
            break
        start = end - overlap
        while start < end and not text[start - 1].isspace():
            start += 1


class Knowledge:
    def __init__(self, path, encode, model_id, min_similarity=0.52):
        self.path = path
        self.encode = encode
        self.min_similarity = min_similarity
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS model (name TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY, title TEXT NOT NULL, category TEXT NOT NULL,
                    content TEXT NOT NULL, source TEXT NOT NULL, valid_from TEXT NOT NULL,
                    valid_until TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(category, title)
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL REFERENCES documents(id),
                    text TEXT NOT NULL, vector BLOB NOT NULL
                );
                CREATE INDEX IF NOT EXISTS chunks_document ON chunks(document_id);
                CREATE VIRTUAL TABLE IF NOT EXISTS search_text USING fts5(terms);
            """)
            existing = db.execute("SELECT name FROM model").fetchone()
            if existing and existing["name"] != model_id:
                raise RuntimeError("Версия эмбеддера изменилась. Экспортируйте записи из старой версии и импортируйте в новую БД.")
            if not existing:
                db.execute("INSERT INTO model VALUES (?)", (model_id,))

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def list(self):
        with self.connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM documents ORDER BY category, title")]

    @staticmethod
    def remove_chunks(db, doc_id):
        db.execute("DELETE FROM search_text WHERE rowid IN (SELECT id FROM chunks WHERE document_id=?)", (doc_id,))
        db.execute("DELETE FROM chunks WHERE document_id=?", (doc_id,))

    def delete(self, doc_id):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self.remove_chunks(db, doc_id)
            return db.execute("DELETE FROM documents WHERE id=?", (doc_id,)).rowcount == 1

    def write(self, values, doc_id=None, upsert=False):
        if not isinstance(values, list) or not 1 <= len(values) <= MAX_IMPORT:
            raise ValueError(f"Передайте от 1 до {MAX_IMPORT} записей.")
        entries = [validate(value) for value in values]
        prepared = []
        for entry in entries:
            parts = list(chunks(entry["content"]))
            heading = entry["title"]
            vectors = self.encode([f"{heading}\n{text}" for text in parts])
            prepared.append((entry, list(zip(parts, vectors, strict=True))))

        saved = []
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            for entry, parts in prepared:
                args = tuple(entry[key] for key in FIELDS)
                existing_id = doc_id
                if upsert:
                    row = db.execute("SELECT id FROM documents WHERE category=? AND title=?",
                                     (entry["category"], entry["title"])).fetchone()
                    existing_id = row["id"] if row else None
                if existing_id is None:
                    cursor = db.execute(
                        "INSERT INTO documents(title,category,content,source,valid_from,valid_until) VALUES (?,?,?,?,?,?)", args)
                    existing_id = cursor.lastrowid
                else:
                    cursor = db.execute(
                        """UPDATE documents SET title=?,category=?,content=?,source=?,valid_from=?,valid_until=?,
                           updated_at=CURRENT_TIMESTAMP WHERE id=?""", (*args, existing_id))
                    if not cursor.rowcount:
                        raise KeyError(existing_id)
                    self.remove_chunks(db, existing_id)
                for part, vector in parts:
                    cursor = db.execute("INSERT INTO chunks(document_id,text,vector) VALUES (?,?,?)",
                                        (existing_id, part, np.asarray(vector, dtype="<f4").tobytes()))
                    terms = " ".join(lex(f"{entry['title']} {entry['title']} {part}"))
                    db.execute("INSERT INTO search_text(rowid,terms) VALUES (?,?)", (cursor.lastrowid, terms))
                saved.append(existing_id)
        return saved

    def search(self, query, limit=4, today=None):
        today = today or datetime.now(ZoneInfo("Europe/Moscow")).date().isoformat()
        terms = list(dict.fromkeys(term for term in lex(query) if len(term) >= 2))[:40]
        expression = " OR ".join('"' + term + '"' for term in terms)
        active = "(d.valid_from='' OR d.valid_from<=?) AND (d.valid_until='' OR d.valid_until>=?)"
        with self.connection() as db:
            db.execute("BEGIN")
            rows = db.execute(
                f"""SELECT c.*,d.title,d.category,d.source,d.valid_from,d.valid_until
                    FROM chunks c JOIN documents d ON c.document_id=d.id WHERE {active} ORDER BY c.id""",
                (today, today),
            ).fetchall()
            if not rows:
                return []
            lexical = db.execute(
                f"""SELECT c.id FROM search_text JOIN chunks c ON search_text.rowid=c.id
                    JOIN documents d ON c.document_id=d.id WHERE search_text MATCH ? AND {active}
                    ORDER BY bm25(search_text) LIMIT 16""",
                (expression, today, today),
            ).fetchall() if expression else []

        query_vector = np.asarray(self.encode([query])[0], dtype="<f4")
        vectors = np.stack([np.frombuffer(row["vector"], dtype="<f4") for row in rows])
        scores = vectors @ query_vector
        lexical_rank = {row["id"]: rank for rank, row in enumerate(lexical)}
        order = sorted(range(len(rows)), key=lambda i: (
            float(scores[i]) + 0.18 / (1 + lexical_rank[rows[i]["id"]] / 5)
            if rows[i]["id"] in lexical_rank else float(scores[i])
        ), reverse=True)
        result, seen = [], {}
        for i in order:
            row = rows[i]
            if scores[i] < self.min_similarity and (
                row["id"] not in lexical_rank or scores[i] < self.min_similarity - 0.12
            ):
                continue
            doc = row["document_id"]
            if seen.get(doc, 0) >= 2:
                continue
            seen[doc] = seen.get(doc, 0) + 1
            result.append({key: row[key] for key in ("title", "category", "source", "valid_from", "valid_until", "text")})
            if len(result) == limit:
                break
        return result
