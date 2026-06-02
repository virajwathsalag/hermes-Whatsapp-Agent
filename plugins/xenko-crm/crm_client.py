"""Airtable client for Xenko lead capture — Leads, People, Companies, Opportunities."""

from __future__ import annotations

import logging
import os
import re
from datetime import date, timedelta
from typing import Any

import requests

logger = logging.getLogger(__name__)

BASE_ID = os.environ.get("AIRTABLE_BASE_ID", "appPcG5glvZZ8dxVQ")
TABLES = {
    "LEADS": os.environ.get("AIRTABLE_LEADS_TABLE", "tbltPK2QoQJ6LCptt"),
    "PEOPLE": os.environ.get("AIRTABLE_PEOPLE_TABLE", "tblfogtDSlN3dMBBI"),
    "COMPANIES": os.environ.get("AIRTABLE_COMPANIES_TABLE", "tblCTSMzQMrXFRK6Y"),
    "OPPORTUNITIES": os.environ.get("AIRTABLE_OPPORTUNITIES_TABLE", "tblJBjdAbOSQj2NPc"),
}
API_URL = f"https://api.airtable.com/v0/{BASE_ID}"
_TIMEOUT = 15

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_QUAL_SCORE = {"HIGH": 7, "MEDIUM": 5, "LOW": 2}


def _pat() -> str:
    pat = (os.environ.get("AIRTABLE_PAT") or os.environ.get("AIRTABLE_API_KEY") or "").strip()
    if not pat:
        raise RuntimeError("AIRTABLE_PAT is not set")
    return pat


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_pat()}",
        "Content-Type": "application/json",
    }


def normalize_phone(phone: str) -> str:
    """Normalize WhatsApp / Sri Lanka numbers for dedup."""
    clean = re.sub(r"[\s\-+()]", "", phone or "")
    if clean.endswith("@lid"):
        clean = clean.split("@")[0]
    if clean.startswith("94") and len(clean) > 9:
        clean = "0" + clean[2:]
    return clean


def _sanitize_text(value: str, *, max_len: int = 500) -> str:
    text = (value or "").strip()
    if len(text) > max_len:
        text = text[:max_len]
    return text


def _escape_formula(value: str) -> str:
    return (value or "").replace("'", "\\'")


def _create_record(table_key: str, fields: dict[str, Any]) -> str:
    url = f"{API_URL}/{TABLES[table_key]}"
    r = requests.post(url, json={"fields": fields}, headers=_headers(), timeout=_TIMEOUT)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Airtable create failed ({table_key}): {r.status_code}: {r.text[:200]}")
    return r.json().get("id", "created")


def _patch_record(table_key: str, record_id: str, fields: dict[str, Any]) -> None:
    url = f"{API_URL}/{TABLES[table_key]}/{record_id}"
    r = requests.patch(url, json={"fields": fields}, headers=_headers(), timeout=_TIMEOUT)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Airtable update failed ({table_key}): {r.status_code}: {r.text[:200]}")


_AIRTABLE_FIELD_ERR = re.compile(r'Field "([^"]+)"')


def _field_from_airtable_error(message: str) -> str | None:
    match = _AIRTABLE_FIELD_ERR.search(message or "")
    return match.group(1) if match else None


def _write_record_with_fallback(
    table_key: str,
    fields: dict[str, Any],
    *,
    record_id: str | None = None,
    drop_order: tuple[str, ...] = (),
) -> str:
    """Create or patch; on field errors drop the failing column, then optional fallbacks."""
    attempt = dict(fields)
    drop_queue = list(drop_order)
    while True:
        try:
            if record_id:
                _patch_record(table_key, record_id, attempt)
                return record_id
            return _create_record(table_key, attempt)
        except RuntimeError as exc:
            msg = str(exc)
            bad = _field_from_airtable_error(msg)
            if bad and bad in attempt:
                attempt.pop(bad, None)
                logger.warning(
                    "Airtable %s write dropped field %r after error: %s",
                    table_key,
                    bad,
                    msg[:160],
                )
                continue
            if not drop_queue:
                raise
            key = drop_queue.pop(0)
            if key not in attempt:
                continue
            attempt.pop(key, None)
            logger.warning(
                "Airtable %s write dropped field %r after error: %s",
                table_key,
                key,
                msg[:160],
            )


