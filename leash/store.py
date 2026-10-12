"""SQLite persistence: mandates and purchases as JSON documents, a hash-chained audit ledger, and a kv table."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional, Type, TypeVar

from pydantic import BaseModel

from .models import Mandate, Purchase

GENESIS = "0" * 64
T = TypeVar("T", bound=BaseModel)


def _canon(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)


class Store:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS docs (kind TEXT NOT NULL, id TEXT NOT NULL, created REAL, updated REAL, parent TEXT,
                                             doc TEXT NOT NULL, PRIMARY KEY (kind, id));
            CREATE INDEX IF NOT EXISTS docs_parent ON docs(kind, parent);
            CREATE TABLE IF NOT EXISTS ledger (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, subject TEXT, actor TEXT NOT NULL,
                kind TEXT NOT NULL, title TEXT NOT NULL, data TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS ledger_subject ON ledger(subject);
            CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL);
            """
        )

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ------------------------------------------------------------------ documents

    def _put(self, kind: str, obj: BaseModel, parent: Optional[str] = None) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO docs(kind, id, created, updated, parent, doc) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(kind, id) DO UPDATE SET updated=excluded.updated, doc=excluded.doc",
                (kind, obj.id, time.time(), time.time(), parent, obj.model_dump_json()),  # type: ignore[attr-defined]
            )

    def _get(self, kind: str, oid: str, cls: Type[T]) -> Optional[T]:
        with self._lock:
            row = self._db.execute("SELECT doc FROM docs WHERE kind=? AND id=?", (kind, oid)).fetchone()
        return cls.model_validate_json(row[0]) if row else None

    def _list(self, kind: str, cls: Type[T], parent: Optional[str] = None, limit: int = 500) -> list[T]:
        q, args = "SELECT doc FROM docs WHERE kind=?", [kind]
        if parent:
            q += " AND parent=?"
            args.append(parent)
        q += " ORDER BY created DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._db.execute(q, args).fetchall()
        return [cls.model_validate_json(r[0]) for r in rows]

    def save_mandate(self, m: Mandate) -> None:
        self._put("mandate", m)

    def mandate(self, mid: str) -> Optional[Mandate]:
        return self._get("mandate", mid, Mandate)

    def mandates(self) -> list[Mandate]:
        return self._list("mandate", Mandate)

    def save_purchase(self, p: Purchase) -> None:
        self._put("purchase", p, parent=p.mandate_id)

    def purchase(self, pid: str) -> Optional[Purchase]:
        return self._get("purchase", pid, Purchase)

    def purchases(self, mandate_id: Optional[str] = None, limit: int = 500) -> list[Purchase]:
        return self._list("purchase", Purchase, parent=mandate_id, limit=limit)

    # ------------------------------------------------------------------ ledger

    def append(self, subject: Optional[str], actor: str, kind: str, title: str, data: Optional[dict] = None) -> dict:
        data = data or {}
        with self._lock:
            row = self._db.execute("SELECT hash FROM ledger ORDER BY seq DESC LIMIT 1").fetchone()
            prev = row[0] if row else GENESIS
            ts = round(time.time(), 6)
            body = {"ts": ts, "subject": subject, "actor": actor, "kind": kind, "title": title, "data": json.loads(_canon(data))}
            h = hashlib.sha256((prev + _canon(body)).encode()).hexdigest()
            cur = self._db.execute(
                "INSERT INTO ledger(ts, subject, actor, kind, title, data, prev_hash, hash) VALUES(?,?,?,?,?,?,?,?)",
                (ts, subject, actor, kind, title, _canon(body["data"]), prev, h),
            )
            return {"seq": cur.lastrowid, **body, "prev_hash": prev, "hash": h}

    def entries(self, subject: Optional[str] = None, after: int = 0, limit: int = 1000, subjects: Optional[list[str]] = None) -> list[dict]:
        q = "SELECT seq, ts, subject, actor, kind, title, data, prev_hash, hash FROM ledger WHERE seq > ?"
        args: list[Any] = [after]
        if subject:
            q += " AND subject = ?"
            args.append(subject)
        elif subjects:
            q += f" AND subject IN ({','.join('?' * len(subjects))})"
            args.extend(subjects)
        q += " ORDER BY seq DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._db.execute(q, args).fetchall()
        return [{"seq": r[0], "ts": r[1], "subject": r[2], "actor": r[3], "kind": r[4], "title": r[5], "data": json.loads(r[6]),
                 "prev_hash": r[7], "hash": r[8]} for r in reversed(rows)]

    def verify_chain(self) -> dict:
        """Recompute every hash. An edited, deleted or reordered row breaks the chain."""
        with self._lock:
            rows = self._db.execute("SELECT seq, ts, subject, actor, kind, title, data, prev_hash, hash FROM ledger ORDER BY seq").fetchall()
        prev = GENESIS
        for r in rows:
            body = {"ts": r[1], "subject": r[2], "actor": r[3], "kind": r[4], "title": r[5], "data": json.loads(r[6])}
            h = hashlib.sha256((prev + _canon(body)).encode()).hexdigest()
            if r[7] != prev or r[8] != h:
                return {"ok": False, "entries": len(rows), "broken_at": r[0]}
            prev = h
        return {"ok": True, "entries": len(rows), "head": prev}

    # ------------------------------------------------------------------ kv

    def kv_get(self, key: str) -> Optional[Any]:
        with self._lock:
            row = self._db.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def kv_set(self, key: str, value: Any) -> None:
        with self._lock:
            self._db.execute("INSERT INTO kv(k, v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (key, _canon(value)))

    def nonce_used(self, nonce: str) -> bool:
        return self.kv_get(f"nonce:{nonce}") is not None

    def use_nonce(self, nonce: str, by: str) -> bool:
        """Atomically mark a nonce as used. Returns False if it was already used (a replay)."""
        with self._lock:
            try:
                self._db.execute("INSERT INTO kv(k, v) VALUES(?,?)", (f"nonce:{nonce}", _canon({"by": by, "at": time.time()})))
                return True
            except sqlite3.IntegrityError:
                return False

    def reset(self) -> None:
        with self._lock:
            self._db.executescript("DELETE FROM docs; DELETE FROM ledger; DELETE FROM kv;")
