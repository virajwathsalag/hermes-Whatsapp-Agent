"""WhatsApp intake guard — Xenko sales line: greet, qualify, block abuse."""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _notify_qualified_lead(**kwargs: Any) -> None:
    """Lazy import to avoid module loading issues."""
    try:
        from .founder_notify import notify_qualified_lead as _notify
        _notify(**kwargs)
    except ImportError:
        logger.warning("founder_notify not available - skipping WhatsApp notification")


def _notify_below_budget_web(**kwargs: Any) -> None:
    """Lazy import to avoid module loading issues."""
    try:
        from .founder_notify import notify_below_budget_web as _notify
        _notify(**kwargs)
    except ImportError:
        logger.warning("founder_notify not available - skipping WhatsApp notification")

_session_step: dict[str, dict[str, Any]] = {}
_session_returning: dict[str, str] = {}  # session_id -> "asked" | "same" | "new"
_session_greeting_burst: set[str] = set()
_session_contact: dict[str, dict[str, str]] = {}  # session_id -> prior name/phone for CRM
_session_transcript: dict[str, list[tuple[str, str]]] = {}  # gateway often sends one turn only
_session_intake_closed: set[str] = set()
_session_crm_synced: set[str] = set()
_session_below_budget: set[str] = set()
_session_lead_type: dict[str, str] = {}  # session_id -> web | marketing | unsure | general
_session_returning_welcomed: set[str] = set()
_session_freshly_synced: set[str] = set()  # phones that were just saved to CRM this session
_session_fields: dict[str, dict[str, str]] = {}  # session_id -> captured intake fields
_phone_intake_fields: dict[str, dict[str, str]] = {}  # survives session_id churn same phone
_phones_in_active_intake: set[str] = set()  # E.164 digit keys — mid-flow, block returning hijack
_session_fresh_intake: set[str] = set()  # block GBrain backfill on new hello same number

STEP_FIELD_MAP: dict[int, str] = {
    1: "name",
    2: "company",
    3: "industry",
    4: "goal",
    5: "budget",
    6: "timeline",
    7: "contact",
}

MONTH_NAME_RE = re.compile(
    r"\b(january|jan|february|feb|march|mar|april|apr|may|june|jun|july|jul|"
    r"august|aug|september|sep|sept|october|oct|november|nov|december|dec)\b",
    re.I,
)
TIMELINE_ANSWER_RE = re.compile(
    r"\b(around|about|by|before|after|next|this|end\s+of|start\s+of|mid)\b",
    re.I,
)

RETURNING_NEW_BUSINESS_STEPS = 5  # new company: company, industry, outcome, budget, timeline
RETURNING_KNOWN_PROFILE_STEPS = 3  # same business: outcome, budget, timeline — then contact number

CLOSE_LINE = (
    "thank you for taking the time to share that with me. i have everything i need for now. "
    "our founder will personally review your requirements and get in touch with you shortly. "
    "we're looking forward to learning more about your business and exploring how we can help."
)
CLOSE_LINE_LEGACY = "our founder will be in touch"
CLOSE_STEP = 8
RETURNING_QUESTION = (
    "great to hear from you again. are you reaching out about your existing project, "
    "or is this something new you'd like help with?"
)

RETURNING_STEPS_KNOWN = {
    1: "what are you hoping to achieve?",
    2: "do you already have a budget in mind?",
    3: "when would you ideally like to get started?",
}
INTAKE_ANSWERS_REQUIRED = 7  # name, company, industry, outcome, budget, timeline, contact number
WEB_MIN_BUDGET_LKR = 100_000

STEPS = {
    1: "before we get started, what's your name?",
    2: "what's the name of your business?",
    3: "what kind of business are you in?",
    4: "what are you hoping to achieve?",
    5: "do you already have a budget in mind?",
    6: "when would you ideally like to get started?",
    7: "is this the best number to reach you on, or do you have another one?",
}

# Single message greeting - NO multi-part, NO marketing pitch in greeting
GREETING = "hi there. thanks for reaching out. i'd be happy to help. what's your name?"

WEB_INTAKE_RE = re.compile(
    r"\b(website|web site|web design|need a site|company website|online presence|"
    r"looking for a website|need a website)\b",
    re.I,
)
PRICE_WEB_RE = re.compile(
    r"\b(how much.*website|website cost|website price|cost of a website)\b",
    re.I,
)

# Skip welcome sequence — go straight to step 1
STRONG_INTAKE_RE = re.compile(
    r"\b(marketing help|need marketing|i need marketing|want marketing|"
    r"help with marketing|looking for marketing)\b",
    re.I,
)

# Any business / growth intent on this number counts as marketing enquiry
DIRECT_INTAKE_RE = re.compile(
    r"\b(marketing|advertis|ads?\b|ad campaign|lead gen|leads|more customers|"
    r"grow(?:ing)?(?:\s+\w+){0,3}\s+sales|increase sales|digital marketing|social media|"
    r"promot(?:e|ion)|brand awareness|get customers|need customers|"
    r"whatsapp (?:marketing|sales)|seo\b|content marketing|"
    r"website|web site|web design|need a site|company website|online presence|"
    r"work with xenko|xenko)\b",
    re.I,
)

NEW_LEAD_RE = re.compile(
    r"\b(new one|new company|another company|different company|second company|"
    r"yes.*new|yep.*new|brand new|a new)\b",
    re.I,
)

SAME_PROJECT_RE = re.compile(
    r"\b(same|previous|old one|that one|existing|first one|continue with|"
    r"the same|same company|same project|same enquiry|same inquiry)\b",
    re.I,
)

CONFIRM_PRIOR_PHONE_RE = re.compile(
    r"^(yes|yeah|yep|yup|sure|ok|okay|correct|right|fine|please|"
    r"that(?:'s| is)?\s*(?:fine|ok|good)|use that|same one|the same|go ahead|"
    r"this one|this number|same number)\b",
    re.I,
)

DECLINE_PRIOR_PHONE_RE = re.compile(
    r"^(no|nope|nah|different|another|new number|not that|don'?t)\b",
    re.I,
)

PHONE_IN_TEXT_RE = re.compile(r"(?:\+?\d[\d\s\-()]{7,}\d|\d{9,})")

# Legacy alias used in a few helpers
MARKETING_RE = STRONG_INTAKE_RE

GREETING_ONLY_RE = re.compile(
    r"^(?:hi+|hello+|hey+|helo+|hellow?|hiya|howdy|yo|good\s*(?:morning|afternoon|evening)|"
    r"sup|greetings)(?:\s+there)?[\s!.?,]*$",
    re.I,
)

GREETING_IN_MESSAGE_RE = re.compile(
    r"\b(hi|hello|hey|good\s*(?:morning|afternoon|evening))\b",
    re.I,
)

HELP_SEEKING_RE = re.compile(
    r"\b(?:need|want|looking\s+for|ned)\b(?:\s+\w+){0,4}\s+\b(?:help|support|assistance)\b",
    re.I,
)

ABUSE_RE = re.compile(
    r"\b(ignore (?:all )?(?:your )?instructions|system prompt|jailbreak|"
    r"dan mode|pretend you(?:'re| are)|act as (?:if|a)|bypass(?: your)? rules|"
    r"forget (?:your )?rules|reveal your (?:prompt|instructions)|"
    r"developer mode|do anything now|you are now|hypothetically ignore|"
    r"override (?:your )?programming|repeat the above|show me your prompt)\b",
    re.I,
)

TROLL_RE = re.compile(
    r"\b(tell me a joke|marry me|are you (?:single|human|a bot|real)|sing a song|"
    r"what llm|which model are you|bet you can'?t|ignore xenko|"
    r"let'?s play a game|roleplay as|speak like a pirate)\b",
    re.I,
)

BOUNDARY_REPLY = (
    "this number is xenko's marketing line - i can only help businesses "
    "looking to grow. if that's you, tell me what you're trying to achieve"
)

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
SLASH_CMD_RE = re.compile(r"^/\w+", re.I)
INJECTED_CTX_RE = re.compile(r"\[(?:PREVIOUS CONVERSATION|NEW MESSAGE|WHATSAPP INTAKE)", re.I)

PREMATURE_CLOSE_RE = re.compile(
    r"alex will be in touch|here'?s what i have|sounds good\?|"
    r"put together a plan|we (?:actually )?just captured|captured this info|"
    r"our team will be in touch|intake complete",
    re.I,
)

BANNED_REPLY_RE = re.compile(
    r"didn'?t catch your name|catch your name|what should i use|"
    r"sounds good\?|❓|✅|✔|✓|👍|🙏|😊|📨|🔍",
    re.I,
)

# Emoji and symbol blocks (WhatsApp sales line: plain text only)
EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002700-\U000027BF"
    "\U00002600-\U000026FF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "]+",
    flags=re.UNICODE,
)

SHORT_ACK_RE = re.compile(
    r"^(?:ok(?:ay)?|thanks|thank you|cool|great|fine|yes|yep|yeah|sure|got it)(?:[.!?\s]|$)",
    re.I,
)

TOOL_LEAK_RE = re.compile(
    r"send_message|crm_add_lead|intake complete|session_search|"
    r"📨|🔍|⚙️|tool_progress|delegate_task|"
    r"to whatsapp:\s*[\"']",
    re.I,
)

MARKDOWN_RE = re.compile(r"[*#_~`|\\[\]{}]")
BULLET_LINE_RE = re.compile(r"^[\s]*[-–—*•]+\s*", re.M)
OPTION_MENU_RE = re.compile(r"\s*[—–-]\s*.+customers.+awareness", re.I)


def _platform_is_whatsapp(platform: str | None) -> bool:
    p = str(platform or "").lower()
    return p in ("whatsapp", "platform.whatsapp") or "whatsapp" in p


def _guard_applies(session_id: str | None = None, **kwargs: Any) -> bool:
    """True when this turn is WhatsApp sales intake (platform set or session tracked)."""
    platform = kwargs.get("platform")
    session_id = session_id or kwargs.get("session_id") or ""
    if _platform_is_whatsapp(platform):
        return True
    for key in ("sender_id", "user_id", "chat_id"):
        raw = str(kwargs.get(key) or "")
        if "@lid" in raw or "@c.us" in raw:
            return True
    if session_id and session_id in _session_step and _session_step[session_id].get("in_intake"):
        return True
    if session_id and session_id in _session_intake_closed:
        return True
    return False


def _phone_keys(phone: str | None) -> set[str]:
    digits = re.sub(r"[^\d]", "", phone or "")
    if not digits:
        return set()
    keys = {digits}
    if digits.startswith("94") and len(digits) >= 11:
        keys.add("0" + digits[2:])
    elif digits.startswith("0") and len(digits) >= 10:
        keys.add("94" + digits[1:])
    return keys


def _phone_row_matches(stored_phone: str, keys: set[str]) -> bool:
    digits = re.sub(r"[^\d]", "", stored_phone or "")
    if not digits:
        return False
    if digits in keys:
        return True
    if digits.startswith("94") and ("0" + digits[2:]) in keys:
        return True
    if digits.startswith("0") and ("94" + digits[1:]) in keys:
        return True
    return False


def _jsonl_history_paths() -> list[Path]:
    paths: list[Path] = []
    paths.append(_hermes_home() / ".gbrain_history.jsonl")
    for env_key in ("XENKO_HERMES_ROOT", "HERMES_PROJECT", "HERMES_CONFIG_DIR"):
        val = os.environ.get(env_key)
        if val:
            paths.append(Path(val) / ".gbrain_history.jsonl")
    plug = Path(__file__).resolve().parent
    for parent in (plug.parent.parent, plug.parent.parent.parent):
        candidate = parent / ".gbrain_history.jsonl"
        if candidate.exists():
            paths.append(candidate)
    repo = Path(r"E:\Freelancer\Akeel\Local-Setup\hermes\.gbrain_history.jsonl")
    if repo.exists():
        paths.append(repo)
    seen: set[str] = set()
    unique: list[Path] = []
    for p in paths:
        key = str(p.resolve()) if p.exists() else str(p)
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def _conversation_from_jsonl(phone: str | None, limit: int = 50) -> list[dict[str, Any]]:
    keys = _phone_keys(phone)
    if not keys:
        return []
    rows: list[dict[str, Any]] = []
    for path in _jsonl_history_paths():
        if not path.exists():
            continue
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                if not _phone_row_matches(str(row.get("phone") or ""), keys):
                    continue
                role = str(row.get("role") or "user").lower()
                if role == "person":
                    continue
                if role not in ("user", "assistant"):
                    role = "user" if role == "human" else "assistant"
                rows.append({"role": role, "message": str(row.get("message") or "")})
        except Exception:
            continue
        if rows:
            break
    return rows[-limit:]


