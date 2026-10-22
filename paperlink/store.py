"""Хранилище сырых ответов API: MongoDB (если задан MONGO_URI) или JSON-файлы.

Сырые данные неизменяемы и лежат отдельно от графа, как в PAUK: граф всегда
можно перестроить, не обращаясь к API повторно.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Iterator, Protocol


class RawStore(Protocol):
    def put(self, collection: str, key: str, doc: dict) -> None: ...
    def get(self, collection: str, key: str) -> dict | None: ...
    def all(self, collection: str) -> Iterator[dict]: ...


class JsonStore:
    def __init__(self, root: str | Path = "data/raw"):
        self.root = Path(root)

    def _path(self, collection: str, key: str) -> Path:
        safe = re.sub(r"[^\w.-]+", "_", key)
        return self.root / collection / f"{safe}.json"

    def put(self, collection: str, key: str, doc: dict) -> None:
        path = self._path(collection, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"_id": key, **doc}, ensure_ascii=False, indent=1), encoding="utf-8")

    def get(self, collection: str, key: str) -> dict | None:
        path = self._path(collection, key)
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def all(self, collection: str) -> Iterator[dict]:
        folder = self.root / collection
        if folder.exists():
            for path in sorted(folder.glob("*.json")):
                yield json.loads(path.read_text(encoding="utf-8"))


class MongoStore:
    def __init__(self, uri: str, db: str = "paperlink", client=None):
        if client is None:
            from pymongo import MongoClient

            client = MongoClient(uri, serverSelectionTimeoutMS=3000)
        self.db = client[db]

    def put(self, collection: str, key: str, doc: dict) -> None:
        self.db[collection].replace_one({"_id": key}, {"_id": key, **doc}, upsert=True)

    def get(self, collection: str, key: str) -> dict | None:
        return self.db[collection].find_one({"_id": key})

    def all(self, collection: str) -> Iterator[dict]:
        yield from self.db[collection].find().sort("_id", 1)


def store_from_env() -> RawStore:
    uri = os.environ.get("MONGO_URI")
    if uri:
        return MongoStore(uri, os.environ.get("MONGO_DB", "paperlink"))
    return JsonStore()
