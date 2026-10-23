"""Workspace persistence: entities, observations (with provenance), rulebook and analyst decisions."""

from __future__ import annotations

import json
import os
import sqlite3

from .evidence import admiralty_confidence, sha256_json, utcnow

DB_PATH = os.environ.get("EVGRAPH_DB", "evgraph.db")
SCHEMA = """
CREATE TABLE IF NOT EXISTS entities (id TEXT PRIMARY KEY, type TEXT, value TEXT, label TEXT, attrs TEXT DEFAULT '{}');
CREATE TABLE IF NOT EXISTS observations (
    id TEXT PRIMARY KEY, subject TEXT, predicate TEXT, object TEXT, attrs TEXT DEFAULT '{}',
    source TEXT, url TEXT, retrieved_at TEXT, sha256 TEXT, method TEXT, reliability TEXT, credibility INTEGER,
    explanation TEXT, observed_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_obs_s ON observations(subject);
CREATE INDEX IF NOT EXISTS ix_obs_o ON observations(object);
CREATE TABLE IF NOT EXISTS rules (id TEXT PRIMARY KEY, lr REAL, description TEXT);
CREATE TABLE IF NOT EXISTS decisions (id TEXT PRIMARY KEY, kind TEXT, a TEXT, b TEXT, status TEXT, note TEXT, analyst TEXT, decided_at TEXT, snapshot TEXT);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
"""


def entity_id(etype: str, value: str) -> str:
    return f"{etype}:{value.lower().strip()}"


class Store:
    def __init__(self, path: str | None = None):
        self.con = sqlite3.connect(path or DB_PATH, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.con.executescript(SCHEMA)

    # ------------------------------------------------------------- entities
    def entity(self, etype: str, value: str, label: str = "", **attrs) -> str:
        eid = entity_id(etype, value)
        row = self.con.execute("SELECT attrs FROM entities WHERE id=?", (eid,)).fetchone()
        with self.con:
            if row:
                merged = {**json.loads(row["attrs"]), **{k: v for k, v in attrs.items() if v not in (None, "")}}
                self.con.execute("UPDATE entities SET attrs=? WHERE id=?", (json.dumps(merged), eid))
            else:
                self.con.execute("INSERT INTO entities VALUES (?,?,?,?,?)", (eid, etype, value.strip(), label or value.strip(), json.dumps(attrs)))
        return eid

    def entities(self) -> list[dict]:
        return [{**dict(r), "attrs": json.loads(r["attrs"])} for r in self.con.execute("SELECT * FROM entities ORDER BY type, value")]

    # ------------------------------------------------------------- observations
    def observe(self, subject: str, predicate: str, obj: str, *, source: str, url: str = "", retrieved_at: str = "", sha256: str = "",
                method: str = "manual", reliability: str = "F", credibility: int = 6, explanation: str = "", observed_at: str = "", **attrs) -> str:
        """Record one observation. The same (s, p, o) from the same source is stored once (idempotent)."""
        oid = sha256_json([subject, predicate, obj, source])[:20]
        with self.con:
            self.con.execute(
                "INSERT INTO observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET retrieved_at=excluded.retrieved_at, "
                "sha256=excluded.sha256, attrs=excluded.attrs, reliability=excluded.reliability, credibility=excluded.credibility, explanation=excluded.explanation",
                (oid, subject, predicate, obj, json.dumps(attrs), source, url, retrieved_at or utcnow(), sha256, method, reliability, int(credibility),
                 explanation, observed_at))
        return oid

    def observations(self) -> list[dict]:
        out = []
        for r in self.con.execute("SELECT * FROM observations ORDER BY subject, predicate"):
            d = dict(r)
            d["attrs"] = json.loads(d["attrs"])
            d["confidence"] = admiralty_confidence(d["reliability"], d["credibility"])
            d["grade"] = f"{d['reliability']}{d['credibility']}"
            out.append(d)
        return out

    def delete_observation(self, oid: str) -> None:
        with self.con:
            self.con.execute("DELETE FROM observations WHERE id=?", (oid,))

    # ------------------------------------------------------------- rules & settings
    def rules(self, defaults: dict) -> dict:
        stored = {r["id"]: {"lr": r["lr"], "description": r["description"]} for r in self.con.execute("SELECT * FROM rules")}
        return {k: {**v, **stored.get(k, {})} for k, v in defaults.items()}

    def set_rule(self, rid: str, lr: float, description: str) -> None:
        if lr <= 0:
            raise ValueError("likelihood ratio must be > 0")
        with self.con:
            self.con.execute("INSERT OR REPLACE INTO rules VALUES (?,?,?)", (rid, lr, description))

    def setting(self, key: str, default=None):
        r = self.con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(r["value"]) if r else default

    def set_setting(self, key: str, value) -> None:
        with self.con:
            self.con.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value)))

    # ------------------------------------------------------------- decisions
    def decide(self, hid: str, kind: str, a: str, b: str, status: str, note: str, analyst: str, snapshot: dict) -> None:
        if status not in ("accepted", "rejected", "open"):
            raise ValueError("status must be accepted, rejected or open")
        with self.con:
            if status == "open":
                self.con.execute("DELETE FROM decisions WHERE id=?", (hid,))
            else:
                self.con.execute("INSERT OR REPLACE INTO decisions VALUES (?,?,?,?,?,?,?,?,?)",
                                 (hid, kind, a, b, status, note, analyst, utcnow(), json.dumps(snapshot)))

    def decisions(self) -> dict:
        return {r["id"]: {**dict(r), "snapshot": json.loads(r["snapshot"])} for r in self.con.execute("SELECT * FROM decisions")}

    def reset(self) -> None:
        with self.con:
            for t in ("entities", "observations", "decisions", "rules", "settings"):
                self.con.execute(f"DELETE FROM {t}")