def _find_by_field(table_key: str, field: str, value: str) -> dict[str, Any] | None:
    if not value:
        return None
    formula = f"{{{field}}}='{_escape_formula(value)}'"
    url = f"{API_URL}/{TABLES[table_key]}"
    r = requests.get(
        url,
        params={"filterByFormula": formula, "maxRecords": 1},
        headers=_headers(),
        timeout=_TIMEOUT,
    )
    if r.status_code != 200:
        return None
    records = r.json().get("records") or []
    return records[0] if records else None


def _phone_lookup_variants(phone: str) -> list[str]:
    norm = normalize_phone(phone)
    variants = [norm]
    if norm.startswith("0") and len(norm) >= 10:
        variants.append("94" + norm[1:])
        variants.append("+94" + norm[1:])
    elif norm.startswith("94"):
        variants.append("0" + norm[2:])
        variants.append("+" + norm)
    return list(dict.fromkeys(variants))


def _find_by_phone(table_key: str, phone: str) -> dict[str, Any] | None:
    for variant in _phone_lookup_variants(phone):
        found = _find_by_field(table_key, "Phone", variant)
        if found:
            return found
    return None


def _find_lead_by_phone(phone: str) -> dict[str, Any] | None:
    return _find_by_phone("LEADS", phone)


def lead_exists_for_phone(phone: str) -> bool:
    """True if this WhatsApp number already has a row in Leads (returning contact)."""
    try:
        return _find_lead_by_phone(phone) is not None
    except Exception:
        return False


def _find_person_by_phone(phone: str) -> dict[str, Any] | None:
    return _find_by_phone("PEOPLE", phone)


def _find_company_by_name(name: str) -> dict[str, Any] | None:
    return _find_by_field("COMPANIES", "Company Name", _sanitize_text(name, max_len=200))


def _split_name(full_name: str) -> tuple[str, str]:
    parts = full_name.strip().split(None, 1)
    if not parts:
        return "Unknown", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]


def _compose_notes(
    notes: str,
    *,
    what_they_sell: str = "",
    goal: str = "",
    budget_timeline: str = "",
    qualification_label: str = "",
) -> str:
    parts: list[str] = []
    if what_they_sell:
        parts.append(f"Sells: {_sanitize_text(what_they_sell, max_len=300)}")
    if goal:
        parts.append(f"Goal: {_sanitize_text(goal, max_len=300)}")
    if budget_timeline:
        parts.append(f"Budget/timeline: {_sanitize_text(budget_timeline, max_len=200)}")
    if qualification_label:
        parts.append(f"Qualification: {qualification_label.upper()}")
    base = _sanitize_text(notes, max_len=1500)
    if base:
        parts.insert(0, base)
    return _sanitize_text("; ".join(parts), max_len=2000)


def _extract_company_name_from_answer(text: str) -> str:
    """Pull a company name from step-1 style answers (e.g. 'Nilan we sell clothes')."""
    raw = _sanitize_text(text, max_len=300)
    if not raw:
        return ""
    low = raw.lower()
    for sep in (" we sell ", " sells ", " selling ", " — ", " - "):
        if sep in low:
            idx = low.index(sep)
            return _sanitize_text(raw[:idx], max_len=200)
    words = raw.split()
    if len(words) <= 3 and not any(w.isdigit() for w in words):
        return raw
    return words[0] if words else ""


