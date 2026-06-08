"""Send internal WhatsApp alerts to the Xenko founder via the local bridge."""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

DEFAULT_FOUNDER_PHONE = "94760193094"
_notified_keys: set[str] = set()


def founder_whatsapp_phone() -> str:
    """E.164 digits only (no +), default Sri Lanka founder line."""
    raw = (
        os.environ.get("FOUNDER_WHATSAPP_PHONE")
        or os.environ.get("HERMES_PRIVILEGED_PHONE")
        or DEFAULT_FOUNDER_PHONE
    ).strip()
    digits = re.sub(r"[^\d]", "", raw)
    if digits.startswith("0") and len(digits) >= 10:
        digits = "94" + digits[1:]
    return digits


def _bridge_port() -> int:
    try:
        return int(os.environ.get("WHATSAPP_BRIDGE_PORT", "3000"))
    except ValueError:
        return 3000


def _whatsapp_chat_id(phone_digits: str) -> str:
    if "@" in phone_digits:
        return phone_digits
    return f"{phone_digits}@s.whatsapp.net"


def notify_founder(
    message: str,
    *,
    session_id: str | None = None,
    dedupe_key: str | None = None,
) -> bool:
    """
    Post an outbound WhatsApp to the founder through the gateway bridge.
    Fails silently (logs only) so customer intake is never blocked.
    """
    body = (message or "").strip()
    if not body:
        return False

    if dedupe_key:
        token = f"{session_id or ''}:{dedupe_key}"
        if token in _notified_keys:
            return False

    phone = founder_whatsapp_phone()
    if not phone:
        logger.warning("Founder notify skipped: no FOUNDER_WHATSAPP_PHONE configured")
        return False

    payload = json.dumps(
        {"chatId": _whatsapp_chat_id(phone), "message": body}
    ).encode("utf-8")
    url = f"http://127.0.0.1:{_bridge_port()}/send"
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if 200 <= resp.status < 300:
                if dedupe_key:
                    _notified_keys.add(f"{session_id or ''}:{dedupe_key}")
                logger.info("Founder WhatsApp notify sent to %s", phone)
                return True
            logger.warning("Founder notify HTTP %s", resp.status)
    except urllib.error.URLError as exc:
        logger.warning("Founder WhatsApp notify failed: %s", exc)
    except Exception as exc:
        logger.warning("Founder WhatsApp notify error: %s", exc)
    return False


def notify_below_budget_web(
    *,
    session_id: str | None,
    name: str = "",
    company: str = "",
    budget_text: str = "",
    amount_lkr: int | None = None,
    phone: str | None = None,
) -> None:
    who = name.strip() or "unknown"
    co = company.strip() or "unknown"
    budget = budget_text.strip() or (f"{amount_lkr:,} LKR" if amount_lkr else "unknown")
    lead_phone = (phone or "").strip() or "unknown"
    msg = (
        f"below-budget web lead\n"
        f"name: {who}\n"
        f"company: {co}\n"
        f"budget: {budget}\n"
        f"whatsapp: {lead_phone}"
    )
    notify_founder(msg, session_id=session_id, dedupe_key="below_budget")


def notify_qualified_lead(
    *,
    session_id: str | None,
    name: str = "",
    company: str = "",
    industry: str = "",
    goal: str = "",
    budget: str = "",
    timeline: str = "",
    email: str = "",
    phone: str | None = None,
    lead_type: str = "",
    below_budget: bool = False,
) -> None:
    tag = "qualified lead"
    if below_budget:
        tag = "qualified lead (below min web budget)"
    lines = [
        tag,
        f"name: {name.strip() or 'unknown'}",
        f"company: {company.strip() or 'unknown'}",
    ]
    if industry.strip():
        lines.append(f"industry: {industry.strip()}")
    if lead_type.strip():
        lines.append(f"type: {lead_type.strip()}")
    if goal.strip():
        lines.append(f"goal: {goal.strip()[:200]}")
    if budget.strip():
        lines.append(f"budget: {budget.strip()[:120]}")
    if timeline.strip():
        lines.append(f"timeline: {timeline.strip()[:120]}")
    if email.strip():
        lines.append(f"email: {email.strip()}")
    if phone:
        lines.append(f"whatsapp: {phone.strip()}")
    notify_founder("\n".join(lines), session_id=session_id, dedupe_key="qualified")
