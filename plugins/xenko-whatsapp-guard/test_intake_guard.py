from unittest.mock import patch

from intake_guard import (
    CLOSE_LINE,
    CLOSE_LINE_LEGACY,
    CLOSE_STEP,
    GREETING_LINES,
    RETURNING_QUESTION,
    STEPS,
    _guard_applies,
    _email_confirm_message,
    _session_returning,
    _session_step,
    compute_intake_step,
    compute_returning_step,
    guard_response,
    pre_tool_block_during_intake,
    sanitize_whatsapp_text,
    _welcome_message_text,
)

assert _guard_applies(session_id="s1", platform="whatsapp")
assert _guard_applies(session_id="s1", platform="whatsapp", sender_id="236373558739146@lid")
assert sanitize_whatsapp_text("hey — what can i help you with?") == "hey - what can i help you with?"

# Direct marketing → step 1 (no greeting)
assert compute_intake_step([], "I need marketing help")["n"] == 1

# New contact hello → welcome with blank lines, no split marker
g = compute_intake_step([], "hello", session_id="s-new")
assert g["mode"] == "greeting_burst"
welcome = guard_response("", g)
assert "<<XENKO" not in welcome
assert "\n\n" in welcome
assert GREETING_LINES[1] in welcome and GREETING_LINES[3] in welcome

# Returning contact hello → previous project question, not welcome
prior = [
    {"role": "user", "content": "I need marketing help"},
    {"role": "assistant", "content": CLOSE_LINE_LEGACY},
]
ret = compute_intake_step(prior, "hello", session_id="s-old")
assert ret["mode"] in ("returning_ask", "returning_welcome"), ret
assert "again" in ret["message"].lower() or "welcome back" in ret["message"].lower()

with patch("intake_guard._crm_lead_exists", return_value=True):
    ret_crm = compute_intake_step([], "Hello", session_id="s-crm", phone="94760193094")
    assert ret_crm["mode"] in ("returning_ask", "returning_welcome"), ret_crm

# Hasangee-style history: email captured but no founder close line
hasangee_msgs = [
    {"role": "assistant", "content": STEPS[1]},
    {"role": "user", "content": "Hasangee"},
    {"role": "assistant", "content": STEPS[2]},
    {"role": "user", "content": "Thomsans"},
    {"role": "assistant", "content": STEPS[7]},
    {"role": "user", "content": "virajwgunas@gmail.com"},
]
ret_h = compute_intake_step(hasangee_msgs, "Hello", session_id="s-has", phone="94778298087")
assert ret_h["mode"] in ("returning_ask", "returning_welcome"), ret_h
assert "again" in ret_h["message"].lower() or "welcome back" in ret_h["message"].lower()

from intake_guard import _returning_question_for_company

assert "we again" not in _returning_question_for_company("we").lower()

_session_returning["s-no-close"] = "new"
old_intake = [
    {"role": "assistant", "content": STEPS[1]},
    {"role": "user", "content": "Hasangee"},
    {"role": "assistant", "content": STEPS[2]},
    {"role": "user", "content": "Thomsans"},
    {"role": "assistant", "content": STEPS[3]},
    {"role": "user", "content": "vehicles"},
    {"role": "assistant", "content": STEPS[4]},
    {"role": "user", "content": "more customers"},
    {"role": "assistant", "content": STEPS[5]},
    {"role": "user", "content": "200K"},
    {"role": "assistant", "content": STEPS[6]},
    {"role": "user", "content": "7 months"},
    {"role": "assistant", "content": STEPS[7]},
    {"role": "user", "content": "virajwgunas@gmail.com"},
]
session_msgs = old_intake + [
    {"role": "user", "content": "Hello"},
    {"role": "assistant", "content": RETURNING_QUESTION},
    {"role": "user", "content": "new company"},
]
with patch("intake_guard._had_prior_intake", return_value=True):
    step_new = compute_intake_step(
        session_msgs, "new company", session_id="s-no-close", phone="94778298087"
    )
assert step_new["n"] == 1, step_new
assert CLOSE_LINE not in (step_new.get("message") or "")

# After welcome + their reply → step 1
hist = [
    {"role": "user", "content": "hi"},
    {"role": "assistant", "content": _welcome_message_text()},
    {"role": "user", "content": "more customers"},
]
step = compute_intake_step(hist, "more customers", session_id="s-new")
assert step["n"] == 1, step