def parse_intake_answers(
    answers: list[str],
    *,
    company_hint: str = "",
) -> dict[str, str]:
    """
    Map WhatsApp intake answers to structured CRM fields.
    Order: company/what they sell, goal, budget+timeline, name, email.
    """
    clean = [a.strip() for a in answers if (a or "").strip()]

    what_they_sell = clean[0] if len(clean) > 0 else ""
    goal = clean[1] if len(clean) > 1 else ""
    budget_timeline = clean[2] if len(clean) > 2 else ""

    name = ""
    email = ""
    for ans in clean[3:]:
        em = _EMAIL_RE.search(ans)
        if em:
            email = em.group(0)
        elif len(ans) < 80 and not re.search(r"\d{3,}", ans):
            name = name or ans
    for ans in reversed(clean):
        em = _EMAIL_RE.search(ans)
        if em and not email:
            email = em.group(0)

    company = _sanitize_text(company_hint, max_len=200)
    if not company:
        company = _extract_company_name_from_answer(what_they_sell)

    return {
        "what_they_sell": what_they_sell,
        "goal": goal,
        "budget_timeline": budget_timeline,
        "name": name,
        "email": email,
        "company": company,
    }


def _parse_budget_timeline(text: str) -> dict[str, Any]:
    """Extract budget amount (LKR), timeline months, and projected close date."""
    raw = (text or "").strip()
    out: dict[str, Any] = {"budget_text": raw, "budget_amount": None, "timeline_months": None, "close_date": None}
    if not raw:
        return out

    budget_match = re.search(
        r"(?:budget|around|about|roughly|upto|up to|max)?\s*"
        r"(\d+(?:\.\d+)?)\s*(k|m|lakh|lakhs|million|mn|usd|\$|rs\.?|lkr)?",
        raw,
        re.I,
    )
    if budget_match:
        amount = float(budget_match.group(1))
        unit = (budget_match.group(2) or "").lower()
        if unit in ("k",):
            amount *= 1000
        elif unit in ("m", "million", "mn"):
            amount *= 1_000_000
        elif unit in ("lakh", "lakhs"):
            amount *= 100_000
        out["budget_amount"] = int(amount)

    months_match = re.search(r"(\d+)\s*(?:months?|mos?|mths?)\b", raw, re.I)
    if months_match:
        out["timeline_months"] = int(months_match.group(1))
    elif re.search(r"\b(asap|urgent|immediately|this month)\b", raw, re.I):
        out["timeline_months"] = 1
    elif re.search(r"\b(next quarter|q[1-4])\b", raw, re.I):
        out["timeline_months"] = 3

    months = out.get("timeline_months")
    if isinstance(months, int) and months > 0:
        close = date.today() + timedelta(days=max(30, months * 30))
        out["close_date"] = close.isoformat()
    return out


def infer_qualification_label(budget_timeline: str, goal: str = "") -> str:
    """Simple rubric when the model does not pass qualification_label."""
    parsed = _parse_budget_timeline(budget_timeline)
    amount = parsed.get("budget_amount")
    months = parsed.get("timeline_months")
    if amount is not None and amount >= 50_000 and (months is None or months <= 12):
        return "HIGH"
    if amount is not None and amount >= 20_000:
        return "MEDIUM"
    if goal.strip():
        return "MEDIUM"
    return "LOW"


def _build_deal_name(company: str, goal: str = "") -> str:
    base = _sanitize_text(company or "WhatsApp lead", max_len=120)
    short_goal = _sanitize_text(goal, max_len=60)
    if short_goal:
        return _sanitize_text(f"{base} — {short_goal}", max_len=200)
    return _sanitize_text(f"{base} — WhatsApp marketing", max_len=200)


def _build_next_action(
    *,
    what_they_sell: str = "",
    goal: str = "",
    budget_timeline: str = "",
    notes: str = "",
    name: str = "",
    company: str = "",
) -> str:
    lines: list[str] = []
    if company:
        lines.append(f"Company: {company}")
    if name:
        lines.append(f"Contact: {name}")
    if what_they_sell:
        lines.append(f"What they sell: {what_they_sell}")
    if goal:
        lines.append(f"Goal: {goal}")
    if budget_timeline:
        lines.append(f"Budget & timeline: {budget_timeline}")
    if notes:
        lines.append(notes)
    lines.append("Source: WhatsApp qualification (auto-sync)")
    return _sanitize_text("\n".join(lines), max_len=2000)


