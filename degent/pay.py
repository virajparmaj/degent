"""Conservative salary candidates with exact spans; never borrow posting locations."""
from __future__ import annotations

import re

from .store import digest

MONEY = re.compile(r"(?P<symbol>USD\s*|US\$\s*|\$\s*)(?P<low>\d[\d,]*(?:\.\d+)?)"
                   r"(?:\s*(?:-|–|—|to)\s*(?:USD\s*|US\$\s*|\$\s*)?(?P<high>\d[\d,]*(?:\.\d+)?))?")


def pay_records(text: str, document_id: str, source_sha256: str) -> list[dict]:
    result, offset, period, period_evidence = [], 0, None, None
    for raw in text.splitlines(keepends=True):
        line = raw.strip()
        start = offset + len(raw) - len(raw.lstrip())
        end = offset + len(raw.rstrip())
        offset += len(raw)
        if re.search(r"qualifications|responsibilities|benefits|equal opportunity", line, re.I):
            period = period_evidence = None
        found_period = next((value for value, pattern in (
            ("year", r"annual (?:base )?salar|per year|/year|yearly"),
            ("hour", r"per hour|/hour|hourly"), ("month", r"per month|monthly"))
            if re.search(pattern, line, re.I)), None)
        if found_period:
            period, period_evidence = found_period, {"start": start, "end": end, "quote": text[start:end]}
        for match in MONEY.finditer(line):
            prefix = line[:match.start()].strip()
            # A colon explicitly associates a location or role label with the salary.
            label = prefix.rsplit(":", 1)[0].strip() if prefix.endswith(":") else None
            location = label if label and not re.search(r"\b(?:engineer|scientist|analyst|manager|director|associate|intern)\b", label, re.I) else None
            location_status = "explicit_label_needs_review" if location else "unknown"
            currency = "USD" if match["symbol"].strip() in {"USD", "US$"} else None
            result.append({
                "pay_id": digest(document_id + str(start + match.start()) + source_sha256),
                "document_id": document_id, "source_sha256": source_sha256,
                "amount_min": float(match["low"].replace(",", "")),
                "amount_max": float(match["high"].replace(",", "")) if match["high"] else None,
                "currency": currency, "currency_symbol": match["symbol"].strip(),
                "currency_status": "explicit" if currency else "dollar_currency_unspecified",
                "pay_period": period, "pay_period_evidence": period_evidence,
                "applicable_location": location, "location_status": location_status,
                "attribution_label": label,
                "evidence_start": start, "evidence_end": end, "evidence": text[start:end],
                "amount_start": start + match.start(), "amount_end": start + match.end(),
                "status": "candidate_requires_pay_attribution_review", "parser_version": "pay-v1",
                "record_kind": "pay_candidate" if period or re.search(r"\b(?:salary|salaries|compensation|pay|wage|bonus|stipend)\b", line, re.I) else "monetary_mention_unclassified",
            })
    return result