ret2 = compute_returning_step("sess1", prior, "hello", None)
assert ret2["mode"] in ("returning_ask", "returning_welcome")

# Returning → new company → step 1 answer must NOT replay welcome
_session_returning["s-runners"] = "new"
runners_hist = [
    {"role": "assistant", "content": RETURNING_QUESTION},
    {"role": "user", "content": "new company"},
    {"role": "assistant", "content": STEPS[1]},
    {"role": "user", "content": "we sell tires company is called as runners"},
]
with patch("intake_guard._had_prior_intake", return_value=True):
    step_run = compute_intake_step(
        runners_hist,
        "we sell tires company is called as runners",
        session_id="s-runners",
        phone="94778298087",
    )
assert step_run["n"] == 2, step_run
assert step_run["mode"] == "intake"
assert "welcome" not in (step_run.get("message") or "").lower()

_session_returning["s-stored"] = "new"
_session_step["s-stored"] = {"mode": "intake", "n": 1, "in_intake": True}
step_stored = compute_intake_step([], "we sell tires", session_id="s-stored")
assert step_stored["n"] == 2, step_stored

# Gateway sends one turn only; in-memory transcript must advance past step 2
from intake_guard import (
    _record_assistant_in_transcript,
    _session_transcript,
)

_session_transcript["s-loop"] = [
    ("user", "Hello"),
    ("assistant", "hey - is this about Thomsans again or a new company?"),
    ("user", "new company"),
    ("assistant", STEPS[1]),
    ("user", "we are Fillers we sell web applications"),
    ("assistant", STEPS[2]),
]
_session_returning["s-loop"] = "new"
with patch("intake_guard._had_prior_intake", return_value=True):
    loop_step = compute_intake_step(
        [{"role": "user", "content": "increase customers"}],
        "increase customers",
        session_id="s-loop",
        phone="94778298087",
    )
assert loop_step.get("n") in (1, 3), loop_step
assert loop_step.get("mode") in ("intake", "returning_intake")
msg_low = loop_step.get("message", "").lower()
assert "business" in msg_low or "welcome back" in msg_low or "help with" in msg_low

# Sparse session history (gateway often sends only latest turn) — merge GBrain by phone
_sparse = [{"role": "user", "content": "70000 and 7 months"}]
import intake_guard as ig

_session_returning["s-sparse"] = "new"
with patch("intake_guard._had_prior_intake", return_value=True):
    sparse_step = compute_intake_step(
        _sparse,
        "70000 and 7 months",
        session_id="s-sparse",
        phone="94778298087",
    )
assert sparse_step.get("mode") in ("email_confirm", "intake", "returning_intake"), sparse_step
if sparse_step.get("mode") == "email_confirm":
    assert "virajwgunas@gmail.com" in sparse_step.get("message", "")

# After budget answer on new-company path → email confirm, not name
_session_returning["s-email2"] = "new"
fillers_hist = [
    {"role": "user", "content": "new company"},
    {"role": "assistant", "content": STEPS[2]},
    {"role": "user", "content": "Fillers"},
    {"role": "assistant", "content": STEPS[3]},
    {"role": "user", "content": "web development"},
    {"role": "assistant", "content": STEPS[4]},
    {"role": "user", "content": "more customers"},
    {"role": "assistant", "content": STEPS[5]},
    {"role": "user", "content": "70000"},
    {"role": "assistant", "content": STEPS[6]},
    {"role": "user", "content": "7 months"},
]
with patch("intake_guard._had_prior_intake", return_value=True), patch(
    "intake_guard._prior_contact",
    return_value={"name": "Hasangee", "email": "virajwgunas@gmail.com"},
):
    after_budget = compute_intake_step(
        fillers_hist,
        "70000 and 7 months",
        session_id="s-email2",
        phone="94778298087",
    )