def _upsert_person(
    *,
    name: str,
    phone: str,
    email: str = "",
    company_name: str = "",
) -> str:
    phone_norm = normalize_phone(phone)
    existing = _find_person_by_phone(phone_norm)
    first, last = _split_name(name)
    fields: dict[str, Any] = {"First name": first, "Phone": phone_norm}
    if last:
        fields["last name"] = last
    if email:
        fields["Email"] = email
    if company_name:
        fields["Company"] = _sanitize_text(company_name, max_len=200)

    if existing:
        _write_record_with_fallback("PEOPLE", fields, record_id=existing["id"], drop_order=("Company",))
        return existing["id"]
    return _write_record_with_fallback("PEOPLE", fields, drop_order=("Company", "Email"))


def _upsert_company(name: str, *, what_they_sell: str = "") -> str:
    del what_they_sell  # Industry is single-select in Airtable; do not write free text
    company = _sanitize_text(name, max_len=200)
    if not company:
        raise ValueError("company name required for company record")
    existing = _find_company_by_name(company)
    if existing:
        return existing["id"]
    return _create_record("COMPANIES", {"Company Name": company})


def _find_opportunity_for_company(company_name: str) -> dict[str, Any] | None:
    """Best-effort: avoid duplicate open deals for same company name (text field)."""
    company_name = _sanitize_text(company_name, max_len=200)
    if not company_name:
        return None
    formula = f"{{COMPANY}}='{_escape_formula(company_name)}'"
    url = f"{API_URL}/{TABLES['OPPORTUNITIES']}"
    r = requests.get(
        url,
        params={"filterByFormula": formula, "maxRecords": 1},
        headers=_headers(),
        timeout=_TIMEOUT,
    )
    if r.status_code != 200:
        return None
    records = r.json().get("records") or []
    return records[0] if records else None


def _create_or_update_opportunity(
    *,
    deal_name: str,
    company_name: str,
    person_name: str,
    next_action: str,
    project_detail: str = "",
    budget_timeline: str = "",
    budget_amount: int | None = None,
    close_date: str | None = None,
) -> str | None:
    """
    Opportunities schema (Xenko base): COMPANY and PERSON are plain text, not record links.
    Next Action is single-line; long brief goes to Pain point. Timeline is single-select (omit).
    """
    fields: dict[str, Any] = {"DEAL NAME": _sanitize_text(deal_name, max_len=200)}
    if company_name:
        fields["COMPANY"] = _sanitize_text(company_name, max_len=200)
    if person_name:
        fields["PERSON"] = _sanitize_text(person_name, max_len=200)
    if next_action:
        fields["Next Action"] = _sanitize_text(next_action, max_len=255)
    detail = project_detail or next_action
    if detail:
        fields["Pain point"] = _sanitize_text(detail, max_len=2000)
    if budget_amount is not None and budget_amount > 0:
        fields["Budget"] = budget_amount
    if close_date:
        fields["Close Date"] = close_date
        fields["Next Action Date"] = close_date
    # Timeline is single-select — keep timeline text inside Pain point only
    if budget_timeline and "Pain point" in fields:
        fields["Pain point"] = _sanitize_text(
            f"{fields['Pain point']}\n\nTimeline: {budget_timeline}",
            max_len=2000,
        )

    drop_order = ("Budget", "Close Date", "Next Action Date", "Pain point", "Next Action", "PERSON", "COMPANY")

    existing = _find_opportunity_for_company(company_name) if company_name else None
    if existing:
        return _write_record_with_fallback(
            "OPPORTUNITIES",
            fields,
            record_id=existing["id"],
            drop_order=drop_order,
        )

    return _write_record_with_fallback("OPPORTUNITIES", fields, drop_order=drop_order)


