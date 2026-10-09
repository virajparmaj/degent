"""Local immutable evidence and cross-run identities, with transactional event indexing."""

from __future__ import annotations

import copy
import json
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .store import digest, utc_now


class SnapshotStore:
    def __init__(self, config, reserve_storage=None):
        self.reserve_storage = reserve_storage
        self.root = Path(config.get("snapshot_dir") or Path(config["data_dir"]) / "snapshots")
        self.root.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS vacancies (id TEXT PRIMARY KEY, first_seen TEXT NOT NULL, metadata TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS versions (id TEXT PRIMARY KEY, vacancy_id TEXT NOT NULL, sha256 TEXT NOT NULL, parser TEXT NOT NULL, first_seen TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY, run_id TEXT NOT NULL, observation TEXT NOT NULL, vacancy_id TEXT);
                CREATE TABLE IF NOT EXISTS artifacts (key TEXT PRIMARY KEY, artifact TEXT NOT NULL);
            """)
            if "vacancy_id" not in {r[1] for r in db.execute("PRAGMA table_info(events)")}:
                db.execute("ALTER TABLE events ADD COLUMN vacancy_id TEXT")
            for identity, payload in db.execute(
                "SELECT id, observation FROM events WHERE vacancy_id IS NULL"
            ).fetchall():
                db.execute(
                    "UPDATE events SET vacancy_id=? WHERE id=?",
                    (json.loads(payload).get("vacancy_id"), identity),
                )
            db.execute("CREATE INDEX IF NOT EXISTS events_vacancy ON events(vacancy_id)")
            db.execute("CREATE INDEX IF NOT EXISTS versions_vacancy ON versions(vacancy_id)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.root / "index.sqlite3", timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def blob(self, payload: bytes, kind="raw") -> str:
        sha = digest(payload)
        path = self.root / kind / sha[:2] / sha
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if digest(path.read_bytes()) != sha:
                raise ValueError("Immutable content blob hash mismatch.")
        else:
            if self.reserve_storage:
                self.reserve_storage(len(payload))
            fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".pending-")
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    if digest(path.read_bytes()) != sha:
                        raise ValueError("Immutable content blob hash mismatch.")
            finally:
                Path(temporary).unlink(missing_ok=True)
        return str(path)

    def event(self, event_id):
        with self.connect() as db:
            row = db.execute("SELECT observation FROM events WHERE id=?", (event_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def capture(self, config, run_id, employer, source, row, observed_at, event_id):
        existing = self.event(event_id)
        if existing:
            return existing
        from .cli import canonical_vacancy_url
        from .companies import source_identity
        from .sources import observation

        provider = source["provider"]
        posting = str(row.get("posting_id") or canonical_vacancy_url(row["source_url"]))
        # Never merge same titles, requisitions on different boards, or unrelated text.
        vacancy = employer["id"] + ":" + source_identity(source) + ":" + posting
        if provider == "capital_one" and row.get("verified_employer") == "Capital One":
            from .cli import vacancy_identity
            vacancy = vacancy_identity(employer["id"], row["source_url"], {"requisition_id": posting})[0]
        if provider in {"dom", "sitemap"}:
            from .generic_sources import canonical

            # One posting discovered through multiple official listings is one vacancy.
            vacancy = employer["id"] + ":generic-url:" + canonical(row["source_url"])
        parser = (
            "workday-cxs-v1"
            if provider == "workday"
            else "capital-one-jobposting-v1" if provider == "capital_one" else "public-feed-v1"
        )
        if provider == "lever":
            from .lever import PARSER_VERSION

            parser = PARSER_VERSION
        elif provider in {"dom", "sitemap"}:
            from .generic_sources import PARSER_VERSION

            parser = (
                PARSER_VERSION
                + ":"
                + digest(json.dumps(source.get("dom_config", {}), sort_keys=True))[:16]
            )
        text = f"{employer['name']}\n{str(row.get('title') or '').strip()}\nLocation: {row.get('location') or 'Unknown'}\n{row.get('text') or ''}"
        version = digest(vacancy + parser + digest(text))
        item = observation(
            config,
            run_id,
            employer,
            row,
            provider,
            observed_at,
            identity={"vacancy_id": vacancy, "document_id": version, "observation_id": event_id},
            persist=False,
        )
        raw = json.dumps(row, ensure_ascii=False, sort_keys=True).encode()
        item.update(
            raw_file=self.blob(raw),
            clean_file=self.blob(text.encode(), "clean"),
            parser_version=parser,
            content_version_id=version,
            career_source_id=source_identity(source),
            provider_tenant=source.get("company") or source.get("board"),
            provider_board=source.get("site") or source.get("board"),
            source_provenance=copy.deepcopy(source),
            url_aliases=[row["source_url"], item["canonical_url"]],
            requisition_id=row.get("requisition_id") or item.get("requisition_id"),
            upstream_start_at=row.get("upstream_start_at"),
            upstream_start_date_source=row.get("upstream_start_date_source"),
        )
        if self.reserve_storage:
            self.reserve_storage(len(json.dumps(item).encode()) + 1024)
        item["collection_session_id"] = row.get("_session_id")
        return self.register_event(run_id, employer, source, posting, item, observed_at, event_id)

    def register_event(self, run_id, employer, source, posting, item, observed_at, event_id):
        vacancy, version, parser = item["vacancy_id"], item["document_id"], item["parser_version"]
        text_sha = item["clean_sha256"]
        with self.connect() as db:
            previous = db.execute(
                "SELECT observation FROM events WHERE vacancy_id=? ORDER BY rowid DESC LIMIT 1",
                (vacancy,),
            ).fetchone()
            prior = json.loads(previous[0])["document_id"] if previous else None
            known = db.execute("SELECT id FROM versions WHERE id=?", (version,)).fetchone()
            db.execute(
                "INSERT OR IGNORE INTO vacancies VALUES (?,?,?)",
                (
                    vacancy,
                    observed_at,
                    json.dumps(
                        {"company_id": employer["id"], "source": source, "posting_id": posting}
                    ),
                ),
            )
            db.execute(
                "UPDATE vacancies SET first_seen=min(first_seen, ?) WHERE id=?",
                (observed_at, vacancy),
            )
            first = db.execute(
                "SELECT first_seen FROM vacancies WHERE id=?", (vacancy,)
            ).fetchone()[0]
            db.execute(
                "INSERT OR IGNORE INTO versions VALUES (?,?,?,?,?)",
                (version, vacancy, text_sha, parser, observed_at),
            )
            item.update(
                collector_first_observed_at=first,
                version_first_observed_at=db.execute(
                    "SELECT first_seen FROM versions WHERE id=?", (version,)
                ).fetchone()[0],
                collection_session_id=item.get("collection_session_id"),
                content_change="unchanged" if prior == version else "changed" if prior else "new",
                new_content_version=not bool(known),
            )
            db.execute(
                "INSERT OR IGNORE INTO events (id,run_id,observation,vacancy_id) VALUES (?,?,?,?)",
                (event_id, run_id, json.dumps(item), vacancy),
            )
        return self.event(event_id)

    def history(self, vacancy):
        with self.connect() as db:
            rows = db.execute(
                "SELECT observation FROM events WHERE vacancy_id=? ORDER BY rowid", (vacancy,)
            ).fetchall()
        return [json.loads(r[0]) for r in rows]

    def artifact(self, key):
        with self.connect() as db:
            row = db.execute("SELECT artifact FROM artifacts WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put_artifact(self, key, artifact):
        # Cache contains automatic evidence only. Human decisions stay run-local.
        saved = copy.deepcopy(artifact)
        for field in ("document_review", "extraction_id", "review_status"):
            saved.pop(field, None)
        for row in saved.get("requirements", []):
            for field in ("requirement_id", "human_review", "review_note", "review_revision"):
                row.pop(field, None)
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO artifacts VALUES (?,?)", (key, json.dumps(saved)))