def _conversation_from_state_db(phone: str | None, limit: int = 40) -> list[dict[str, Any]]:
    keys = _phone_keys(phone)
    if not keys:
        return []
    state_db = _hermes_home() / "state.db"
    if not state_db.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        conn = sqlite3.connect(str(state_db))
        cur = conn.cursor()
        cur.execute(
            "SELECT id, user_id FROM sessions WHERE source = 'whatsapp' ORDER BY rowid DESC LIMIT 80"
        )
        sessions = cur.fetchall()
        for sid, user_id in sessions:
            resolved = _phone_from_identifier(str(user_id or ""))
            if not resolved or not _phone_row_matches(resolved, keys):
                continue
            cur.execute(
                """
                SELECT role, content FROM messages
                WHERE session_id = ?
                ORDER BY timestamp ASC
                LIMIT ?
                """,
                (sid, limit),
            )
            for role, content in cur.fetchall():
                r = str(role or "").lower()
                if r in ("user", "human"):
                    rows.append({"role": "user", "message": str(content or "")})
                elif r in ("assistant", "ai", "bot"):
                    rows.append({"role": "assistant", "message": str(content or "")})
            if rows:
                break
        conn.close()
    except Exception:
        pass
    return rows[-limit:]


def _conversation_from_postgres(phone: str | None, limit: int = 50) -> list[dict[str, Any]]:
    keys = _phone_keys(phone)
    if not keys:
        return []
    password = os.environ.get("POSTGRES_PASSWORD", "").strip()
    if not password:
        return []
    try:
        import psycopg2

        conn = psycopg2.connect(
            host=os.environ.get("GBRAIN_PG_HOST", "zephyr.proxy.rlwy.net"),
            port=int(os.environ.get("GBRAIN_PG_PORT", "24385")),
            database=os.environ.get("GBRAIN_PG_DB", "railway"),
            user=os.environ.get("GBRAIN_PG_USER", "postgres"),
            password=password,
        )
        cur = conn.cursor()
        for key in keys:
            variants = {key, "+" + key if key.startswith("94") else key}
            if key.startswith("94"):
                variants.add("0" + key[2:])
            for variant in variants:
                cur.execute(
                    """
                    SELECT role, message FROM conversations
                    WHERE regexp_replace(phone, '[^0-9]', '', 'g') = regexp_replace(%s, '[^0-9]', '', 'g')
                    ORDER BY id DESC LIMIT %s
                    """,
                    (variant, limit),
                )
                fetched = cur.fetchall()
                if fetched:
                    conn.close()
                    return [
                        {"role": r[0], "message": r[1]}
                        for r in reversed(fetched)
                    ]
        conn.close()
    except Exception:
        pass
    return []


def _intake_started_marker(text: str) -> bool:
    if _is_welcome_greeting_message(text):
        return False
    low = (text or "").lower()
    return (
        "your name" in low
        or "company called" in low
        or "name of your business" in low
        or "before we get started" in low
    )


def _assistant_closed(messages: list[tuple[str, str]]) -> bool:
    for role, content in messages:
        if role != "assistant":
            continue
        low = content.lower()
        if CLOSE_LINE_LEGACY in low:
            return True
        if "founder will personally review" in low:
            return True
        if CLOSE_LINE[:40].lower() in low:
            return True
    return False


def _completed_intake_in_messages(messages: list[tuple[str, str]], include_current_session: bool = True) -> bool:
    """Prior full intake even if founder close line was never logged.
    
    Args:
        messages: The messages to check
        include_current_session: If False, only check for completed close line (used to avoid
            false positives when checking prior history during active intake)
    """
    if _assistant_closed(messages):
        return True
    
    # If we're checking prior history, don't use the "phone step answered" heuristic
    # because the current session's answer will trigger false positives
    if not include_current_session:
        return False
        
    user_texts = [c for r, c in messages if r == "user"]
    asst = " ".join(c.lower() for r, c in messages if r == "assistant")
    asked_intake = _intake_started_marker(asst)
    asked_phone = "best number to reach you" in asst
    if asked_intake and asked_phone and len(user_texts) >= INTAKE_ANSWERS_REQUIRED:
        return True
    if asked_intake and asked_phone and "budget" in asst and len(user_texts) >= 6:
        return True
    return False


def _parse_budget_amount_lkr(text: str) -> int | None:
    raw = (text or "").strip()
    if not raw:
        return None
    match = re.search(
        r"(\d+(?:\.\d+)?)\s*(k|m|lakh|lakhs|million|mn|usd|\$|rs\.?|lkr)?",
        raw,
        re.I,
    )
    if not match:
        return None
    amount = float(match.group(1))
    unit = (match.group(2) or "").lower()
    if unit == "k":
        amount *= 1000
    elif unit in ("m", "million", "mn"):
        amount *= 1_000_000
    elif unit in ("lakh", "lakhs"):
        amount *= 100_000
    return int(amount)


def _detect_lead_type(user_message: str, messages: list[tuple[str, str]] | None = None) -> str:
    combined = (user_message or "").lower()
    if messages:
        combined += " " + " ".join(
            c.lower() for r, c in messages if r == "user"
        )
    if PRICE_WEB_RE.search(combined) or WEB_INTAKE_RE.search(combined):
        return "web"
    if re.search(r"\b(marketing|social media|ads?|brand awareness)\b", combined, re.I):
        return "marketing"
    if re.search(r"\bnot sure what i need|don't know what i need\b", combined, re.I):
        return "unsure"
    return "general"


def _below_budget_close_line(name: str = "") -> str:
    who = name.strip() or "there"
    return (
        f"thank you for reaching out, {who}. our founder will review your requirements "
        "and get in touch with you shortly to discuss the best options available "
        "for your budget and timeline."
    )


def _close_line_for_session(session_id: str | None, contact: dict[str, str] | None = None) -> str:
    sid = session_id or ""
    if sid in _session_below_budget:
        name = (contact or {}).get("name") or (_session_contact.get(sid) or {}).get("name") or ""
        return _below_budget_close_line(name)
    return CLOSE_LINE


def _first_name(full_name: str) -> str:
    parts = (full_name or "").strip().split()
    return parts[0] if parts else ""


def _extract_industry_from_history(messages: list[tuple[str, str]]) -> str:
    for i, (role, content) in enumerate(messages):
        if role != "assistant":
            continue
        low = content.lower()
        if "kind of business" in low or "what industry" in low:
            for j in range(i + 1, min(i + 5, len(messages))):
                if messages[j][0] != "user":
                    continue
                text = messages[j][1].strip()
                if text and not EMAIL_RE.search(text) and len(text) < 80:
                    if not NEW_LEAD_RE.search(text) and not SAME_PROJECT_RE.search(text):
                        return text[:80]
    return ""


def _infer_prior_service(messages: list[tuple[str, str]]) -> str:
    text = " ".join(c.lower() for r, c in messages if r == "user")
    if re.search(r"\b(website|web design|company site|need a site)\b", text, re.I):
        if not re.search(r"\b(marketing|social media|content marketing)\b", text, re.I):
            return "website"
    if re.search(r"\b(marketing|social media|content marketing)\b", text, re.I):
        return "marketing"
    return "project"


def _detect_returning_new_intent(user_message: str) -> str | None:
    text = (user_message or "").lower()
    if WEB_INTAKE_RE.search(text) or re.search(r"\b(new website|need a website)\b", text, re.I):
        return "web"
    if re.search(
        r"\b(marketing|social media|content marketing|more leads|help with marketing)\b",
        text,
        re.I,
    ):
        return "marketing"
    return None


def _enrich_prior_contact(
    phone: str | None,
    messages: list[tuple[str, str]],
) -> dict[str, str]:
    contact = _prior_contact(phone, messages)
    merged = _merged_messages(None, phone) if phone else messages
    company = _guess_company(merged, phone)
    industry = _extract_industry_from_history(merged)
    if company and not contact.get("company"):
        contact["company"] = company
    if industry:
        contact["industry"] = industry
    contact["prior_service"] = _infer_prior_service(merged)
    return contact


def _has_returning_known_profile(prior: dict[str, str]) -> bool:
    return bool((prior.get("name") or "").strip() and (prior.get("company") or "").strip())


def _returning_business_steps_required(prior: dict[str, str]) -> int:
    if NEW_LEAD_RE.search(str(prior.get("_new_company") or "")):
        return RETURNING_NEW_BUSINESS_STEPS
    if _has_returning_known_profile(prior):
        return RETURNING_KNOWN_PROFILE_STEPS
    return RETURNING_NEW_BUSINESS_STEPS


def _returning_welcome_message(prior: dict[str, str]) -> str:
    who = _first_name(prior.get("name") or "") or "there"
    svc = prior.get("prior_service") or "project"
    if svc == "website":
        return (
            f"hi {who}. great to hear from you again. "
            "how have things been since we completed the website project?"
        )
    return (
        f"hi {who}. welcome back. it's been a while since we last spoke. "
        "how has business been?"
    )


def _returning_followup_message() -> str:
    return "glad to hear that. what can we help you with today?"


def _returning_clarify_message(prior: dict[str, str]) -> str:
    who = _first_name(prior.get("name") or "") or "there"
    return (
        f"hi {who}. great to hear from you again. "
        "are you reaching out about your existing project, or is this something new "
        "you'd like help with?"
    )


def _returning_direct_intent_message(prior: dict[str, str], intent: str) -> str:
    who = _first_name(prior.get("name") or "") or "there"
    company = (prior.get("company") or "").strip()
    if intent == "marketing" and company:
        return (
            f"hi {who}. nice to hear from you again. "
            f"i can see we previously worked with {company} on your website. "
            "what would you like to achieve with your marketing efforts?"
        )
    if intent == "web":
        return (
            f"hi {who}. nice to hear from you again. "
            "i remember we previously discussed content marketing for your business. "
            "what are you hoping the new website will help you achieve?"
        )
    return _returning_resume_message(prior)


def _returning_resume_message(prior: dict[str, str]) -> str:
    who = _first_name(prior.get("name") or "") or "there"
    company = (prior.get("company") or "").strip()
    industry = (prior.get("industry") or "").strip()
    if company and industry:
        return (
            f"welcome back, {who}. i still have {company} listed as a {industry} company. "
            "what would you like help with this time?"
        )
    if company:
        return (
            f"welcome back, {who}. i still have {company} on file. "
            "what would you like help with this time?"
        )
    return f"welcome back, {who}. what would you like help with this time?"


def _returning_intake_message(step_n: int, prior: dict[str, str]) -> str:
    if _has_returning_known_profile(prior) and step_n in RETURNING_STEPS_KNOWN:
        return RETURNING_STEPS_KNOWN[step_n]
    return STEPS.get(step_n, STEPS[4])


def _returning_intake_started(messages: list[tuple[str, str]]) -> bool:
    markers = (
        "welcome back",
        "what would you like help with",
        "what are you hoping to achieve",
        "previously worked with",
        "previously discussed",
        "still have ",
        "listed as a",
    )
    asst = " ".join(c.lower() for r, c in messages if r == "assistant")
    return any(m in asst for m in markers)


def _flag_below_budget_web(
    session_id: str | None,
    budget_answer: str,
    lead_type: str,
    *,
    answers: list[str] | None = None,
    phone: str | None = None,
) -> None:
    if lead_type != "web":
        return
    amount = _parse_budget_amount_lkr(budget_answer)
    if amount is None or amount >= WEB_MIN_BUDGET_LKR:
        return
    sid = session_id or ""
    if sid:
        _session_below_budget.add(sid)
    logger.warning(
        "BELOW_MIN_WEB_BUDGET: session=%s amount_lkr=%s answer=%r",
        sid,
        amount,
        budget_answer[:80],
    )
    clean = [a.strip() for a in (answers or []) if (a or "").strip()]
    _notify_below_budget_web(
        session_id=session_id,
        name=clean[0] if len(clean) > 0 else "",
        company=clean[1] if len(clean) > 1 else "",
        industry=clean[2] if len(clean) > 2 else "",
        goal=clean[3] if len(clean) > 3 else "",
        budget_text=budget_answer[:80],
        amount_lkr=amount,
        timeline=clean[5] if len(clean) > 5 else "",
        phone=phone,
        destination="founder",
    )


