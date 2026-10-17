"""Archive authentication, continuation and retained failures without live requests."""

import json

import pytest

from degent import archive_inventory
from degent.archive_inventory import (
    archive_posting,
    index_page,
    parse_index,
    public_get,
    verified_wording,
)
from degent.store import read_jsonl

URL = "https://www.capitalonecareers.com/job/mclean/data-analyst/1732/12345"


def receipt(**kwargs):
    return {
        "status": 200,
        "final_url": f"https://web.archive.org/web/20200301120000id_/{URL}",
        "response_headers": {
            "Memento-Datetime": "Sun, 01 Mar 2020 12:00:00 GMT",
            "Link": f'<{URL}>; rel="original"',
            "X-Archive-Src": "capture.warc.gz",
        },
        **kwargs,
    }


def test_archive_index_preserves_records_and_explicit_continuation():
    body = json.dumps(
        [["timestamp", "original", "digest"], ["20200301120000", URL, "abc"], [], ["next-page"]]
    ).encode()
    rows, cursor = parse_index(body)
    assert len(rows) == 1 and rows[0]["original"] == URL
    assert cursor == "next-page"
    with pytest.raises(ValueError, match="schema"):
        parse_index(b"[]")


def test_actual_archive_date_requires_original_uri_and_archive_storage_headers():
    assert verified_wording(receipt(), URL) == "2020-03-01T12:00:00+00:00"
    bad = receipt()
    del bad["response_headers"]["X-Archive-Src"]
    with pytest.raises(ValueError, match="authentication"):
        verified_wording(bad, URL)
    bad = receipt()
    bad["response_headers"][
        "Link"
    ] = '<https://www.capitalonecareers.com/job/x/y/1732/999>; rel="original"'
    with pytest.raises(ValueError, match="URI"):
        verified_wording(bad, URL)
    with pytest.raises(ValueError, match="timestamp"):
        verified_wording(
            receipt(final_url=f"https://web.archive.org/web/20210301120000id_/{URL}"), URL
        )
    with pytest.raises(ValueError, match="transport"):
        verified_wording(receipt(final_url=URL), URL)


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openrouter.ai/api/v1/chat/completions",
        "http://web.archive.org/x",
        "https://secret@web.archive.org/x",
    ],
)
def test_collector_cannot_call_paid_provider_or_send_credentials(tmp_path, url):
    with pytest.raises(ValueError, match="Only the free"):
        public_get(url, tmp_path)


def test_index_resume_preserves_both_pages_and_does_not_stop_at_page_size(tmp_path, monkeypatch):
    calls = []

    def response(url, directory):
        calls.append(url)
        rows = [["timestamp", "original", "digest"], ["20200301120000", URL, "abc"]]
        if len(calls) == 1:
            rows += [[], ["continue"]]
        else:
            assert "resumeKey=continue" in url
            rows[1] = ["20200401120000", URL, "changed"]
        return json.dumps(rows).encode(), {"status": 200}

    monkeypatch.setattr(archive_inventory, "public_get", response)
    first = index_page(2020, tmp_path)
    assert not first["complete"]
    second = index_page(2020, tmp_path)
    assert second["complete"]
    assert len(read_jsonl(tmp_path / "candidates-2020.jsonl")) == 2
    index_page(2020, tmp_path)
    assert len(calls) == 2


def test_nonadvancing_cursor_is_failure_not_completed_collection(tmp_path, monkeypatch):
    body = json.dumps([["timestamp", "original", "digest"], [], ["same-cursor"]]).encode()
    monkeypatch.setattr(
        archive_inventory, "public_get", lambda url, directory: (body, {"status": 200})
    )
    index_page(2020, tmp_path)
    with pytest.raises(ValueError, match="did not advance"):
        index_page(2020, tmp_path)


def test_focused_query_is_separately_bound_even_after_completion(tmp_path, monkeypatch):
    body = json.dumps(
        [["timestamp", "original", "digest"], ["20200301120000", URL, "abc"]]
    ).encode()
    calls = []

    def response(url, directory):
        calls.append(url)
        return body, {"status": 200}

    monkeypatch.setattr(archive_inventory, "public_get", response)
    state = index_page(2020, tmp_path, url_pattern="www.capitalonecareers.com/job/mclean/*")
    assert state["complete"]
    assert "mclean" in calls[0]
    with pytest.raises(ValueError, match="query changed"):
        index_page(2020, tmp_path)


def test_early_year_query_dates_do_not_change_saved_range(tmp_path, monkeypatch):
    body = json.dumps(
        [["timestamp", "original", "digest"], ["20260101120000", URL, "abc"]]
    ).encode()

    def response(url, directory):
        assert "from=202601" in url and "to=202603" in url
        return body, {"status": 200}

    monkeypatch.setattr(archive_inventory, "public_get", response)
    state = index_page(2026, tmp_path, from_month=1, to_month=3)
    assert state["to_month"] == 3
    with pytest.raises(ValueError, match="month range changed"):
        index_page(2026, tmp_path)


@pytest.mark.parametrize(
    "employer,status",
    [
        ("Capital One - US", "capital_one_us_source_alias"),
        ("People Center", "conflicting_employer_assertion"),
    ],
)
def test_historical_aliases_are_explicit_and_conflicting_employers_remain_unresolved(
    employer, status
):
    job = {
        "@type": "JobPosting",
        "title": "Data Analyst",
        "url": URL,
        "identifier": "R12345",
        "hiringOrganization": {"name": employer},
        "description": "<p>Analyze datasets.</p>",
        "jobLocation": [],
    }
    body = ('<script type="application/ld+json">' + json.dumps(job) + "</script>").encode()
    parsed = archive_posting(body, URL)
    assert parsed["employer_status"] == status
    assert parsed["employer_assertion"] == employer
    assert parsed["original_record"] == job
    assert parsed["text"] == "Analyze datasets."
