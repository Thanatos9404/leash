"""SQLite persistence: deals (as JSON documents), a hash-chained audit ledger, and a small key-value table."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

from .models import Deal

GENESIS = "0" * 64


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
            CREATE TABLE IF NOT EXISTS deals (id TEXT PRIMARY KEY, created REAL, updated REAL, doc TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS ledger (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                deal_id TEXT,
                actor TEXT NOT NULL,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                data TEXT NOT NULL,
                prev_hash TEXT NOT NULL,
                hash TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ledger_deal ON ledger(deal_id);
            CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL);
            """
        )

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ------------------------------------------------------------------ deals

    def save_deal(self, deal: Deal) -> None:
        doc = deal.model_dump_json()
        with self._lock:
            self._db.execute(
                "INSERT INTO deals(id, created, updated, doc) VALUES(?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET updated=excluded.updated, doc=excluded.doc",
                (deal.id, time.time(), time.time(), doc),
            )

    def get_deal(self, deal_id: str) -> Optional[Deal]:
        with self._lock:
            row = self._db.execute("SELECT doc FROM deals WHERE id=?", (deal_id,)).fetchone()
        return Deal.model_validate_json(row[0]) if row else None

    def list_deals(self, limit: int = 50) -> list[Deal]:
        with self._lock:
            rows = self._db.execute("SELECT doc FROM deals ORDER BY created DESC LIMIT ?", (limit,)).fetchall()
        return [Deal.model_validate_json(r[0]) for r in rows]

    def find_deal_by_order(self, order_id: str) -> Optional[Deal]:
        for d in self.list_deals(500):
            if d.paypal.order_id == order_id:
                return d
        return None

    def find_deal_by(self, predicate) -> Optional[Deal]:
        for d in self.list_deals(500):
            if predicate(d):
                return d
        return None

    # ------------------------------------------------------------------ ledger

    def append(self, deal_id: Optional[str], actor: str, kind: str, title: str, data: Optional[dict] = None) -> dict:
        data = data or {}
        with self._lock:
            row = self._db.execute("SELECT hash FROM ledger ORDER BY seq DESC LIMIT 1").fetchone()
            prev = row[0] if row else GENESIS
            ts = time.time()
            body = {"ts": round(ts, 6), "deal_id": deal_id, "actor": actor, "kind": kind, "title": title, "data": data}
            h = hashlib.sha256((prev + _canon(body)).encode()).hexdigest()
            cur = self._db.execute(
                "INSERT INTO ledger(ts, deal_id, actor, kind, title, data, prev_hash, hash) VALUES(?,?,?,?,?,?,?,?)",
                (body["ts"], deal_id, actor, kind, title, _canon(data), prev, h),
            )
            return {"seq": cur.lastrowid, **body, "prev_hash": prev, "hash": h}

    def entries(self, deal_id: Optional[str] = None, after: int = 0, limit: int = 1000) -> list[dict]:
        q = "SELECT seq, ts, deal_id, actor, kind, title, data, prev_hash, hash FROM ledger WHERE seq > ?"
        args: list[Any] = [after]
        if deal_id:
            q += " AND deal_id = ?"
            args.append(deal_id)
        q += " ORDER BY seq LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._db.execute(q, args).fetchall()
        return [
            {"seq": r[0], "ts": r[1], "deal_id": r[2], "actor": r[3], "kind": r[4], "title": r[5], "data": json.loads(r[6]), "prev_hash": r[7], "hash": r[8]}
            for r in rows
        ]

    def verify_chain(self) -> dict:
        """Recompute every hash. Any edited, deleted or reordered row breaks the chain."""
        with self._lock:
            rows = self._db.execute("SELECT seq, ts, deal_id, actor, kind, title, data, prev_hash, hash FROM ledger ORDER BY seq").fetchall()
        prev = GENESIS
        for r in rows:
            body = {"ts": r[1], "deal_id": r[2], "actor": r[3], "kind": r[4], "title": r[5], "data": json.loads(r[6])}
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
            self._db.execute(
                "INSERT INTO kv(k, v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (key, _canon(value))
            )

    def reset(self) -> None:
        with self._lock:
            self._db.executescript("DELETE FROM deals; DELETE FROM ledger; DELETE FROM kv;")
