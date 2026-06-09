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
DEFAULT_HOME_PHONE = "94741597999"

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


def home_whatsapp_phone() -> str:
    """E.164 digits only (no +), home number for lead notifications."""
    raw = (
        os.environ.get("HOME_WHATSAPP_PHONE")
        or DEFAULT_HOME_PHONE
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
    destination: str | None = None,
) -> bool:
    """
    Post an outbound WhatsApp to the founder through the gateway bridge.
    Fails silently (logs only) so customer intake is never blocked.
    
    destination: "founder" (default), "home", or phone digits.
    """
    body = (message or "").strip()
    if not body:
        return False

    if dedupe_key:
        token = f"{session_id or ''}:{dedupe_key}"
        if token in _notified_keys:
            return False

    phone = founder_whatsapp_phone()
    if destination == "home":
        phone = home_whatsapp_phone()
    elif destination and destination.isdigit():
        phone = destination
    elif destination and "@" in destination:
        phone = destination
    
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
    industry: str = "",
    goal: str = "",
    budget_text: str = "",
    amount_lkr: int | None = None,
    timeline: str = "",
    phone: str | None = None,
    destination: str | None = "founder",
) -> None:
    who = name.strip() or "unknown"
    co = company.strip() or "unknown"
    budget = budget_text.strip() or (f"{amount_lkr:,} LKR" if amount_lkr else "unknown")
    lead_phone = (phone or "").strip() or "unknown"
    msg_lines = [
        "below-budget web lead",
        f"name: {who}",
        f"company: {co}",
    ]
    if industry.strip():
        msg_lines.append(f"industry: {industry.strip()[:120]}")
    if goal.strip():
        msg_lines.append(f"goal: {goal.strip()[:200]}")
    msg_lines.extend([
        f"budget: {budget}",
    ])
    if timeline.strip():
        msg_lines.append(f"timeline: {timeline.strip()[:120]}")
    msg_lines.append(f"whatsapp: {lead_phone}")
    notify_founder("\n".join(msg_lines), session_id=session_id, dedupe_key="below_budget", destination=destination)


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
    destination: str | None = "founder",
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
    if phone:
        lines.append(f"contact: {phone.strip()}")
    notify_founder("\n".join(lines), session_id=session_id, dedupe_key="qualified", destination=destination)