assert after_budget.get("mode") == "email_confirm", after_budget
assert "virajwgunas@gmail.com" in after_budget.get("message", "")
# Returning + new company: after 5 business answers, confirm prior email (not name again)
with patch("intake_guard._had_prior_intake", return_value=True), patch(
    "intake_guard._prior_contact",
    return_value={"name": "Hasangee", "email": "virajwgunas@gmail.com"},
):
    _session_returning["s-email-yes"] = "new"
    _session_returning["s-email-no"] = "new"
    biz_hist = [
        {"role": "assistant", "content": RETURNING_QUESTION},
        {"role": "user", "content": "new company"},
        {"role": "assistant", "content": STEPS[2]},
        {"role": "user", "content": "Runners"},
        {"role": "assistant", "content": STEPS[3]},
        {"role": "user", "content": "automotive"},
        {"role": "assistant", "content": STEPS[4]},
        {"role": "user", "content": "more customers"},
        {"role": "assistant", "content": STEPS[5]},
        {"role": "user", "content": "50k"},
        {"role": "assistant", "content": STEPS[6]},
        {"role": "user", "content": "3 months"},
    ]
    confirm = compute_intake_step(
        biz_hist, "50k in 3 months", session_id="s-email-yes", phone="94778298087"
    )
    assert confirm["mode"] == "email_confirm", confirm
    assert "virajwgunas@gmail.com" in confirm["message"]

    yes_hist = biz_hist + [
        {"role": "assistant", "content": confirm["message"]},
        {"role": "user", "content": "yes"},
    ]
    _session_returning["s-email-yes"] = "new"
    done = compute_intake_step(yes_hist, "yes", session_id="s-email-yes", phone="94778298087")
    assert done["n"] == CLOSE_STEP, done
    assert done.get("crm_contact", {}).get("email") == "virajwgunas@gmail.com"

    _session_returning["s-email-no"] = "new"
    no_hist = biz_hist + [
        {"role": "assistant", "content": _email_confirm_message("virajwgunas@gmail.com")},
        {"role": "user", "content": "no"},
    ]
    ask_email = compute_intake_step(
        no_hist, "no", session_id="s-email-no", phone="94778298087"
    )
    assert ask_email.get("mode") == "email_ask", ask_email
    assert STEPS[7] in ask_email.get("message", "")

    # yep after email confirm (session transcript, not GBrain)
    _session_transcript["s-yep"] = [
        ("user", "new company"),
        ("assistant", STEPS[2]),
        ("user", "Dellas"),
        ("assistant", STEPS[3]),
        ("user", "web sites"),
        ("assistant", STEPS[4]),
        ("user", "increase customers"),
        ("assistant", STEPS[5]),
        ("user", "70K"),
        ("assistant", STEPS[6]),
        ("user", "5 months"),
        ("assistant", _email_confirm_message("virajwgunas@gmail.com")),
        ("user", "yep"),
    ]
    _session_returning["s-yep"] = "new"
    yep_done = compute_intake_step(
        [{"role": "user", "content": "yep"}],
        "yep",
        session_id="s-yep",
        phone="94778298087",
    )
    assert yep_done.get("mode") == "complete", yep_done
    assert yep_done.get("message") == CLOSE_LINE

assert sanitize_whatsapp_text("❓ I didn't catch your name — what should I use?") == ""
assert sanitize_whatsapp_text("our team will be in touch — sounds good?") == ""
assert guard_response("❓ Our team will be in touch — sounds good?", {"mode": "complete", "n": CLOSE_STEP, "in_intake": True}) == CLOSE_LINE

# pre_tool_call: block terminal on any WhatsApp session (platform via sender_id)
_session_step["s-hook"] = {"in_intake": True, "mode": "greeting_burst", "n": 0}
block = pre_tool_block_during_intake(
    tool_name="terminal",
    session_id="s-hook",
    sender_id="236373558739146@lid",
)
assert block and block.get("action") == "block"
assert pre_tool_block_during_intake(tool_name="terminal", session_id="other") is None

# crm only when intake complete (n>=CLOSE_STEP)
_session_step["s-crm"] = {"in_intake": True, "mode": "intake", "n": 3}
assert pre_tool_block_during_intake(
    tool_name="crm_add_lead", session_id="s-crm", sender_id="94778298087@c.us"
).get("action") == "block"
_session_step["s-crm"] = {"in_intake": True, "mode": "intake", "n": CLOSE_STEP}
assert pre_tool_block_during_intake(
    tool_name="crm_add_lead", session_id="s-crm", sender_id="94778298087@c.us"
) is None

print("ok")
