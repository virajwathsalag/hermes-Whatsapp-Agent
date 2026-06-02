"""WhatsApp intake guard — Xenko marketing line: greet, qualify, block abuse."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

_session_step: dict[str, dict[str, Any]] = {}
_session_returning: dict[str, str] = {}  # session_id -> "asked" | "same" | "new"
_session_greeting_burst: set[str] = set()
_session_contact: dict[str, dict[str, str]] = {}  # session_id -> prior name/email for CRM
_session_transcript: dict[str, list[tuple[str, str]]] = {}  # gateway often sends one turn only
_session_intake_closed: set[str] = set()
_session_crm_synced: set[str] = set()

RETURNING_NEW_BUSINESS_STEPS = 3  # company, goal, budget — then email confirm (not name again)

CLOSE_LINE = "our founder will be in touch"
RETURNING_QUESTION = "hey - is this about your previous project with us or a new company?"
INTAKE_ANSWERS_REQUIRED = 5  # company, goal, budget+timeline, name, email

STEPS = {
    1: "hey what's your company called and what do you sell",
    2: "what are you mainly trying to do right now",
    3: "what budget and timeline do you have in mind",
    4: "what's your name",
    5: "what's the best email to reach you on",
    6: CLOSE_LINE,
}

GREETING_LINES = {
    1: "hello, welcome to xenko marketing services",
    2: (
        "we help businesses get more customers through social media, "
        "paid ads, and sales follow-up that actually converts"
    ),
    3: "what are you here for today",
}

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

CONFIRM_PRIOR_EMAIL_RE = re.compile(
    r"^(yes|yeah|yep|yup|sure|ok|okay|correct|right|fine|please|"
    r"that(?:'s| is)?\s*(?:fine|ok|good)|use that|same one|the same|go ahead)\b",
    re.I,
)

DECLINE_PRIOR_EMAIL_RE = re.compile(
    r"^(no|nope|nah|different|another|new email|not that|don'?t)\b",
    re.I,
)

EMAIL_CONFIRM_MARKER = "again to reach you on this one"

# Legacy alias used in a few helpers
MARKETING_RE = STRONG_INTAKE_RE

GREETING_ONLY_RE = re.compile(
    r"^(?:hi|hello|hey|hiya|howdy|yo|good\s*(?:morning|afternoon|evening)|"
    r"sup|greetings)(?:\s+there)?[\s!.?,]*$",
    re.I,
)

GREETING_IN_MESSAGE_RE = re.compile(
    r"\b(hi|hello|hey|good\s*(?:morning|afternoon|evening))\b",
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


def _completed_intake_in_messages(messages: list[tuple[str, str]]) -> bool:
    """Prior full intake even if founder close line was never logged."""
    if _assistant_closed(messages):
        return True
    user_texts = [c for r, c in messages if r == "user"]
    asst = " ".join(c.lower() for r, c in messages if r == "assistant")
    has_email = any(EMAIL_RE.search(t) for t in user_texts)
    asked_company = "company called" in asst
    asked_email = "email" in asst and (
        "reach you" in asst or "your email" in asst or "best email" in asst
    )
    if has_email and asked_company and asked_email:
        return True
    if has_email and asked_company and "budget" in asst and "name" in asst:
        return True
    return False


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
    """GBrain history, CRM lead row, or this session already completed intake."""
    if _completed_intake_in_messages(messages):
        return True
    if not phone:
        return False
    merged = _merged_messages(None, phone)
    if _completed_intake_in_messages(merged):
        return True
    try:
        from gbrain_history import conversation_get

        rows = conversation_get(phone, 50)
        gbrain_msgs = [
            ("user" if r.get("role") == "user" else "assistant", str(r.get("message") or ""))
            for r in rows
        ]
        if _completed_intake_in_messages(gbrain_msgs):
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
        return gbrain
    return gbrain + session if len(gbrain) > len(session) else session + gbrain


def _is_intake_trigger(text: str) -> bool:
    return bool(
        STRONG_INTAKE_RE.search(text)
        or DIRECT_INTAKE_RE.search(text)
        or NEW_LEAD_RE.search(text)
    )


def _greeting_line_sent(messages: list[tuple[str, str]], step: int) -> bool:
    needle = GREETING_LINES[step][:24].lower()
    return any(role == "assistant" and needle in content.lower() for role, content in messages)


def _welcome_message_text() -> str:
    return "\n\n".join([GREETING_LINES[1], GREETING_LINES[2], GREETING_LINES[3]])


def _greeting_burst_sent(messages: list[tuple[str, str]], session_id: str | None) -> bool:
    if session_id and session_id in _session_greeting_burst:
        return True
    welcome = _welcome_message_text()
    needle = GREETING_LINES[3][:20].lower()
    return any(
        role == "assistant"
        and (needle in content.lower() or welcome.lower() in content.lower())
        for role, content in messages
    )


def _greeting_complete(messages: list[tuple[str, str]], session_id: str | None = None) -> bool:
    if _greeting_burst_sent(messages, session_id):
        return True
    return all(_greeting_line_sent(messages, n) for n in (1, 2, 3))


def _step1_asked(messages: list[tuple[str, str]]) -> bool:
    return any(
        role == "assistant" and "company called" in content.lower()
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
    """1, 2, or 3 if that line still needs sending; 0 if welcome sequence is done."""
    for n in (1, 2, 3):
        if not _greeting_line_sent(messages, n):
            return n
    return 0


def _should_skip_greeting(user_message: str) -> bool:
    """Direct marketing ask on first contact — no 3-part welcome."""
    if STRONG_INTAKE_RE.search(user_message):
        return True
    if NEW_LEAD_RE.search(user_message):
        return True
    if DIRECT_INTAKE_RE.search(user_message) and not GREETING_ONLY_RE.match(user_message.strip()):
        if STRONG_INTAKE_RE.search(user_message):
            return True
        if re.search(
            r"\b(help|need|want|looking)\b.*\b(marketing|ads?|customers|sales)\b",
            user_message,
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


def _intake_window(messages: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """User answers for the 5-step form — excludes their reply to 'what are you here for'."""
    for i, (role, content) in enumerate(messages):
        if role == "assistant" and "company called" in content.lower():
            return messages[i + 1 :]

    start = 0
    for i, (role, content) in enumerate(messages):
        if role == "user" and _is_intake_trigger(content):
            start = i
        if role == "user" and NEW_LEAD_RE.search(content):
            start = i
    return messages[start:]


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
        for i, (role, content) in enumerate(segment):
            if role == "assistant" and "company called" in content.lower():
                return segment[i + 1 :]
        return segment
    return _intake_window(messages)


def _step1_asked_fresh(
    session_id: str | None,
    messages: list[tuple[str, str]],
) -> bool:
    sid = session_id or ""
    if _session_returning.get(sid) == "new":
        segment = messages[_fresh_project_start_index(messages) :]
        return any(
            role == "assistant" and "company called" in content.lower()
            for role, content in segment
        )
    return _step1_asked(messages)


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
    for msg in _messages_from_history(conversation_history):
        if not transcript or transcript[-1] != msg:
            transcript.append(msg)
    if user_message:
        um = ("user", user_message)
        if not transcript or transcript[-1] != um:
            transcript.append(um)
    _session_transcript[sid] = transcript
    return transcript


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
        low = content.strip().lower()
        for n in range(5, 0, -1):
            step = STEPS[n].lower()
            if step in low or low == step:
                return n
    return 0


def _next_intake_step_number(window: list[tuple[str, str]], answers: list[str]) -> int:
    """Next question index from answers and the last intake line we sent."""
    last_asked = _last_intake_step_asked(window)
    if last_asked and len(answers) >= last_asked:
        return min(last_asked + 1, 5)
    n = min(len(answers) + 1, 5)
    if len(answers) >= 1:
        n = max(n, 2)
    return n


def _user_answers(window: list[tuple[str, str]]) -> list[str]:
    answers: list[str] = []
    for role, content in window:
        if role != "user":
            continue
        text = content.strip()
        if not text or SLASH_CMD_RE.match(text):
            continue
        if INJECTED_CTX_RE.search(text):
            continue
        if GREETING_ONLY_RE.match(text):
            continue
        if NEW_LEAD_RE.search(text) or SAME_PROJECT_RE.search(text):
            continue
        if _is_intake_trigger(text) and STRONG_INTAKE_RE.search(text):
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
        "company called",
        "what do you sell",
        "trying to do right now",
        "budget",
        "your name",
        "email to reach",
    )
    assistant_text = " ".join(c.lower() for r, c in window if r == "assistant")
    return any(m in assistant_text for m in intake_markers)


def _assistant_closed(messages: list[tuple[str, str]]) -> bool:
    return any(
        role == "assistant" and CLOSE_LINE in content.lower()
        for role, content in messages
    )


def _closed_in_window(window: list[tuple[str, str]]) -> bool:
    return _assistant_closed(window)


def _returning_question_asked(messages: list[tuple[str, str]]) -> bool:
    needle = RETURNING_QUESTION.split("-")[0].strip().lower()
    for role, content in messages:
        if role == "assistant" and needle in content.lower():
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


def _returning_question_for_company(company: str) -> str:
    if _valid_company_name(company):
        return f"hey - is this about {company} again or a new company?"
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


def _email_confirm_message(email: str) -> str:
    return f"should we use {email} again to reach you on this one?"


def _email_confirm_asked(
    messages: list[tuple[str, str]],
    session_id: str | None = None,
) -> bool:
    scope = _conversation_scope(session_id, messages)
    return any(
        role == "assistant"
        and (EMAIL_CONFIRM_MARKER in content.lower() or "previous email" in content.lower())
        for role, content in scope
    )


def _user_confirms_prior_email(user_message: str) -> bool:
    text = (user_message or "").strip()
    if not text or NEW_LEAD_RE.search(text) or EMAIL_RE.search(text):
        return False
    normalized = re.sub(r"[^\w\s'']+$", "", text).strip()
    return bool(CONFIRM_PRIOR_EMAIL_RE.match(text) or CONFIRM_PRIOR_EMAIL_RE.match(normalized))


def _user_declines_prior_email(user_message: str) -> bool:
    text = (user_message or "").strip()
    if not text or EMAIL_RE.search(text):
        return False
    return bool(DECLINE_PRIOR_EMAIL_RE.match(text))


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
    """Name and email from earlier intake on this number (GBrain, session, or Airtable)."""
    merged = _merged_messages(None, phone) if phone else messages
    email = ""
    name = ""
    history_rows: list[dict[str, Any]] = []
    if phone:
        history_rows = _conversation_from_jsonl(phone, 80)
        for row in history_rows:
            if row.get("role") != "user":
                continue
            text = str(row.get("message") or "").strip()
            if not email:
                found = EMAIL_RE.search(text)
                if found:
                    email = found.group(0)
    for role, content in merged:
        if role == "user":
            found = EMAIL_RE.search(content)
            if found:
                email = found.group(0)
    name = _extract_name_from_history(merged) or name
    if not name and history_rows:
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
                    if not email:
                        email = str(fields.get("Email") or "").strip()
                lead = mod._find_lead_by_phone(phone)
                if lead:
                    fields = lead.get("fields") or {}
                    if not name:
                        name = str(fields.get("Name") or "").strip()
                    if not email:
                        email = str(fields.get("Email") or "").strip()
        except Exception:
            pass
    contact = {"name": name.strip(), "email": email.strip()}
    return contact


def _known_contact(
    session_id: str | None,
    phone: str | None,
    messages: list[tuple[str, str]],
) -> dict[str, str]:
    """Cached name/email for this session (loaded once from GBrain / Airtable)."""
    sid = session_id or ""
    if sid and sid in _session_contact:
        return _session_contact[sid]
    contact = _prior_contact(phone, messages)
    if sid and (contact.get("email") or contact.get("name")):
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

    parsed = crm.parse_intake_answers(answers, company_hint=contact.get("company") or "")
    name = (contact.get("name") or parsed.get("name") or "").strip()
    email = (contact.get("email") or parsed.get("email") or "").strip()
    company = (contact.get("company") or parsed.get("company") or "").strip()
    if not name or name.lower() == "unknown":
        return None
    if not email:
        return None

    try:
        result = crm.add_or_update_lead(
            name=name,
            phone=phone or "",
            email=email,
            company=company,
            notes=contact.get("notes") or "",
            source="WhatsApp",
            lead_status="Qualified",
            qualification_label=crm.infer_qualification_label(
                parsed.get("budget_timeline") or "",
                parsed.get("goal") or "",
            ),
            what_they_sell=parsed.get("what_they_sell") or "",
            goal=parsed.get("goal") or "",
            budget_timeline=parsed.get("budget_timeline") or "",
        )
        if sid:
            _session_crm_synced.add(sid)
        return result
    except Exception:
        return None


def _intake_notes_from_answers(answers: list[str], *, company: str = "") -> str:
    parts: list[str] = []
    if company:
        parts.append(f"Company: {company}")
    labels = ("What they sell / company", "Goal", "Budget and timeline", "Name", "Email")
    for i, ans in enumerate(answers[:5]):
        label = labels[i] if i < len(labels) else f"Answer {i + 1}"
        if EMAIL_RE.search(ans):
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
    email: str,
    name: str | None = None,
) -> dict[str, Any]:
    prior = _prior_contact(phone, messages)
    final_name = (name or prior.get("name") or "").strip() or "Unknown"
    final_email = email.strip()
    company = _company_from_current_window(window) or _guess_company(messages, phone)
    notes = _intake_notes_from_answers(answers, company=company)
    contact = {"name": final_name, "email": final_email, "company": company, "notes": notes}
    crm_result = _sync_crm_on_qualification(session_id, phone, contact, answers)
    if session_id:
        _session_contact[session_id] = contact
        _session_intake_closed.add(session_id)
        _session_step[session_id] = {
            **(_session_step.get(session_id) or {}),
            "_prior_contact": contact,
            "crm_contact": contact,
            "crm_sync": crm_result,
        }
    return {
        "n": 6,
        "message": CLOSE_LINE,
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
  Returning client, new company: steps 1-3 only, then confirm prior email.
  Yes -> close with prior email. No -> ask email once. New email in chat -> close.
    """
    scope = _conversation_scope(session_id, session_messages or messages)
    if not _is_returning_new_project(session_id, phone, scope):
        return None
    prior = _known_contact(session_id, phone, messages)
    if not prior.get("email"):
        return None

    if len(answers) < RETURNING_NEW_BUSINESS_STEPS:
        return None

    new_email = EMAIL_RE.search(user_message or "")
    if new_email and _email_confirm_asked(messages, session_id):
        return _complete_with_contact(
            session_id,
            phone,
            messages,
            window,
            answers,
            email=new_email.group(0),
        )

    if _email_confirm_asked(messages, session_id):
        if _user_confirms_prior_email(user_message):
            return _complete_with_contact(
                session_id,
                phone,
                messages,
                window,
                answers,
                email=prior["email"],
            )
        if _user_declines_prior_email(user_message):
            return {
                "n": 5,
                "message": STEPS[5],
                "in_intake": True,
                "mode": "email_ask",
            }
        return {
            "mode": "email_confirm",
            "n": 4,
            "message": _email_confirm_message(prior["email"]),
            "in_intake": True,
        }

    return {
        "mode": "email_confirm",
        "n": 4,
        "message": _email_confirm_message(prior["email"]),
        "in_intake": True,
        "prior_contact": prior,
    }


