from __future__ import annotations

import hashlib
import gzip
import json
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "item"


def digest(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def append_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        _repair_jsonl_tail(path)
    with path.open("a", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _repair_jsonl_tail(path: Path) -> None:
    """Discard an interrupted write before resuming; keep complete unterminated records."""
    with path.open("rb+") as stream:
        stream.seek(0, 2)
        size = stream.tell()
        if not size:
            return
        stream.seek(size - 1)
        if stream.read(1) == b"\n":
            return
        position = size
        boundary = 0
        while position:
            start = max(0, position - 4096)
            stream.seek(start)
            chunk = stream.read(position - start)
            newline = chunk.rfind(b"\n")
            if newline >= 0:
                boundary = start + newline + 1
                break
            position = start
        stream.seek(boundary)
        tail = stream.read()
        try:
            json.loads(tail)
        except (json.JSONDecodeError, UnicodeDecodeError):
            stream.truncate(boundary)
        else:
            stream.seek(0, 2)
            stream.write(b"\n")


@contextmanager
def operation_lock(directory: Path):
    """Prevent overlapping CLI/app writers; OS releases the lock on interruption."""
    import fcntl

    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".operation.lock").open("a") as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another app or CLI process is already writing this run.") from exc
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    temporary.replace(path)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as stream:
        records = []
        lines = stream.readlines()
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                # A live worker may still be appending the final record.
                if index != len(lines) - 1 or line.endswith("\n"):
                    raise
        return records


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def html_payload(payload: bytes) -> bytes:
    """Decode archived gzip bodies while keeping saved source bytes unchanged."""
    return gzip.decompress(payload) if payload.startswith(b"\x1f\x8b") else payload


def clean_html(payload: bytes) -> str:
    from html.parser import HTMLParser

    class Text(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.parts: list[str] = []
            self.hidden = 0
            self.hidden_tags: list[str] = []

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag in {"br", "p", "div", "li", "h1", "h2", "h3", "h4", "tr"} and not self.hidden:
                self.parts.append("\n")
            attributes = dict(attrs)
            style = (attributes.get("style") or "").replace(" ", "").lower()
            void_tags = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
            if tag in {"script", "style", "noscript", "svg"} or (tag not in void_tags and ("hidden" in attributes or "display:none" in style or "visibility:hidden" in style)):
                self.hidden += 1
                self.hidden_tags.append(tag)

        def handle_endtag(self, tag: str) -> None:
            if self.hidden_tags and tag in self.hidden_tags:
                self.hidden_tags.remove(tag)
                self.hidden -= 1
            elif tag in {"p", "div", "br", "li", "h1", "h2", "h3", "tr"}:
                self.parts.append("\n")

        def handle_data(self, data: str) -> None:
            if not self.hidden:
                self.parts.append(data)

    parser = Text()
    parser.feed(html_payload(payload).decode("utf-8", errors="replace"))
    return "\n".join(" ".join(line.split()) for line in "".join(parser.parts).splitlines() if line.strip())


def body_usability(text: str, employer: str, title: str | None = None) -> tuple[bool, str]:
    normalized = " ".join(text.lower().replace("’", "\'").split())
    if len(normalized) < 500:
        return False, "too_short_or_shell"
    if any(term in normalized for term in ("enable javascript", "checking your browser", "consent required")):
        return False, "javascript_or_consent_shell"
    if any(term in normalized for term in ("this job is no longer available", "position has been filled", "job not found")):
        return False, "closed_job_notice"
    if employer.lower() not in normalized:
        return False, "employer_not_found"
    if title and title.lower() not in normalized:
        return False, "title_not_found"
    responsibility_markers = ("responsibilities", "what you'll do", "what you will do", "you will", "you'll", "about the role")
    requirement_markers = ("qualifications", "what you'll need", "requirements", "experience", "skills", "what we're looking for", "what we are looking for")
    if not any(marker in normalized for marker in responsibility_markers) or not any(marker in normalized for marker in requirement_markers):
        return False, "requirements_or_responsibilities_not_found"
    return True, "complete_candidate"