def _hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME", Path.home() / "AppData/Local" / "hermes"))


def _crm_client_path() -> Path:
    """Prefer repo plugin (dev) over installed Hermes home copy."""
    sibling = Path(__file__).resolve().parent.parent / "xenko-crm" / "crm_client.py"
    if sibling.exists():
        return sibling
    return _hermes_home() / "plugins" / "xenko-crm" / "crm_client.py"


def _phone_from_lid(user_id: str) -> str | None:
    """Map WhatsApp LID (236373558739146@lid) to real phone via bridge mapping file."""
    if "@lid" not in user_id:
        return None
    lid = user_id.split("@")[0]
    mapping = _hermes_home() / "whatsapp" / "session" / f"lid-mapping-{lid}_reverse.json"
    if not mapping.exists():
        return None
    phone = mapping.read_text(encoding="utf-8").strip().strip('"').replace("+", "")
    if phone.isdigit() and len(phone) >= 9:
        return phone
    return None


def _phone_from_identifier(raw: str) -> str | None:
    text = (raw or "").strip()
    if not text:
        return None
    if "@lid" in text:
        return _phone_from_lid(text)
    if "@c.us" in text or "@g.us" in text:
        digits = re.sub(r"[^\d]", "", text.split("@")[0])
        return digits if len(digits) >= 9 else None
    digits = re.sub(r"[^\d]", "", text)
    if len(digits) >= 9:
        return digits
    return None


def _phone_from_session_id(session_id: str | None) -> str | None:
    if not session_id:
        return None
    state_db = _hermes_home() / "state.db"
    if not state_db.exists():
        return None
    try:
        conn = sqlite3.connect(str(state_db))
        cur = conn.cursor()
        cur.execute("SELECT user_id FROM sessions WHERE id = ?", (session_id,))
        row = cur.fetchone()
        conn.close()
        if row and row[0]:
            return _phone_from_identifier(str(row[0]))
    except Exception:
        pass
    return None


def _resolve_phone(session_id: str | None = None, **kwargs: Any) -> str | None:
    for key in ("phone", "user_phone", "from_phone", "sender_id", "user_id"):
        found = _phone_from_identifier(str(kwargs.get(key) or ""))
        if found:
            return found
    source = kwargs.get("source")
    if source is not None:
        for attr in ("chat_id", "user_id", "phone"):
            val = getattr(source, attr, None) if not isinstance(source, dict) else source.get(attr)
            if val:
                found = _phone_from_identifier(str(val))
                if found:
                    return found
    return _phone_from_session_id(session_id)


def _extract_phone(session_id: str | None = None, **kwargs: Any) -> str | None:
    return _resolve_phone(session_id, **kwargs)


def _crm_lead_exists(phone: str) -> bool:
    try:
        import importlib.util

        path = _crm_client_path()
        if not path.exists():
            return False
        spec = importlib.util.spec_from_file_location("xenko_crm_client", path)
        if not spec or not spec.loader:
            return False
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return bool(mod.lead_exists_for_phone(phone))
    except Exception:
        return False


def _had_prior_intake(phone: str | None, messages: list[tuple[str, str]]) -> bool:
    # Mid-flow intake on this number must never be treated as a returning lead.
    if phone and _phone_in_active_intake(phone):
        return False
    """GBrain history, CRM lead row, or this session already completed intake."""
    # Only check for close line in PRIOR messages, not current session
    if _completed_intake_in_messages(messages, include_current_session=False):
        return True
    if not phone:
        return False
    merged = _merged_messages(None, phone)
    # Check prior history only - don't use email heuristic for current session
    if _completed_intake_in_messages(merged, include_current_session=False):
        return True
    try:
        from gbrain_history import conversation_get

        rows = conversation_get(phone, 50)
        gbrain_msgs = [
            ("user" if r.get("role") == "user" else "assistant", str(r.get("message") or ""))
            for r in rows
        ]
        # Check prior GBrain history only - don't use email heuristic
        if _completed_intake_in_messages(gbrain_msgs, include_current_session=False):
            return True
    except Exception:
        pass
    if _crm_lead_exists(phone):
        return True
    return False


def _normalize_messages(messages: list | None) -> list[tuple[str, str]]:
    if not messages:
        return []
    if isinstance(messages[0], dict):
        return _messages_from_history(messages)
    return list(messages)


def _messages_from_history(conversation_history: list | None) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if not conversation_history:
        return out
    for item in conversation_history:
        if isinstance(item, dict):
            role = str(item.get("role") or item.get("type") or "").lower()
            content = str(item.get("content") or item.get("text") or item.get("message") or "")
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            role = str(item[0] or "").lower()
            content = str(item[1] or "")
        else:
            continue
        if role in ("user", "human"):
            out.append(("user", content))
        elif role in ("assistant", "ai", "bot"):
            out.append(("assistant", content))
    return out


def _gbrain_messages(phone: str | None, limit: int = 25) -> list[tuple[str, str]]:
    if not phone:
        return []
    rows: list[dict[str, Any]] = []
    try:
        from gbrain_history import conversation_get

        rows = conversation_get(phone, limit)
    except Exception:
        rows = []
    if not rows:
        rows = _conversation_from_jsonl(phone, limit)
    if not rows:
        rows = _conversation_from_state_db(phone, limit)
    if not rows:
        rows = _conversation_from_postgres(phone, limit)
    return [
        ("user" if r.get("role") == "user" else "assistant", str(r.get("message") or ""))
        for r in rows
    ]


def _merged_messages(conversation_history: list | None, phone: str | None) -> list[tuple[str, str]]:
    session = _messages_from_history(conversation_history)
    gbrain = _gbrain_messages(phone)
    if not gbrain:
        return session
    if not session:
        # Fresh session — do not inject stale GBrain into intake step logic.
        return session
    return gbrain + session if len(gbrain) > len(session) else session + gbrain


def _is_intake_trigger(text: str) -> bool:
    return bool(
        STRONG_INTAKE_RE.search(text)
        or DIRECT_INTAKE_RE.search(text)
        or NEW_LEAD_RE.search(text)
    )


def _greeting_line_sent(messages: list[tuple[str, str]], step: int) -> bool:
    needle = GREETING[:24].lower()
    return any(role == "assistant" and needle in content.lower() for role, content in messages)


def _welcome_message_text() -> str:
    return GREETING


def _greeting_burst_sent(messages: list[tuple[str, str]], session_id: str | None) -> bool:
    welcome = _welcome_message_text()
    needle = GREETING[:20].lower()
    in_messages = any(
        role == "assistant"
        and (needle in content.lower() or welcome.lower() in content.lower())
        for role, content in messages
    )
    if in_messages:
        return True
    # Session flag is only valid when the welcome line is actually in the transcript.
    if session_id and session_id in _session_greeting_burst:
        transcript = _session_transcript.get(session_id or "", [])
        return any(
            role == "assistant"
            and (needle in content.lower() or welcome.lower() in content.lower())
            for role, content in transcript
        )
    return False


def _greeting_complete(messages: list[tuple[str, str]], session_id: str | None = None) -> bool:
    if _greeting_burst_sent(messages, session_id):
        return True
    return _greeting_line_sent(messages, 1)


def _step1_asked(messages: list[tuple[str, str]]) -> bool:
    return _name_question_asked(messages)


def _name_question_asked(messages: list[tuple[str, str]]) -> bool:
    """True once we asked for their personal name (welcome line or step 1)."""
    return any(
        role == "assistant"
        and (_intake_started_marker(content) or _is_welcome_greeting_message(content))
        for role, content in messages
    )


def _in_fresh_intake(session_id: str | None, messages: list[tuple[str, str]]) -> bool:
    """Mid-flow after 'new company' or step 1 — never replay the welcome."""
    sid = session_id or ""
    if _session_returning.get(sid) == "new":
        return True
    if _step1_asked_fresh(session_id, messages):
        return True
    stored = _session_step.get(sid) or {}
    return stored.get("mode") == "intake" and int(stored.get("n") or 0) >= 1


def _next_greeting_step(messages: list[tuple[str, str]]) -> int:
    """1 if greeting still needs sending; 0 if welcome is done."""
    if not _greeting_line_sent(messages, 1):
        return 1
    return 0


def _should_skip_greeting(user_message: str) -> bool:
    """Direct marketing or help ask on first contact — no welcome-only preamble."""
    text = (user_message or "").strip()
    if STRONG_INTAKE_RE.search(text):
        return True
    if NEW_LEAD_RE.search(text):
        return True
    if HELP_SEEKING_RE.search(text):
        return True
    if DIRECT_INTAKE_RE.search(text) and not GREETING_ONLY_RE.match(text):
        if STRONG_INTAKE_RE.search(text):
            return True
        if re.search(
            r"\b(help|need|want|looking)\b.*\b(marketing|ads?|customers|sales)\b",
            text,
            re.I,
        ):
            return True
    return False


def _classify_user_message(user_message: str) -> str:
    text = (user_message or "").strip()
    if not text or SLASH_CMD_RE.match(text) or INJECTED_CTX_RE.search(text):
        return "empty"
    if ABUSE_RE.search(text):
        return "abuse"
    if TROLL_RE.search(text) and not _is_intake_trigger(text):
        return "troll"
    if _should_skip_greeting(text):
        return "direct_intake"
    if GREETING_ONLY_RE.match(text):
        return "greeting"
    return "general"


def _is_welcome_greeting_message(content: str) -> bool:
    """Combined welcome + name line — not a separate intake step for field pairing."""
    low = (content or "").strip().lower()
    if not low:
        return False
    welcome = _welcome_message_text().strip().lower()
    if low == welcome:
        return True
    return low.startswith("hi there. thanks for reaching out") and "what's your name" in low