def _can_complete_intake(window: list[tuple[str, str]], answers: list[str]) -> bool:
    if len(answers) < INTAKE_ANSWERS_REQUIRED:
        return False
    if not any(EMAIL_RE.search(a) for a in answers):
        return False
    intake_markers = (
        "company called",
        "what do you sell",
        "trying to do right now",
        "budget",
        "your name",
        "email to reach",
    )
    assistant_text = " ".join(c.lower() for r, c in window if r == "assistant")
    if not any(m in assistant_text for m in intake_markers):
        return False
    return True


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
    if not _user_answers(window) and _step1_asked_fresh(session_id, messages):
        window = _active_intake_window(session_id, messages)
    if _step1_asked_fresh(session_id, session_messages) or _step1_asked_fresh(
        session_id, messages
    ):
        answers = _user_answers(window)
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
        if (
            _is_returning_new_project(session_id, phone, session_messages)
            and prior.get("email")
            and len(answers) >= RETURNING_NEW_BUSINESS_STEPS
            and not _email_confirm_asked(messages, session_id)
        ):
            return {
                "mode": "email_confirm",
                "n": 4,
                "message": _email_confirm_message(prior["email"]),
                "in_intake": True,
                "prior_contact": prior,
            }
        if not _can_complete_intake(window, answers):
            n = _next_intake_step_number(window, answers)
            if (
                n == 4
                and _is_returning_new_project(session_id, phone, messages)
                and prior.get("name")
            ):
                if prior.get("email"):
                    return {
                        "mode": "email_confirm",
                        "n": 4,
                        "message": _email_confirm_message(prior["email"]),
                        "in_intake": True,
                    }
                n = 5
            return {"n": n, "message": STEPS[n], "in_intake": True, "mode": "intake"}
        return {"n": 6, "message": STEPS[6], "in_intake": True, "mode": "intake"}
    if int(stored.get("n") or 0) == 1 and stored.get("mode") == "intake":
        return {"n": 2, "message": STEPS[2], "in_intake": True, "mode": "intake"}
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
        "message": GREETING_LINES[n],
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
            "n": 6,
            "message": CLOSE_LINE,
            "in_intake": True,
        }

    if not _had_prior_intake(phone, messages):
        return None
    all_msgs = _merged_messages(session_messages, phone) if phone else messages

    prior_complete = _completed_intake_in_messages(messages) or _completed_intake_in_messages(
        all_msgs
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

    if NEW_LEAD_RE.search(user_message):
        if sid:
            _session_returning[sid] = "new"
            if phone:
                _known_contact(sid, phone, messages)
        return None

    if SAME_PROJECT_RE.search(user_message) and not STRONG_INTAKE_RE.search(user_message):
        if sid:
            _session_returning[sid] = "same"
        return {
            "mode": "returning_same",
            "n": 6,
            "message": CLOSE_LINE,
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
                "n": 6,
                "message": CLOSE_LINE,
                "in_intake": True,
            }
        company = _guess_company(all_msgs, phone)
        msg = _returning_question_for_company(company)
        return {
            "mode": "returning_ask",
            "n": 0,
            "message": msg,
            "in_intake": True,
        }

    if sid:
        _session_returning[sid] = "asked"
    if phone:
        _known_contact(sid, phone, all_msgs)
    company = _guess_company(all_msgs, phone)
    msg = _returning_question_for_company(company)
    return {
        "mode": "returning_ask",
        "n": 0,
        "message": msg,
        "in_intake": True,
    }


def compute_intake_step(
    conversation_history: list | None,
    user_message: str,
    *,
    phone: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    session_messages = _accumulate_session_transcript(
        session_id, conversation_history, user_message
    )
    messages = _merged_messages(conversation_history, phone)
    sid = session_id or ""
    if sid and sid in _session_intake_closed:
        if SHORT_ACK_RE.match((user_message or "").strip()):
            return {
                "n": 6,
                "message": CLOSE_LINE,
                "in_intake": True,
                "mode": "complete",
            }
        return {"n": 0, "message": "", "in_intake": False, "mode": "idle"}

    if user_message:
        last = messages[-1] if messages else None
        if last != ("user", user_message):
            messages.append(("user", user_message))

    boundary = compute_boundary_step(user_message)
    if boundary:
        return boundary

    returning = compute_returning_step(session_id, session_messages, user_message, phone)
    if returning:
        return returning

    sid = session_id or ""
    if (
        _session_returning.get(sid) == "new"
        and user_message
        and NEW_LEAD_RE.search(user_message)
        and not _step1_asked_fresh(session_id, messages)
    ):
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
            window = _intake_window(messages)
            if _closed_in_window(window):
                alt = compute_returning_step(session_id, session_messages, user_message, phone)
                if alt:
                    return alt
        return {"n": 1, "message": STEPS[1], "in_intake": True, "mode": "intake"}

    greeting = compute_greeting_step(session_messages, user_message, session_id)
    if greeting:
        return greeting

    if not messages:
        return {"n": 0, "message": "", "in_intake": False, "mode": "idle"}

    if (
        _greeting_complete(messages, session_id)
        and _user_re_engaged(user_message)
        and not _step1_asked(messages)
    ):
        return {"n": 1, "message": STEPS[1], "in_intake": True, "mode": "intake"}

    window = _active_intake_window(session_id, messages)
    if not _in_active_intake(window, messages):
        if _user_re_engaged(user_message):
            alt_fresh = _continue_fresh_intake_step(
                session_id, session_messages, messages, user_message, phone
            )
            if alt_fresh:
                return alt_fresh
            alt_returning = compute_returning_step(session_id, session_messages, user_message, phone)
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

    answers = _user_answers(window)
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

    if not _can_complete_intake(window, answers):
        n = _next_intake_step_number(window, answers)
        return {"n": n, "message": STEPS[n], "in_intake": True, "mode": "intake"}

    return {"n": 6, "message": STEPS[6], "in_intake": True, "mode": "intake"}


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
        out = step.get("message") or GREETING_LINES[1]
    elif mode == "returning_ask":
        out = step.get("message") or RETURNING_QUESTION
    elif mode == "returning_same":
        out = CLOSE_LINE
    elif mode == "email_confirm":
        out = step.get("message") or _email_confirm_message("")
    elif mode == "email_ask":
        out = step.get("message") or STEPS[5]
    elif mode == "complete" or int(step.get("n") or 0) >= 6:
        out = CLOSE_LINE
    elif not step.get("in_intake"):
        out = sanitize_whatsapp_text(text)
    else:
        n = int(step.get("n", 0))
        if n >= 6:
            out = CLOSE_LINE
        elif 1 <= n <= 5:
            out = STEPS[n]
        else:
            out = sanitize_whatsapp_text(text)
    out = sanitize_whatsapp_text(out)
    if not out and int(step.get("n") or 0) >= 6:
        out = CLOSE_LINE
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
            "Then wait for their reply before the company question."
        )
    elif mode == "greeting":
        hint = (
            f"Welcome sequence step {step.get('greeting_n')} of 3. "
            f"Reply ONLY: {step['message']} - one short message, no lists."
        )
    elif mode == "returning_ask":
        hint = (
            f"Returning contact. Ask ONLY: {step['message']} - "
            f"wait for same vs new before intake or close."
        )
    elif mode == "returning_same":
        hint = f"Same project confirmed. Visible reply ONLY: {CLOSE_LINE}"
    elif mode == "email_confirm":
        hint = (
            f"Returning client, new company. Business questions done. "
            f"Ask ONLY: {step['message']} - wait for yes/no or a new email address."
        )
    elif mode == "email_ask":
        hint = (
            "They declined the previous email. Ask ONLY for email (one question). "
            f"Reply ONLY: {step['message']}"
        )
    elif mode == "complete" or n >= 6:
        crm = step.get("crm_contact") or _session_contact.get(session_id or "", {})
        synced = step.get("crm_sync")
        if synced:
            hint = (
                "CRM already synced (company, person, opportunity). "
                f"Do NOT call crm_add_lead or send_message. Visible reply ONLY: {CLOSE_LINE}"
            )
        elif crm.get("email"):
            hint = (
                f"Call crm_add_lead silently with name={crm.get('name')!r}, "
                f"phone from session, email={crm.get('email')!r}, "
                f"company={crm.get('company')!r}, notes={crm.get('notes')!r}, "
                f"lead_status='Qualified'. "
                f"Do NOT call send_message. Visible reply ONLY: {CLOSE_LINE}"
            )
        else:
            hint = (
                f"Call crm_add_lead silently with lead_status='Qualified'. "
                f"Do NOT call send_message. Visible reply ONLY: {CLOSE_LINE}"
            )
    else:
        hint = (
            f"Step {n} of 5. Do not skip steps. "
            f"NEVER use terminal, session_search, read_file, skill_view, or execute_code. "
            f"Visible reply ONLY (exact): {step['message']}"
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
            "email_confirm",
            "email_ask",
        ):
            return {"action": "block", "message": "Not ready for CRM yet."}
        if step.get("mode") == "returning_ask":
            return {"action": "block", "message": "Wait for same vs new answer before CRM."}
        if step.get("in_intake") and int(step.get("n") or 0) < 6:
            return {"action": "block", "message": "Complete intake before CRM."}
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
    step = compute_intake_step(
        hist_for_step,
        user_msg,
        phone=phone,
        session_id=session_id,
    )
    if session_id:
        _session_step[session_id] = {
            **step,
            "_user_message": user_msg,
            "_phone": phone,
            "_conversation_history": history,
        }

    if step.get("in_intake") or step.get("mode") == "complete":
        if step.get("mode") == "complete":
            crm_contact = step.get("crm_contact") or _session_contact.get(session_id or "", {})
            msgs = _normalize_messages(hist_for_step or history)
            scope = _conversation_scope(session_id, msgs)
            window = _active_intake_window(session_id, scope)
            answers = _user_answers(window)
            if crm_contact and not step.get("crm_sync"):
                sync = _sync_crm_on_qualification(
                    session_id, phone, crm_contact, answers
                )
                if sync and session_id:
                    _session_step[session_id] = {**step, "crm_sync": sync}
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
        returning = compute_returning_step(session_id, msgs, user_msg, phone)
        if returning and returning.get("mode") in ("returning_ask", "returning_same"):
            return guard_response(response_text or "", returning)

    if cleaned != (response_text or ""):
        return cleaned
    return None