def add_or_update_lead(
    *,
    name: str,
    phone: str,
    email: str = "",
    company: str = "",
    notes: str = "",
    source: str = "WhatsApp",
    lead_status: str = "New",
    qualification_label: str = "",
    what_they_sell: str = "",
    goal: str = "",
    budget_timeline: str = "",
) -> dict[str, Any]:
    """
    Create or update lead and related People, Company, and Opportunity records.
    """
    name = _sanitize_text(name, max_len=200)
    if not name:
        raise ValueError("name is required")

    phone_norm = normalize_phone(phone)
    if not phone_norm or len(phone_norm) < 7:
        raise ValueError("phone is required")

    email = _sanitize_text(email, max_len=254)
    if email and not _EMAIL_RE.match(email):
        raise ValueError("invalid email format")

    company = _sanitize_text(company, max_len=200)
    if not company and what_they_sell:
        company = _extract_company_name_from_answer(what_they_sell)

    q_label = (qualification_label or "").strip().upper()
    if not q_label or q_label not in _QUAL_SCORE:
        q_label = infer_qualification_label(budget_timeline, goal)

    if lead_status == "New" and q_label in ("HIGH", "MEDIUM"):
        lead_status = "Qualified"

    notes = _compose_notes(
        notes,
        what_they_sell=what_they_sell,
        goal=goal,
        budget_timeline=budget_timeline,
        qualification_label=q_label,
    )

    parsed_bt = _parse_budget_timeline(budget_timeline)
    project_detail = _build_next_action(
        what_they_sell=what_they_sell,
        goal=goal,
        budget_timeline=budget_timeline,
        notes=notes,
        name=name,
        company=company,
    )
    next_action = _sanitize_text(
        f"Follow up: {goal or 'marketing project'} — budget {budget_timeline or 'TBD'}",
        max_len=255,
    )

    company_id = None
    if company:
        try:
            company_id = _upsert_company(company)
        except Exception as exc:
            logger.warning("Company upsert failed for %r: %s", company, exc)
            company_id = None

    person_id = _upsert_person(
        name=name,
        phone=phone_norm,
        email=email,
        company_name=company,
    )

    lead_fields: dict[str, Any] = {
        "Name": name,
        "Phone": phone_norm,
        "Source": source,
        "Status": lead_status,
        "Notes": notes,
    }
    if email:
        lead_fields["Email"] = email
    if company:
        lead_fields["Company"] = company
    if q_label in _QUAL_SCORE:
        lead_fields["Qualification Score"] = _QUAL_SCORE[q_label]

    existing = _find_lead_by_phone(phone_norm)
    if existing:
        try:
            _patch_record("LEADS", existing["id"], lead_fields)
        except RuntimeError:
            for key in ("Source", "Status", "Qualification Score"):
                lead_fields.pop(key, None)
            _patch_record("LEADS", existing["id"], lead_fields)
        lead_id = existing["id"]
        action = "updated"
    else:
        try:
            lead_id = _create_record("LEADS", lead_fields)
        except RuntimeError:
            for key in ("Source", "Status", "Qualification Score"):
                lead_fields.pop(key, None)
            lead_id = _create_record("LEADS", lead_fields)
        action = "created"

    opportunity_id = None
    if company:
        try:
            deal = _build_deal_name(company, goal)
            opportunity_id = _create_or_update_opportunity(
                deal_name=deal,
                company_name=company,
                person_name=name,
                next_action=next_action,
                project_detail=project_detail,
                budget_timeline=budget_timeline,
                budget_amount=parsed_bt.get("budget_amount"),
                close_date=parsed_bt.get("close_date"),
            )
        except Exception as exc:
            logger.warning("Opportunity create failed for %r: %s", company, exc)
            opportunity_id = None

    return {
        "action": action,
        "id": lead_id,
        "name": name,
        "person_id": person_id,
        "company_id": company_id,
        "opportunity_id": opportunity_id,
        "qualification": q_label or None,
    }