def _is_timeline_answer(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if MONTH_NAME_RE.search(t):
        return True
    if TIMELINE_ANSWER_RE.search(t):
        return True
    if re.search(r"\b\d{4}\b", t):
        return True
    if re.search(r"\b(asap|soon|immediately|urgent|next\s+week|next\s+month)\b", t, re.I):
        return True
    return False


def _is_valid_name_answer(text: str) -> bool:
    t = (text or "").strip()
    if not t or _is_noise_answer(t):
        return False
    if HELP_SEEKING_RE.search(t):
        return False
    if _is_timeline_answer(t):
        return False
    if re.fullmatch(r"\d+", t):
        return False
    if GREETING_IN_MESSAGE_RE.search(t) and re.search(
        r"\b(need|want|help|website|marketing|customer|business|grow|sell)\b",
        t,
        re.I,
    ):
        return False
    return True


def _assistant_step_number(content: str) -> int:
    """Map an assistant intake line to step 1-7 (0 if not a step)."""
    if _is_welcome_greeting_message(content):
        return 1
    low = (content or "").strip().lower()
    for n in range(7, 0, -1):
        step = STEPS[n].lower()
        if step in low or low == step:
            return n
    key = _detect_intake_step_key(content)
    if not key:
        return 0
    for n, field in STEP_FIELD_MAP.items():
        if field == key:
            return n
    return 0


def _highest_filled_step(fields: dict[str, str]) -> int:
    highest = 0
    for n, key in STEP_FIELD_MAP.items():
        if (fields.get(key) or "").strip():
            highest = max(highest, n)
    return highest


def _accept_answer_for_step(
    step_n: int,
    text: str,
    fields: dict[str, str],
    phone: str | None,
) -> bool:
    t = (text or "").strip()
    if not t or _is_noise_answer(t):
        return False
    key = STEP_FIELD_MAP.get(step_n, "")
    if not key:
        return False
    if step_n == 1 and not _is_valid_name_answer(t):
        return False
    if step_n == 5 and not re.search(r"\d", t):
        return False
    if step_n == 7:
        return bool(
            _extract_phone_from_text(t)
            or _user_confirms_current_phone(t)
            or t
        )
    return True


def _apply_step_answer(
    step_n: int,
    text: str,
    fields: dict[str, str],
    phone: str | None,
) -> None:
    if not _accept_answer_for_step(step_n, text, fields, phone):
        return
    key = STEP_FIELD_MAP[step_n]
    if step_n == 7:
        fields[key] = _resolve_contact_phone(text, phone)
    else:
        fields[key] = text[:200]


def _phone_field_keys(phone: str | None) -> list[str]:
    return [k for k in _phone_keys(phone) if k]


def _session_field_store(session_id: str | None, phone: str | None) -> dict[str, str]:
    sid = session_id or ""
    if sid and _session_fields.get(sid):
        return dict(_session_fields[sid])
    store: dict[str, str] = {}
    if phone and _phone_in_active_intake(phone):
        for key in _phone_field_keys(phone):
            for fk, fv in (_phone_intake_fields.get(key) or {}).items():
                if fv and not store.get(fk):
                    store[fk] = fv
    return store


def _persist_session_fields(
    session_id: str | None,
    phone: str | None,
    fields: dict[str, str],
) -> dict[str, str]:
    sid = session_id or ""
    clean = {k: v for k, v in fields.items() if (v or "").strip()}
    if sid:
        _session_fields[sid] = clean
    for key in _phone_field_keys(phone):
        _phone_intake_fields[key] = dict(clean)
    return clean


def _capture_reply_for_last_question(
    session_id: str | None,
    phone: str | None,
    messages: list[tuple[str, str]],
    user_message: str,
) -> dict[str, str]:
    """
    Pair the latest user line with the last intake question we asked.
    Primary source of truth — survives polluted GBrain / duplicate assistant lines.
    """
    if not (user_message or "").strip():
        return _session_field_store(session_id, phone)
    scope = list(messages)
    if scope and scope[-1] == ("user", user_message.strip()):
        scope = scope[:-1]
    window = _intake_window(scope) if scope else []
    prior_fields = _extract_intake_fields_from_window(window, phone) if window else {}
    store = _session_field_store(session_id, phone)
    merged_prior = dict(prior_fields)
    for key, val in store.items():
        if (val or "").strip():
            merged_prior[key] = val
    answering_step = _last_unanswered_intake_step(scope, merged_prior)
    if not answering_step:
        return _session_field_store(session_id, phone)
    fields = _session_field_store(session_id, phone)
    _apply_step_answer(answering_step, user_message, fields, phone)
    return _persist_session_fields(session_id, phone, fields)


def _merged_intake_fields(
    window: list[tuple[str, str]],
    phone: str | None,
    session_id: str | None = None,
) -> dict[str, str]:
    window_fields = _extract_intake_fields_from_window(window, phone)
    session_fields = _session_field_store(session_id, phone)
    merged = dict(window_fields)
    for key, val in session_fields.items():
        if (val or "").strip():
            merged[key] = val
    return merged


def _clear_field_stores(phone: str | None, session_id: str | None = None) -> None:
    sid = session_id or ""
    if sid:
        _session_fields.pop(sid, None)
    for key in _phone_field_keys(phone):
        _phone_intake_fields.pop(key, None)


def _is_name_step_question(content: str) -> bool:
    if _is_welcome_greeting_message(content):
        return False
    low = (content or "").lower()
    if "name of your business" in low or "business called" in low:
        return False
    return (
        "before we get started" in low
        or "what's your name" in low
        or ("your name" in low and "business" not in low and "company" not in low)
    )


def _detect_intake_step_key(content: str) -> str | None:
    if _is_welcome_greeting_message(content):
        return None
    low = (content or "").lower()
    if _is_name_step_question(content):
        return "name"
    if "name of your business" in low or "business called" in low:
        return "company"
    if "kind of business" in low or "what industry" in low:
        return "industry"
    if (
        "hoping to achieve" in low
        or "help you achieve" in low
        or "help with" in low
        or "hoping we can help" in low
    ):
        return "goal"
    if "budget" in low:
        return "budget"
    if "when would you" in low or "get started" in low or "completed" in low:
        return "timeline"
    if "best number to reach you" in low:
        return "contact"
    return None


def _is_noise_answer(text: str) -> bool:
    if not text or SLASH_CMD_RE.match(text) or INJECTED_CTX_RE.search(text):
        return True
    if GREETING_ONLY_RE.match(text.strip()):
        return True
    if HELP_SEEKING_RE.search(text):
        return True
    if NEW_LEAD_RE.search(text) or SAME_PROJECT_RE.search(text):
        return True
    if _is_intake_trigger(text) and STRONG_INTAKE_RE.search(text):
        return True
    return False


def _extract_intake_fields_from_window(
    window: list[tuple[str, str]],
    phone: str | None = None,
) -> dict[str, str]:
    """
    Map user replies to intake fields by pairing assistant questions with the next user line.
    Monotonic: duplicate earlier questions in polluted history cannot steal later answers.
    """
    fields: dict[str, str] = {}
    pending_step = 0
    for role, content in window:
        if role == "assistant":
            step_n = _assistant_step_number(content)
            if step_n:
                pending_step = step_n
            continue
        if role != "user" or not pending_step:
            continue
        text = content.strip()
        if not _accept_answer_for_step(pending_step, text, fields, phone):
            continue
        highest = _highest_filled_step(fields)
        key = STEP_FIELD_MAP[pending_step]
        if pending_step < highest and (fields.get(key) or "").strip():
            pending_step = 0
            continue
        _apply_step_answer(pending_step, text, fields, phone)
        pending_step = 0
    return fields


def _intake_window(messages: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Messages for the current intake block, from the first intake question onward."""
    start = _intake_block_start(messages)
    block = messages[start:]
    for i, (role, content) in enumerate(block):
        if role == "assistant" and (
            _is_name_step_question(content) or _is_welcome_greeting_message(content)
        ):
            return block[i:]
    for i, (role, content) in enumerate(block):
        if role == "assistant" and _intake_started_marker(content):
            return block[i:]
    return block


def _fresh_project_start_index(messages: list[tuple[str, str]]) -> int:
    """Last 'new company' (etc.) in this session — start of a new project intake."""
    start = 0
    for i, (role, content) in enumerate(messages):
        if role == "user" and NEW_LEAD_RE.search(content):
            start = i
    return start


def _active_intake_window(
    session_id: str | None,
    messages: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """
    Messages that count for the current intake.
    After 'new company', ignore older GBrain/session answers (Thomsans etc.).
    """
    sid = session_id or ""
    if _session_returning.get(sid) == "new":
        segment = messages[_fresh_project_start_index(messages) :]
        return _intake_window(segment)
    return _intake_window(messages)


def _step1_asked_fresh(
    session_id: str | None,
    messages: list[tuple[str, str]],
) -> bool:
    sid = session_id or ""
    if _session_returning.get(sid) == "new":
        segment = messages[_fresh_project_start_index(messages) :]
        return any(
            role == "assistant" and _intake_started_marker(content)
            for role, content in segment
        )
    return _step1_asked(messages)


def _merge_message_lists(
    existing: list[tuple[str, str]],
    incoming: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """Append only the non-overlapping suffix — avoids duplicate replay from gateway history."""
    if not incoming:
        return list(existing)
    if not existing:
        return list(incoming)
    max_ov = min(len(existing), len(incoming))
    overlap = 0
    for n in range(max_ov, 0, -1):
        if existing[-n:] == incoming[:n]:
            overlap = n
            break
    return existing + incoming[overlap:]


def _accumulate_session_transcript(
    session_id: str | None,
    conversation_history: list | None,
    user_message: str = "",
) -> list[tuple[str, str]]:
    """Build full in-session thread; gateway history is often just the latest user line."""
    sid = session_id or ""
    if not sid:
        return _messages_from_history(conversation_history)
    transcript = list(_session_transcript.get(sid) or [])
    transcript = _merge_message_lists(
        transcript, _messages_from_history(conversation_history)
    )
    if user_message:
        um = ("user", user_message)
        transcript = _merge_message_lists(transcript, [um])
    _session_transcript[sid] = transcript
    return transcript


def _phone_in_active_intake(phone: str | None) -> bool:
    keys = _phone_keys(phone)
    return bool(keys and keys & _phones_in_active_intake)


def _mark_intake_active(phone: str | None, session_id: str | None = None) -> None:
    for key in _phone_keys(phone):
        _phones_in_active_intake.add(key)


def _clear_intake_active(phone: str | None, session_id: str | None = None) -> None:
    sid = session_id or ""
    if sid:
        _session_fresh_intake.discard(sid)
    for key in _phone_keys(phone):
        _phones_in_active_intake.discard(key)
        _session_freshly_synced.discard(key)
    _clear_field_stores(phone, session_id)


def _persistent_conversation(phone: str | None, limit: int = 60) -> list[tuple[str, str]]:
    """Best available history from GBrain jsonl, state.db, or postgres (survives redeploy/sync)."""
    if not phone:
        return []
    chunks: list[list[tuple[str, str]]] = []
    gb = _gbrain_messages(phone, limit)
    if gb:
        chunks.append(gb)
    jl = [
        (str(r.get("role") or "user"), str(r.get("message") or ""))
        for r in _conversation_from_jsonl(phone, limit)
    ]
    if jl:
        chunks.append(jl)
    st = [
        (str(r.get("role") or "user"), str(r.get("message") or ""))
        for r in _conversation_from_state_db(phone, limit)
    ]
    if st:
        chunks.append(st)
    pg = [
        (str(r.get("role") or "user"), str(r.get("message") or ""))
        for r in _conversation_from_postgres(phone, limit)
    ]
    if pg:
        chunks.append(pg)
    if not chunks:
        return []
    return max(chunks, key=len)


def _intake_block_start(messages: list[tuple[str, str]]) -> int:
    """Index of the first name question in the current intake block."""
    start = 0
    for i, (role, content) in enumerate(messages):
        if role == "assistant" and _assistant_closed([(role, content)]):
            start = i + 1
    for i in range(start, len(messages)):
        role, content = messages[i]
        if role == "user" and NEW_LEAD_RE.search(content):
            start = i
    for i in range(start, len(messages)):
        role, content = messages[i]
        if role != "assistant":
            continue
        low = content.lower()
        if "before we get started" in low or (
            "what's your name" in low
            or ("your name" in low and "business" not in low and "company" not in low)
        ):
            return i
    for i in range(start, len(messages)):
        role, content = messages[i]
        if role == "assistant" and _intake_started_marker(content):
            return i
    return start


def _current_intake_segment(messages: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Messages for the intake currently in progress (after last close / new-company marker)."""
    if not messages:
        return []
    start = _intake_block_start(messages)
    segment = messages[start:]
    if not segment or _closed_in_window(segment):
        return []
    return segment


def _backfill_session_transcript(session_id: str | None, phone: str | None) -> None:
    """
    Gateway often sends one turn; GBrain/state.db may have the full thread after sync.
    Append-only merge — never replace in-memory transcript; never append after a close line.
    """
    sid = session_id or ""
    if not sid or not phone:
        return
    if sid in _session_fresh_intake:
        return
    segment = _current_intake_segment(_persistent_conversation(phone))
    if not segment:
        return
    transcript = list(_session_transcript.get(sid) or [])
    if not transcript:
        # Redeploy / sparse gateway — hydrate mid-intake thread from persistent store.
        _session_transcript[sid] = list(segment)
        return
    if not any(role == "assistant" for role, _ in transcript):
        if _segment_has_active_intake(segment, sid):
            merged = list(segment)
            for msg in transcript:
                if msg not in merged:
                    merged.append(msg)
            _session_transcript[sid] = merged
        return
    if _assistant_closed(transcript):
        return
    merged = list(transcript)
    for msg in segment:
        if msg not in merged:
            merged.append(msg)
    if len(merged) > len(transcript):
        _session_transcript[sid] = merged


def _segment_has_active_intake(
    segment: list[tuple[str, str]],
    session_id: str | None = None,
) -> bool:
    if not segment or _closed_in_window(segment):
        return False
    answers = _user_answers(segment, phone=None, session_id=session_id)
    if not answers:
        return False
    if len(answers) < INTAKE_ANSWERS_REQUIRED:
        return True
    if not _phone_step_asked(segment, session_id):
        return True
    return False


def _conversation_scope(
    session_id: str | None,
    messages: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """Prefer in-memory session transcript over stale GBrain for this chat."""
    transcript = _session_transcript.get(session_id or "", [])
    return transcript if transcript else messages


def _history_dicts_from_transcript(session_id: str | None) -> list[dict[str, str]]:
    return [
        {"role": role, "content": content}
        for role, content in _session_transcript.get(session_id or "", [])
    ]


def _record_assistant_in_transcript(session_id: str | None, text: str) -> None:
    sid = session_id or ""
    if not sid or not (text or "").strip():
        return
    transcript = _session_transcript.setdefault(sid, [])
    msg = ("assistant", text.strip())
    if not transcript or transcript[-1] != msg:
        transcript.append(msg)


def _last_intake_step_asked(window: list[tuple[str, str]]) -> int:
    for role, content in reversed(window):
        if role != "assistant":
            continue
        step_n = _assistant_step_number(content)
        if step_n:
            return step_n
    return 0


def _last_unanswered_intake_step(
    messages: list[tuple[str, str]],
    fields: dict[str, str],
) -> int:
    """Last intake question that does not yet have a captured answer (skips duplicate orphans)."""
    for role, content in reversed(messages):
        if role != "assistant":
            continue
        step_n = _assistant_step_number(content)
        if not step_n:
            continue
        key = STEP_FIELD_MAP.get(step_n, "")
        if key and (fields.get(key) or "").strip():
            continue
        return step_n
    return 0


def _next_intake_step_number(
    window: list[tuple[str, str]],
    answers: list[str],
    phone: str | None = None,
    session_id: str | None = None,
) -> int:
    """Next question index from merged fields and the last intake line we sent."""
    fields = _merged_intake_fields(window, phone, session_id)
    if not fields.get("name"):
        return 1
    if not fields.get("company"):
        return 2
    if not fields.get("industry"):
        return 3
    if not fields.get("goal"):
        return 4
    if not fields.get("budget"):
        return 5
    if not fields.get("timeline"):
        return 6
    if not fields.get("contact") and not _phone_step_asked(window, session_id):
        return 7
    if not fields.get("contact"):
        return 7
    last_asked = _last_intake_step_asked(window)
    if last_asked and len(answers) >= last_asked:
        return min(last_asked + 1, 7)
    return min(len(answers) + 1, 7)


def _user_answers(
    window: list[tuple[str, str]],
    phone: str | None = None,
    session_id: str | None = None,
) -> list[str]:
    fields = _merged_intake_fields(window, phone, session_id)
    ordered: list[str] = []
    for key in ("name", "company", "industry", "goal", "budget", "timeline", "contact"):
        val = fields.get(key, "").strip()
        if val:
            ordered.append(val)
    if ordered:
        return ordered
    answers: list[str] = []
    for role, content in window:
        if role != "user":
            continue
        text = content.strip()
        if _is_noise_answer(text):
            continue
        answers.append(text)
    return answers


def _in_active_intake(window: list[tuple[str, str]], messages: list[tuple[str, str]]) -> bool:
    if not window:
        return False
    joined = " ".join(c for r, c in window if r == "user")
    if _is_intake_trigger(joined):
        return True
    if _greeting_complete(messages, None):
        return True
    intake_markers = (
        "your name",
        "company called",
        "name of your business",
        "kind of business",
        "hoping to achieve",
        "budget",
        "best number to reach you",
    )
    assistant_text = " ".join(c.lower() for r, c in window if r == "assistant")
    return any(m in assistant_text for m in intake_markers)


def _closed_in_window(window: list[tuple[str, str]]) -> bool:
    return _assistant_closed(window)


def _session_has_active_intake(
    session_id: str | None,
    session_messages: list[tuple[str, str]],
    phone: str | None = None,
) -> bool:
    """
    True when this WhatsApp session already has intake in progress.
    Must block returning-customer flow even if GBrain/CRM shows an older completed lead.
    """
    sid = session_id or ""
    if sid in _session_intake_closed:
        return False

    if phone and _phone_in_active_intake(phone):
        return True

    transcript = _session_transcript.get(sid) or session_messages
    if phone and sid not in _session_fresh_intake:
        if not transcript:
            _backfill_session_transcript(sid, phone)
            transcript = _session_transcript.get(sid) or session_messages
        if transcript and any(role == "assistant" for role, _ in transcript):
            _backfill_session_transcript(sid, phone)
            segment = _current_intake_segment(_persistent_conversation(phone))
            if _segment_has_active_intake(segment, sid):
                _mark_intake_active(phone, sid)
                return True

    stored = _session_step.get(sid) or {}
    if stored.get("in_intake") and stored.get("mode") in ("intake", "returning_intake"):
        n = int(stored.get("n") or 0)
        if 1 <= n < CLOSE_STEP:
            if phone:
                _mark_intake_active(phone, sid)
            return True

    if not transcript:
        return False

    window = _active_intake_window(session_id, transcript)
    scope = _conversation_scope(session_id, transcript)
    if not window or _closed_in_window(window):
        return False

    user_msgs_in_window = [c for r, c in window if r == "user" and (c or "").strip()]
    if not user_msgs_in_window:
        return False

    answers = _user_answers(window, phone, session_id)
    if answers and (
        len(answers) < INTAKE_ANSWERS_REQUIRED
        or not _phone_step_asked(transcript, session_id)
    ):
        if phone:
            _mark_intake_active(phone, sid)
        return True

    if _step1_asked(transcript) and _in_active_intake(window, scope):
        if phone:
            _mark_intake_active(phone, sid)
        return True

    return False


def _track_intake_step_state(
    session_id: str | None,
    phone: str | None,
    step: dict[str, Any],
) -> None:
    """Remember phones mid-intake so GBrain/CRM cannot hijack the flow after sync."""
    if step.get("mode") == "complete" or int(step.get("n") or 0) >= CLOSE_STEP:
        _clear_intake_active(phone, session_id)
        return
    if not step.get("in_intake"):
        return
    mode = step.get("mode")
    n = int(step.get("n") or 0)
    if mode in ("intake", "returning_intake", "email_ask") and 1 <= n < CLOSE_STEP:
        _mark_intake_active(phone, session_id)


def _safe_returning_step(
    session_id: str | None,
    session_messages: list | None,
    user_message: str,
    phone: str | None,
) -> dict[str, Any] | None:
    msgs = _normalize_messages(session_messages)
    if _session_has_active_intake(session_id, msgs, phone):
        return None
    return compute_returning_step(session_id, session_messages, user_message, phone)


def _returning_question_asked(messages: list[tuple[str, str]]) -> bool:
    markers = (
        "great to hear from you again",
        "welcome back",
        "existing project",
        "something new you'd like help with",
        "previous project with us",
        "about your previous project",
    )
    for role, content in messages:
        if role != "assistant":
            continue
        low = content.lower()
        if any(m in low for m in markers):
            return True
    return False


_INVALID_COMPANY_NAMES = frozenset({
    "we",
    "i",
    "hi",
    "hello",
    "yes",
    "no",
    "ok",
    "the",
    "a",
    "more customers",
    "marketing help",
})


def _valid_company_name(name: str) -> bool:
    n = (name or "").strip()
    if not n or len(n) < 2:
        return False
    low = n.lower()
    if low in _INVALID_COMPANY_NAMES:
        return False
    if low.startswith("we ") or low == "we":
        return False
    return True


def _returning_question_for_company(company: str, prior: dict[str, str] | None = None) -> str:
    if prior and (prior.get("name") or "").strip():
        return _returning_clarify_message(prior)
    if _valid_company_name(company):
        return (
            f"great to hear from you again. is this about {company} again, "
            "or is this something new you'd like help with?"
        )
    return RETURNING_QUESTION


def _guess_company(messages: list[tuple[str, str]], phone: str | None) -> str:
    for _role, content in messages:
        if _role != "user":
            continue
        text = content.strip()
        if not text or EMAIL_RE.search(text) or _is_intake_trigger(text) or len(text) > 120:
            continue
        low = text.lower()
        called = re.search(r"(?i)company is called\s+(.+?)(?:\s+and|\s*$)", text)
        if called:
            name = called.group(1).strip().split(",")[0][:60]
            if _valid_company_name(name):
                return name
        if "we are" in low or "we're" in low:
            chunk = re.sub(r"(?i)^we\s+are\s+", "", text)
            chunk = chunk.split(" and we sell")[0].split(" sell ")[0].strip()
            if _valid_company_name(chunk):
                return chunk.split(",")[0].strip()[:60]
        if " sell " in low and not low.strip().startswith("we sell"):
            before = text.split(" sell ")[0].strip()
            if _valid_company_name(before):
                return before.split(",")[0].strip()[:60]
    for start_idx, (role, content) in enumerate(messages):
        if role == "user" and _is_intake_trigger(content):
            window = messages[start_idx + 1 : start_idx + 8]
            for r, c in window:
                if r != "user":
                    continue
                text = c.strip()
                if not text or EMAIL_RE.search(text) or _is_intake_trigger(text):
                    continue
                if len(text) > 80:
                    continue
                name = text.split(",")[0].split(" and ")[0].strip()[:60]
                if _valid_company_name(name):
                    return name
    if phone:
        try:
            from gbrain_history import find_person_by_phone

            person = find_person_by_phone(phone)
            if person and person.get("company"):
                return str(person["company"])[:60]
        except Exception:
            pass
    return ""


def _user_re_engaged(user_message: str) -> bool:
    if not user_message or not user_message.strip():
        return False
    if SLASH_CMD_RE.match(user_message.strip()):
        return False
    if INJECTED_CTX_RE.search(user_message):
        return False
    return True


def _is_returning_new_project(
    session_id: str | None,
    phone: str | None,
    messages: list[tuple[str, str]],
) -> bool:
    sid = session_id or ""
    if _session_returning.get(sid) != "new":
        return False
    return _had_prior_intake(phone, messages)


def _extract_phone_from_text(text: str) -> str | None:
    if not text:
        return None
    for match in PHONE_IN_TEXT_RE.finditer(text):
        digits = re.sub(r"\D", "", match.group(0))
        if len(digits) >= 9:
            if digits.startswith("0") and len(digits) >= 10:
                digits = "94" + digits[1:]
            return digits
    return None


def _normalize_phone_digits(phone: str | None) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("0") and len(digits) >= 10:
        digits = "94" + digits[1:]
    return digits


def _phone_step_asked(
    messages: list[tuple[str, str]],
    session_id: str | None = None,
) -> bool:
    scope = _conversation_scope(session_id, messages)
    return any(
        role == "assistant" and "best number to reach you" in content.lower()
        for role, content in scope
    )


def _user_confirms_current_phone(user_message: str) -> bool:
    text = (user_message or "").strip()
    if not text or _extract_phone_from_text(text):
        return False
    normalized = re.sub(r"[^\w\s'']+$", "", text).strip()
    return bool(
        CONFIRM_PRIOR_PHONE_RE.match(text) or CONFIRM_PRIOR_PHONE_RE.match(normalized)
    )


def _resolve_contact_phone(user_message: str, session_phone: str | None) -> str:
    alt = _extract_phone_from_text(user_message or "")
    if alt:
        return alt
    normalized = _normalize_phone_digits(session_phone)
    if normalized and len(normalized) >= 9:
        return normalized
    return normalized


def _email_confirm_message(email: str) -> str:
    """Legacy alias — contact step is now phone confirmation."""
    return STEPS[7]


def _email_confirm_asked(
    messages: list[tuple[str, str]],
    session_id: str | None = None,
) -> bool:
    return _phone_step_asked(messages, session_id)


def _user_confirms_prior_email(user_message: str) -> bool:
    return _user_confirms_current_phone(user_message)


def _user_declines_prior_email(user_message: str) -> bool:
    text = (user_message or "").strip()
    if not text or _extract_phone_from_text(text):
        return False
    return bool(DECLINE_PRIOR_PHONE_RE.match(text))


def _extract_name_from_history(messages: list[tuple[str, str]]) -> str:
    for i, (role, content) in enumerate(messages):
        if role != "assistant" or "your name" not in content.lower():
            continue
        for j in range(i + 1, min(i + 5, len(messages))):
            if messages[j][0] != "user":
                continue
            text = messages[j][1].strip()
            if not text or EMAIL_RE.search(text) or len(text) > 80:
                continue
            if NEW_LEAD_RE.search(text) or SAME_PROJECT_RE.search(text):
                continue
            return text[:120]
    return ""


def _prior_contact(phone: str | None, messages: list[tuple[str, str]]) -> dict[str, str]:
    """Name and phone from earlier intake on this number (GBrain, session, or Airtable)."""
    merged = _merged_messages(None, phone) if phone else messages
    contact_phone = _normalize_phone_digits(phone)
    name = ""
    history_rows: list[dict[str, Any]] = []
    name = _extract_name_from_history(merged) or name
    if not name and phone:
        history_rows = _conversation_from_jsonl(phone, 80)
        for i, row in enumerate(history_rows):
            if row.get("role") != "assistant":
                continue
            if "your name" not in str(row.get("message") or "").lower():
                continue
            for nxt in history_rows[i + 1 : i + 4]:
                if nxt.get("role") != "user":
                    continue
                text = str(nxt.get("message") or "").strip()
                if text and not EMAIL_RE.search(text) and len(text) < 80:
                    if not NEW_LEAD_RE.search(text):
                        name = text
                        break
    if phone:
        try:
            path = _crm_client_path()
            import importlib.util

            spec = importlib.util.spec_from_file_location("xenko_crm_client_lookup", path)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                person = mod._find_person_by_phone(phone)
                if person:
                    fields = person.get("fields") or {}
                    if not name:
                        first = str(fields.get("First name") or "").strip()
                        last = str(fields.get("last name") or fields.get("Last Name") or "").strip()
                        name = f"{first} {last}".strip()
                lead = mod._find_lead_by_phone(phone)
                if lead:
                    fields = lead.get("fields") or {}
                    if not name:
                        name = str(fields.get("Name") or "").strip()
        except Exception:
            pass
    return {"name": name.strip(), "phone": contact_phone}


def _known_contact(
    session_id: str | None,
    phone: str | None,
    messages: list[tuple[str, str]],
) -> dict[str, str]:
    """Cached name/phone/company for this session (loaded once from GBrain / Airtable)."""
    sid = session_id or ""
    if sid and sid in _session_contact:
        return _session_contact[sid]
    contact = _enrich_prior_contact(phone, messages)
    if sid and (contact.get("phone") or contact.get("name")):
        _session_contact[sid] = contact
    return contact


def _company_from_current_window(window: list[tuple[str, str]]) -> str:
    for role, content in window:
        if role != "user":
            continue
        text = content.strip()
        if not text or NEW_LEAD_RE.search(text) or EMAIL_RE.search(text):
            continue
        if len(text) < 3:
            continue
        low = text.lower()
        if "sell" in low or "company" in low or "called" in low or len(text) > 12:
            chunk = re.sub(r"(?i)^we\s+are\s+", "", text)
            chunk = re.sub(r"(?i)company is called\s+", "", chunk)
            chunk = chunk.split(" and we sell")[0].split(" sell ")[0].strip()
            return chunk.split(",")[0].strip()[:60]
    return ""


def _valid_whatsapp_phone(phone: str | None) -> bool:
    if not phone:
        return False
    norm = re.sub(r"[\s\-+()]", "", phone)
    if norm.startswith("session_") or "@" in norm:
        return False
    digits = re.sub(r"\D", "", norm)
    return len(digits) >= 9


def _load_crm_client():
    import importlib.util

    path = _crm_client_path()
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location("xenko_crm_client_auto", path)
    if not spec or not spec.loader:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sync_crm_on_qualification(
    session_id: str | None,
    phone: str | None,
    contact: dict[str, str],
    answers: list[str],
    *,
    fields: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """
    Deterministic CRM write when intake completes — does not rely on the LLM calling crm_add_lead.
    """
    sid = session_id or ""
    if sid and sid in _session_crm_synced:
        return None
    if not _valid_whatsapp_phone(phone):
        return None

    crm = _load_crm_client()
    if not crm:
        return None

    fld = fields or {}
    parsed = crm.parse_intake_answers(answers, company_hint=contact.get("company") or "")
    name = (fld.get("name") or contact.get("name") or parsed.get("name") or "").strip()
    company = (fld.get("company") or contact.get("company") or parsed.get("company") or "").strip()
    goal = (fld.get("goal") or parsed.get("goal") or "").strip()
    industry = (fld.get("industry") or parsed.get("what_they_sell") or "").strip()
    budget = (fld.get("budget") or "").strip()
    timeline = (fld.get("timeline") or "").strip()
    budget_timeline = "; ".join(p for p in (budget, timeline) if p) or parsed.get("budget_timeline") or ""
    contact_phone = _normalize_phone_digits(contact.get("phone") or phone)
    if not name or name.lower() == "unknown":
        return None
    if not contact_phone or len(contact_phone) < 9:
        return None

    try:
        result = crm.add_or_update_lead(
            name=name,
            phone=contact_phone,
            email="",
            company=company,
            notes=contact.get("notes") or "",
            source="WhatsApp",
            lead_status="Qualified",
            qualification_label=crm.infer_qualification_label(
                budget_timeline,
                goal,
            ),
            what_they_sell=industry or company,
            goal=goal,
            budget_timeline=budget_timeline,
        )
        if sid:
            _session_crm_synced.add(sid)
        if phone:
            for key in _phone_keys(phone):
                _session_freshly_synced.discard(key)
        return result
    except Exception:
        return None


def _intake_notes_from_answers(
    answers: list[str],
    *,
    company: str = "",
    session_id: str | None = None,
) -> str:
    parts: list[str] = []
    if company:
        parts.append(f"Company: {company}")
    if session_id and session_id in _session_below_budget:
        parts.append("BELOW_MIN_WEB_BUDGET")
    lead_type = _session_lead_type.get(session_id or "", "")
    if lead_type:
        parts.append(f"Lead type: {lead_type}")
    labels = (
        "Name",
        "Company",
        "Industry",
        "Outcome",
        "Budget",
        "Timeline",
        "Contact number",
    )
    for i, ans in enumerate(answers[:7]):
        label = labels[i] if i < len(labels) else f"Answer {i + 1}"
        if EMAIL_RE.search(ans) and i < 6:
            continue
        parts.append(f"{label}: {ans[:200]}")
    return "; ".join(parts)[:2000]


def _complete_with_contact(
    session_id: str | None,
    phone: str | None,
    messages: list[tuple[str, str]],
    window: list[tuple[str, str]],
    answers: list[str],
    *,
    preferred_phone: str | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    prior = _prior_contact(phone, messages)
    fields = _merged_intake_fields(window, phone, session_id)
    final_name = (
        fields.get("name")
        or name
        or prior.get("name")
        or (answers[0] if answers else "")
        or ""
    ).strip() or "Unknown"
    final_phone = _normalize_phone_digits(
        preferred_phone or fields.get("contact") or phone
    )
    company = (
        fields.get("company")
        or _company_from_current_window(window)
        or _guess_company(messages, phone)
        or ""
    ).strip()
    structured_answers = _answers_list_from_fields(
        {**fields, "name": final_name, "contact": final_phone}
    )
    notes = _intake_notes_from_answers(structured_answers, company=company, session_id=session_id)
    contact = {"name": final_name, "phone": final_phone, "company": company, "notes": notes}
    crm_result = _sync_crm_on_qualification(
        session_id, final_phone, contact, structured_answers, fields=fields
    )
    close_msg = _close_line_for_session(session_id, contact)
    _notify_qualified_lead(
        session_id=session_id,
        name=final_name,
        company=company,
        industry=fields.get("industry", ""),
        goal=fields.get("goal", ""),
        budget=fields.get("budget", ""),
        timeline=fields.get("timeline", ""),
        phone=final_phone,
        lead_type=_session_lead_type.get(session_id or "", ""),
        below_budget=(session_id or "") in _session_below_budget,
        destination="founder",
    )
    if session_id:
        _session_contact[session_id] = contact
        _session_intake_closed.add(session_id)
        _clear_intake_active(phone, session_id)
        _session_step[session_id] = {
            **(_session_step.get(session_id) or {}),
            "_prior_contact": contact,
            "crm_contact": contact,
            "crm_sync": crm_result,
        }
    return {
        "n": CLOSE_STEP,
        "message": close_msg,
        "in_intake": True,
        "mode": "complete",
        "crm_contact": contact,
    }


def _returning_new_contact_step(
    session_id: str | None,
    messages: list[tuple[str, str]],
    user_message: str,
    phone: str | None,
    window: list[tuple[str, str]],
    answers: list[str],
    *,
    session_messages: list[tuple[str, str]] | None = None,
) -> dict[str, Any] | None:
    """
    Returning client, new company: collect missing business fields, then contact number, then close.
    """
    scope = _conversation_scope(session_id, session_messages or messages)
    if not _is_returning_new_project(session_id, phone, scope):
        return None
    prior = _known_contact(session_id, phone, messages)
    steps_required = _returning_business_steps_required(prior)
    if len(answers) < steps_required:
        return None

    if not _phone_step_asked(messages, session_id):
        return {"n": 7, "message": STEPS[7], "in_intake": True, "mode": "intake"}

    alt_phone = _extract_phone_from_text(user_message or "")
    if alt_phone or _user_confirms_current_phone(user_message) or (user_message or "").strip():
        resolved = _resolve_contact_phone(user_message, phone)
        contact_name = prior.get("name") or (answers[0] if answers else "")
        return _complete_with_contact(
            session_id,
            phone,
            messages,
            window,
            answers,
            preferred_phone=resolved,
            name=contact_name or None,
        )
    return None


def _can_complete_intake(
    window: list[tuple[str, str]],
    answers: list[str],
    phone: str | None = None,
    session_id: str | None = None,
) -> bool:
    if not _phone_step_asked(window, session_id):
        return False
    fields = _merged_intake_fields(window, phone, session_id)
    required = ("name", "company", "industry", "goal", "budget", "timeline")
    if not all((fields.get(k) or "").strip() for k in required):
        return False
    intake_markers = (
        "your name",
        "name of your business",
        "kind of business",
        "hoping to achieve",
        "budget",
        "when would you",
        "get started",
        "best number to reach you",
    )
    assistant_text = " ".join(c.lower() for r, c in window if r == "assistant")
    if not any(m in assistant_text for m in intake_markers):
        return False
    return True


def _answers_list_from_fields(fields: dict[str, str]) -> list[str]:
    return [
        fields.get(key, "").strip()
        for key in ("name", "company", "industry", "goal", "budget", "timeline", "contact")
        if fields.get(key, "").strip()
    ]


def _continue_fresh_intake_step(
    session_id: str | None,
    session_messages: list[tuple[str, str]],
    messages: list[tuple[str, str]],
    user_message: str = "",
    phone: str | None = None,
) -> dict[str, Any] | None:
    """Advance steps 2-5 during fresh intake (returning → new company path)."""
    if not _in_fresh_intake(session_id, session_messages):
        return None
    sid = session_id or ""
    stored = _session_step.get(sid) or {}
    window = _active_intake_window(session_id, session_messages)
    if not _user_answers(window, phone, session_id) and _step1_asked_fresh(session_id, messages):
        window = _active_intake_window(session_id, messages)
    if _step1_asked_fresh(session_id, session_messages) or _step1_asked_fresh(
        session_id, messages
    ):
        answers = _user_answers(window, phone, session_id)
        ret = _returning_new_contact_step(
            session_id,
            messages,
            user_message,
            phone,
            window,
            answers,
            session_messages=session_messages,
        )
        if ret:
            return ret
        prior = _known_contact(sid, phone, messages)
        steps_required = _returning_business_steps_required(prior)
        if (
            _is_returning_new_project(session_id, phone, session_messages)
            and len(answers) >= steps_required
            and not _phone_step_asked(messages, session_id)
        ):
            return {"n": 7, "message": STEPS[7], "in_intake": True, "mode": "intake"}
        if not _can_complete_intake(window, answers, phone, session_id):
            if _is_returning_new_project(session_id, phone, messages):
                if not _returning_intake_started(session_messages) and not _returning_intake_started(
                    messages
                ):
                    intent = _detect_returning_new_intent(user_message)
                    msg = (
                        _returning_direct_intent_message(prior, intent)
                        if intent
                        else _returning_resume_message(prior)
                    )
                    return {
                        "n": 1,
                        "message": msg,
                        "in_intake": True,
                        "mode": "returning_intake",
                    }
                n = min(len(answers) + 1, RETURNING_KNOWN_PROFILE_STEPS)
                if _has_returning_known_profile(prior):
                    if (
                        len(answers) >= RETURNING_KNOWN_PROFILE_STEPS
                        and not _phone_step_asked(messages, session_id)
                    ):
                        return {"n": 7, "message": STEPS[7], "in_intake": True, "mode": "intake"}
                    return {
                        "n": n,
                        "message": _returning_intake_message(n, prior),
                        "in_intake": True,
                        "mode": "returning_intake",
                    }
            n = _next_intake_step_number(window, answers, phone, session_id)
            return {"n": n, "message": STEPS[n], "in_intake": True, "mode": "intake"}
        resolved = _resolve_contact_phone(
            answers[-1] if answers else (user_message or ""),
            phone,
        )
        contact_name = answers[0] if answers else prior.get("name")
        return _complete_with_contact(
            session_id,
            phone,
            messages,
            window,
            answers,
            preferred_phone=resolved,
            name=contact_name or None,
        )
    if int(stored.get("n") or 0) == 1 and stored.get("mode") == "intake":
        window = _active_intake_window(session_id, session_messages)
        answers = _user_answers(window, phone, session_id)
        n = _next_intake_step_number(window, answers, phone, session_id)
        return {"n": n, "message": STEPS[n], "in_intake": True, "mode": "intake"}
    return None


def compute_greeting_step(
    messages: list[tuple[str, str]],
    user_message: str,
    session_id: str | None = None,
) -> dict[str, Any] | None:
    """Welcome sequence: all 3 lines after hi, then wait for one reply before intake."""
    if _in_fresh_intake(session_id, messages):
        return None
    if _should_skip_greeting(user_message):
        return None
    if _greeting_complete(messages, session_id):
        return None

    kind = _classify_user_message(user_message)
    if kind not in ("greeting", "general"):
        return None

    if not _greeting_burst_sent(messages, session_id) and not _greeting_line_sent(messages, 1):
        return {
            "mode": "greeting_burst",
            "message": _welcome_message_text(),
            "in_intake": True,
            "n": 0,
        }

    n = _next_greeting_step(messages)
    if n == 0:
        return None
    return {
        "mode": "greeting",
        "greeting_n": n,
        "message": GREETING,
        "in_intake": True,
        "n": 0,
    }


def compute_boundary_step(user_message: str) -> dict[str, Any] | None:
    kind = _classify_user_message(user_message)
    if kind == "abuse":
        return {
            "mode": "boundary",
            "message": BOUNDARY_REPLY,
            "in_intake": True,
            "n": 0,
        }
    if kind == "troll":
        return {
            "mode": "boundary",
            "message": BOUNDARY_REPLY,
            "in_intake": True,
            "n": 0,
        }
    return None


def compute_returning_step(
    session_id: str | None,
    session_messages: list | None,
    user_message: str,
    phone: str | None,
) -> dict[str, Any] | None:
    """session_messages = this WhatsApp session only; GBrain/CRM used for prior detection."""
    messages = _normalize_messages(session_messages)
    sid = session_id or ""
    prior_state = _session_returning.get(sid)

    if prior_state == "new":
        return None

    if prior_state == "same":
        return {
            "mode": "returning_same",
            "n": CLOSE_STEP,
            "message": CLOSE_LINE_LEGACY,
            "in_intake": True,
        }

    if not _had_prior_intake(phone, messages):
        return None

    if phone and _phone_in_active_intake(phone):
        return None

    # Never hijack a fresh intake mid-flow (common when GBrain/CRM has an old completed lead).
    if _session_has_active_intake(session_id, messages, phone):
        return None

    all_msgs = _merged_messages(session_messages, phone) if phone else messages

    # Only check for close line - don't use phone-step heuristic for "prior complete"
    # because the current session's answers will false-positive
    prior_complete = _completed_intake_in_messages(messages, include_current_session=False) or _completed_intake_in_messages(
        all_msgs, include_current_session=False
    )
    if not prior_complete:
        transcript = _session_transcript.get(sid) or []
        fresh_hello = GREETING_ONLY_RE.match((user_message or "").strip()) and len(
            transcript
        ) <= 1
        if not fresh_hello:
            window = _intake_window(transcript or messages)
            scope = transcript or messages
            if _in_active_intake(window, scope) and not _closed_in_window(window):
                return None

    if not _user_re_engaged(user_message):
        return None

    prior = _known_contact(sid, phone, all_msgs)

    intent = _detect_returning_new_intent(user_message)
    if intent and _has_returning_known_profile(prior):
        if sid:
            _session_returning[sid] = "new"
        return {
            "mode": "returning_intake",
            "n": 1,
            "message": _returning_direct_intent_message(prior, intent),
            "in_intake": True,
        }

    if NEW_LEAD_RE.search(user_message):
        if sid:
            _session_returning[sid] = "new"
            if phone:
                _known_contact(sid, phone, messages)
        if _has_returning_known_profile(prior):
            return {
                "mode": "returning_intake",
                "n": 1,
                "message": _returning_resume_message(prior),
                "in_intake": True,
            }
        return None

    if SAME_PROJECT_RE.search(user_message) and not STRONG_INTAKE_RE.search(user_message):
        if sid:
            _session_returning[sid] = "same"
        return {
            "mode": "returning_same",
            "n": CLOSE_STEP,
            "message": CLOSE_LINE_LEGACY,
            "in_intake": True,
        }

    if _returning_question_asked(messages):
        if NEW_LEAD_RE.search(user_message):
            if sid:
                _session_returning[sid] = "new"
            return None
        if SAME_PROJECT_RE.search(user_message):
            if sid:
                _session_returning[sid] = "same"
            return {
                "mode": "returning_same",
                "n": CLOSE_STEP,
                "message": CLOSE_LINE_LEGACY,
                "in_intake": True,
            }
        if sid in _session_returning_welcomed:
            return {
                "mode": "returning_followup",
                "n": 0,
                "message": _returning_followup_message(),
                "in_intake": True,
            }
        company = _guess_company(all_msgs, phone)
        msg = _returning_question_for_company(company, prior)
        return {
            "mode": "returning_ask",
            "n": 0,
            "message": msg,
            "in_intake": True,
        }

    if sid and sid in _session_returning_welcomed:
        if GREETING_ONLY_RE.match((user_message or "").strip()) or SHORT_ACK_RE.match(
            (user_message or "").strip()
        ):
            return {
                "mode": "returning_followup",
                "n": 0,
                "message": _returning_followup_message(),
                "in_intake": True,
            }
        if not _returning_question_asked(messages):
            return {
                "mode": "returning_ask",
                "n": 0,
                "message": _returning_clarify_message(prior),
                "in_intake": True,
            }

    if sid:
        _session_returning[sid] = "asked"
        _session_returning_welcomed.add(sid)
    if phone:
        _known_contact(sid, phone, all_msgs)
    return {
        "mode": "returning_welcome",
        "n": 0,
        "message": _returning_welcome_message(prior),
        "in_intake": True,
    }


def compute_intake_step(
    conversation_history: list | None,
    user_message: str,
    *,
    phone: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    sid = session_id or ""
    session_messages = _accumulate_session_transcript(
        session_id, conversation_history, user_message
    )
    if (
        user_message
        and GREETING_ONLY_RE.match(user_message.strip())
        and len(session_messages) <= 1
        and sid not in _session_intake_closed
    ):
        _clear_field_stores(phone, session_id)
        _session_fresh_intake.add(sid)
    _backfill_session_transcript(session_id, phone)
    session_messages = _session_transcript.get(sid) or session_messages
    if user_message and (session_id or phone):
        sid_capture = session_id or ""
        if NEW_LEAD_RE.search(user_message) and sid_capture:
            _clear_field_stores(phone, session_id)
        _capture_reply_for_last_question(
            session_id, phone, session_messages, user_message
        )
    messages = _merged_messages(conversation_history, phone)
    # Session transcript only — never use stale GBrain for step logic.
    intake_thread = session_messages if session_messages else messages
    if sid and sid in _session_intake_closed:
        if SHORT_ACK_RE.match((user_message or "").strip()):
            return {
                "n": CLOSE_STEP,
                "message": _close_line_for_session(session_id),
                "in_intake": True,
                "mode": "complete",
            }
        if _user_re_engaged(user_message):
            returning = compute_returning_step(
                session_id, session_messages, user_message, phone
            )
            if returning:
                _session_intake_closed.discard(sid)
                return returning
            _session_intake_closed.discard(sid)
        else:
            return {"n": 0, "message": "", "in_intake": False, "mode": "idle"}

    if user_message:
        last = messages[-1] if messages else None
        if last != ("user", user_message):
            messages.append(("user", user_message))

    boundary = compute_boundary_step(user_message)
    if boundary:
        return boundary

    if not _session_has_active_intake(session_id, session_messages, phone):
        returning = compute_returning_step(session_id, session_messages, user_message, phone)
        if returning:
            _track_intake_step_state(session_id, phone, returning)
            return returning

    sid = session_id or ""
    if (
        _session_returning.get(sid) == "new"
        and user_message
        and NEW_LEAD_RE.search(user_message)
        and not _step1_asked_fresh(session_id, messages)
        and not _returning_intake_started(messages)
    ):
        prior = _known_contact(sid, phone, messages)
        if _has_returning_known_profile(prior):
            return {
                "n": 1,
                "message": _returning_resume_message(prior),
                "in_intake": True,
                "mode": "returning_intake",
            }
        return {"n": 1, "message": STEPS[1], "in_intake": True, "mode": "intake"}

    fresh = _continue_fresh_intake_step(
        session_id, session_messages, messages, user_message, phone
    )
    if fresh:
        return fresh

    if user_message and _should_skip_greeting(user_message):
        if NEW_LEAD_RE.search(user_message) and session_id:
            _session_returning[session_id] = "new"
        if STRONG_INTAKE_RE.search(user_message) or DIRECT_INTAKE_RE.search(user_message):
            window = _intake_window(intake_thread)
            if _closed_in_window(window):
                alt = _safe_returning_step(session_id, session_messages, user_message, phone)
                if alt:
                    return alt
        return {"n": 1, "message": STEPS[1], "in_intake": True, "mode": "intake"}

    greeting = compute_greeting_step(session_messages, user_message, session_id)
    if greeting:
        return greeting

    if not messages:
        return {"n": 0, "message": "", "in_intake": False, "mode": "idle"}

    if (
        _greeting_complete(intake_thread, session_id)
        and _user_re_engaged(user_message)
        and not _name_question_asked(intake_thread)
    ):
        return {"n": 1, "message": STEPS[1], "in_intake": True, "mode": "intake"}

    window = _active_intake_window(session_id, intake_thread)
    if not _in_active_intake(window, intake_thread):
        if _user_re_engaged(user_message):
            alt_fresh = _continue_fresh_intake_step(
                session_id, session_messages, messages, user_message, phone
            )
            if alt_fresh:
                return alt_fresh
            alt_returning = _safe_returning_step(session_id, session_messages, user_message, phone)
            if alt_returning:
                return alt_returning
            if (
                _classify_user_message(user_message) == "general"
                and not _in_fresh_intake(session_id, messages)
            ):
                g = compute_greeting_step(session_messages, user_message, session_id)
                if g:
                    return g
        return {"n": 0, "message": "", "in_intake": False, "mode": "idle"}

    answers = _user_answers(window, phone, session_id)
    scope = _conversation_scope(session_id, session_messages)
    _track_lead_type_and_budget(session_id, user_message, scope, answers, phone=phone)
    ret_contact = _returning_new_contact_step(
        session_id,
        messages,
        user_message,
        phone,
        window,
        answers,
        session_messages=session_messages,
    )
    if ret_contact:
        return ret_contact

    if not _can_complete_intake(window, answers, phone, session_id):
        n = _next_intake_step_number(window, answers, phone, session_id)
        step_out = {"n": n, "message": STEPS[n], "in_intake": True, "mode": "intake"}
        _track_intake_step_state(session_id, phone, step_out)
        return step_out

    resolved = _resolve_contact_phone(
        answers[-1] if answers else (user_message or ""),
        phone,
    )
    contact_name = answers[0] if answers else None
    result = _complete_with_contact(
        session_id,
        phone,
        messages,
        window,
        answers,
        preferred_phone=resolved,
        name=contact_name,
    )
    _track_intake_step_state(session_id, phone, result)
    return result


def _track_lead_type_and_budget(
    session_id: str | None,
    user_message: str,
    scope: list[tuple[str, str]],
    answers: list[str],
    *,
    phone: str | None = None,
) -> None:
    sid = session_id or ""
    if not sid:
        return
    if sid not in _session_lead_type:
        _session_lead_type[sid] = _detect_lead_type(user_message, scope)
    if len(answers) >= 5:
        _flag_below_budget_web(
            sid,
            answers[4],
            _session_lead_type.get(sid, "general"),
            answers=answers,
            phone=phone,
        )


def strip_customer_leaks(text: str) -> str:
    if not text:
        return ""
    kept: list[str] = []
    for line in text.splitlines():
        if TOOL_LEAK_RE.search(line):
            continue
        if BANNED_REPLY_RE.search(line):
            continue
        if PREMATURE_CLOSE_RE.search(line) and CLOSE_LINE not in line.lower():
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def sanitize_whatsapp_text(text: str) -> str:
    if not text:
        return text
    t = strip_customer_leaks(text)
    t = EMOJI_RE.sub("", t)
    t = t.replace("\u2014", "-").replace("\u2013", "-").replace("—", "-").replace("–", "-")
    t = OPTION_MENU_RE.sub("", t)
    t = BULLET_LINE_RE.sub("", t)
    t = re.sub(r"\([^)]*\)", "", t)
    t = MARKDOWN_RE.sub("", t)
    t = re.sub(r"-{2,}", "", t)
    t = re.sub(r"!+", "", t)
    t = t.replace("<<XENKO_WA_SPLIT>>", "\n\n")
    t = re.sub(r"[^\S\n]{2,}", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    t = t.strip()
    if BANNED_REPLY_RE.search(t) or PREMATURE_CLOSE_RE.search(t):
        return ""
    return t


def guard_response(text: str, step: dict[str, Any]) -> str:
    mode = step.get("mode", "intake")
    if mode == "boundary":
        out = step.get("message") or BOUNDARY_REPLY
    elif mode == "greeting_burst":
        out = step.get("message") or _welcome_message_text()
    elif mode == "greeting":
        out = step.get("message") or GREETING
    elif mode == "returning_ask":
        out = step.get("message") or RETURNING_QUESTION
    elif mode == "returning_welcome":
        out = step.get("message") or _returning_welcome_message({})
    elif mode == "returning_followup":
        out = step.get("message") or _returning_followup_message()
    elif mode == "returning_intake":
        out = step.get("message") or RETURNING_STEPS_KNOWN[1]
    elif mode == "returning_same":
        out = CLOSE_LINE_LEGACY
    elif mode == "email_confirm":
        out = step.get("message") or STEPS[7]
    elif mode == "email_ask":
        out = step.get("message") or STEPS[7]
    elif mode == "complete" or int(step.get("n") or 0) >= CLOSE_STEP:
        out = step.get("message") or CLOSE_LINE
    elif not step.get("in_intake"):
        out = sanitize_whatsapp_text(text)
    else:
        n = int(step.get("n", 0))
        if n >= CLOSE_STEP:
            out = step.get("message") or CLOSE_LINE
        elif 1 <= n <= 7:
            out = step.get("message") or STEPS[n]
        else:
            out = sanitize_whatsapp_text(text)
    out = sanitize_whatsapp_text(out)
    if not out and int(step.get("n") or 0) >= CLOSE_STEP:
        out = step.get("message") or CLOSE_LINE
    return out


def pre_llm_intake_context(
    session_id: str = "",
    user_message: str = "",
    conversation_history: list | None = None,
    **kwargs,
):
    conversation_history = conversation_history or kwargs.get("conversation_history") or []
    if not _guard_applies(session_id=session_id, **kwargs):
        return None
    phone = _resolve_phone(session_id, **kwargs)
    step = compute_intake_step(
        conversation_history,
        user_message,
        phone=phone,
        session_id=session_id,
    )
    _track_intake_step_state(session_id, phone, step)
    if session_id:
        _session_step[session_id] = {
            **step,
            "_user_message": user_message,
            "_phone": phone,
            "_conversation_history": conversation_history,
        }
    if not step.get("in_intake"):
        return None

    mode = step.get("mode", "intake")
    n = step["n"]

    if mode == "boundary":
        hint = f"Security/troll message. Reply ONLY: {step['message']} - no tools, no intake."
    elif mode == "greeting_burst":
        hint = (
            "First contact only. Reply with this welcome as one message "
            "(blank line between parts, no markers or bullets):\n"
            f"{_welcome_message_text()}\n"
            "Then wait for their reply before asking their name."
        )
    elif mode == "greeting":
        hint = (
            f"Welcome sequence step {step.get('greeting_n')} of 3. "
            f"Reply ONLY: {step['message']} - one short message, no lists."
        )
    elif mode == "returning_welcome":
        hint = (
            f"Returning contact — warm welcome only. Reply with: {step['message']} "
            "One question. Do not restart full intake."
        )
    elif mode == "returning_followup":
        hint = (
            f"Returning contact follow-up. Reply with: {step['message']} "
            "One question only."
        )
    elif mode == "returning_intake":
        hint = (
            f"Returning customer, new project. Do NOT ask name, company, or industry if known. "
            f"Reply with: {step['message']} — one question only."
        )
    elif mode == "returning_ask":
        hint = (
            f"Returning contact. Ask ONLY: {step['message']} - "
            f"wait for same vs new before intake or close."
        )
    elif mode == "returning_same":
        hint = f"Same project confirmed. Visible reply ONLY: {CLOSE_LINE_LEGACY}"
    elif mode == "email_confirm":
        hint = (
            f"Business questions done. Ask ONLY: {step['message']} - "
            "wait for yes/no or an alternate phone number."
        )
    elif mode == "email_ask":
        hint = (
            "Ask ONLY for their best contact number (one question). "
            f"Reply ONLY: {step['message']}"
        )
    elif mode == "complete" or n >= CLOSE_STEP:
        close_msg = step.get("message") or _close_line_for_session(session_id)
        crm = step.get("crm_contact") or _session_contact.get(session_id or "", {})
        synced = step.get("crm_sync")
        if synced:
            hint = (
                "CRM already synced (company, person, opportunity). "
                f"Do NOT call crm_add_lead or send_message. Visible reply ONLY: {close_msg}"
            )
        elif crm.get("phone"):
            hint = (
                f"Call crm_add_lead silently with name={crm.get('name')!r}, "
                f"phone={crm.get('phone')!r}, "
                f"company={crm.get('company')!r}, notes={crm.get('notes')!r}, "
                f"lead_status='Qualified'. "
                f"Do NOT call send_message. Visible reply ONLY: {close_msg}"
            )
        else:
            hint = (
                f"Call crm_add_lead silently with lead_status='Qualified'. "
                f"Do NOT call send_message. Visible reply ONLY: {close_msg}"
            )
    else:
        hint = (
            f"Step {n} of 7. One question per message. Acknowledge their last answer naturally. "
            f"Collect: name, company, industry, outcome, budget, timeline, contact number. "
            f"NEVER use terminal, session_search, read_file, skill_view, or execute_code. "
            f"Ask about: {step['message']}"
        )
    return {"context": f"\n\n[WHATSAPP INTAKE - mandatory]\n{hint}\n"}


_NON_CRM_TOOLS_BLOCKED = frozenset({
    "terminal",
    "execute_code",
    "session_search",
    "search_files",
    "read_file",
    "write_file",
    "patch",
    "delegate_task",
    "send_message",
    "send",
    "skill_view",
    "skill_manage",
    "skills_list",
    "memory",
    "cronjob",
    "web",
})


def pre_tool_block_during_intake(tool_name: str = "", session_id: str = "", **kwargs):
    if not _guard_applies(session_id=session_id, **kwargs):
        return None

    step = _session_step.get(session_id or "", {})
    name = (tool_name or "").strip().lower()

    if name == "crm_add_lead":
        if step.get("mode") in (
            "boundary",
            "greeting",
            "greeting_burst",
            "returning_ask",
            "returning_welcome",
            "returning_followup",
            "returning_intake",
            "email_confirm",
            "email_ask",
        ):
            return {"action": "block", "message": "Not ready for CRM yet."}
        if step.get("mode") == "returning_ask":
            return {"action": "block", "message": "Wait for same vs new answer before CRM."}
        if step.get("in_intake") and int(step.get("n") or 0) < CLOSE_STEP:
            return {"action": "block", "message": "Complete intake before CRM."}
        return None

    # Allow send_message after intake is complete
        if name == "send_message" and step.get("mode") == "complete":
            return None
        if name == "send_message" and step.get("in_intake") and int(step.get("n") or 0) >= CLOSE_STEP:
            return None

        if name in _NON_CRM_TOOLS_BLOCKED:
            return {
                "action": "block",
                "message": (
                    "WhatsApp sales line: reply in plain text only. "
                    "No terminal, sessions, files, or skills."
                ),
            }

    if step.get("in_intake") and name not in ("crm_add_lead",):
        return {
            "action": "block",
            "message": "Use plain text only during intake.",
        }
    return None


def transform_whatsapp_output(
    response_text: str = "",
    session_id: str = "",
    **kwargs,
):
    if not _guard_applies(session_id=session_id, **kwargs):
        return None
    stored = _session_step.get(session_id or "", {})
    user_msg = stored.get("_user_message") or kwargs.get("user_message") or ""
    phone = stored.get("_phone") or _resolve_phone(session_id, **kwargs)
    history = stored.get("_conversation_history") or kwargs.get("conversation_history")
    hist_for_step = _history_dicts_from_transcript(session_id) or history
    transcript = _session_transcript.get(session_id or "", [])
    has_outbound = any(role == "assistant" for role, _ in transcript)
    if (
        stored.get("_user_message") == user_msg
        and stored.get("mode") == "greeting_burst"
        and not has_outbound
        and user_msg
    ):
        step = {k: v for k, v in stored.items() if not str(k).startswith("_")}
    else:
        step = compute_intake_step(
            hist_for_step,
            user_msg,
            phone=phone,
            session_id=session_id,
        )
    _track_intake_step_state(session_id, phone, step)
    if session_id:
        _session_step[session_id] = {
            **step,
            "_user_message": user_msg,
            "_phone": phone,
            "_conversation_history": history,
        }

    if step.get("in_intake") or step.get("mode") == "complete":
        if step.get("mode") == "complete" or int(step.get("n") or 0) >= CLOSE_STEP:
            crm_contact = step.get("crm_contact") or _session_contact.get(session_id or "", {})
            msgs = _normalize_messages(hist_for_step or history)
            scope = _conversation_scope(session_id, msgs)
            window = _active_intake_window(session_id, scope)
            answers = _user_answers(window, phone, session_id)
            fields = _merged_intake_fields(window, phone, session_id)
            if crm_contact and not step.get("crm_sync"):
                sync = _sync_crm_on_qualification(
                    session_id, phone, crm_contact, answers, fields=fields
                )
                if sync and session_id:
                    _session_step[session_id] = {**step, "crm_sync": sync}
            if _can_complete_intake(window, answers, phone, session_id):
                _notify_qualified_lead(
                    session_id=session_id,
                    name=crm_contact.get("name") or fields.get("name", ""),
                    company=crm_contact.get("company") or fields.get("company", ""),
                    industry=fields.get("industry", ""),
                    goal=fields.get("goal", ""),
                    budget=fields.get("budget", ""),
                    timeline=fields.get("timeline", ""),
                    phone=crm_contact.get("phone") or phone,
                    lead_type=_session_lead_type.get(session_id or "", ""),
                    below_budget=(session_id or "") in _session_below_budget,
                    destination="founder",
                )
        out = guard_response(response_text or "", step)
        out = sanitize_whatsapp_text(out) or CLOSE_LINE
        _record_assistant_in_transcript(session_id, out)
        if step.get("mode") == "greeting_burst" and session_id:
            _session_greeting_burst.add(session_id)
        return out

    if user_msg:
        recovery = compute_intake_step(
            hist_for_step, user_msg, phone=phone, session_id=session_id
        )
        if recovery.get("in_intake") or recovery.get("mode") == "complete":
            out = sanitize_whatsapp_text(guard_response(response_text or "", recovery))
            out = out or CLOSE_LINE
            _record_assistant_in_transcript(session_id, out)
            if recovery.get("mode") == "greeting_burst" and session_id:
                _session_greeting_burst.add(session_id)
            return out

    cleaned = sanitize_whatsapp_text(response_text or "")
    if BANNED_REPLY_RE.search(response_text or "") or PREMATURE_CLOSE_RE.search(
        response_text or ""
    ):
        if session_id in _session_intake_closed:
            return CLOSE_LINE
        retry = compute_intake_step(
            hist_for_step, user_msg, phone=phone, session_id=session_id
        )
        if retry.get("in_intake") or retry.get("mode") == "complete":
            return sanitize_whatsapp_text(guard_response("", retry)) or CLOSE_LINE
    if CLOSE_LINE in cleaned.lower():
        msgs = _messages_from_history(history)
        if user_msg:
            msgs.append(("user", user_msg))
        returning = _safe_returning_step(session_id, msgs, user_msg, phone)
        if returning and returning.get("mode") in ("returning_ask", "returning_same"):
            return guard_response(response_text or "", returning)

    if cleaned != (response_text or ""):
        return cleaned
    return None
