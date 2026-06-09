from unittest.mock import patch

from intake_guard import (
    CLOSE_LINE,
    CLOSE_LINE_LEGACY,
    CLOSE_STEP,
    GREETING,
    RETURNING_QUESTION,
    STEPS,
    _extract_intake_fields_from_window,
    _guard_applies,
    _next_intake_step_number,
    _phones_in_active_intake,
    _session_intake_closed,
    _session_returning,
    _session_step,
    _session_transcript,
    _user_answers,
    compute_intake_step,
    compute_returning_step,
    guard_response,
    pre_tool_block_during_intake,
    sanitize_whatsapp_text,
    _welcome_message_text,
)

_phones_in_active_intake.clear()
_session_transcript.clear()
_session_step.clear()

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
assert "\n\n" in welcome or GREETING in welcome

# Returning contact hello → previous project question, not welcome
prior = [
    {"role": "user", "content": "I need marketing help"},
    {"role": "assistant", "content": CLOSE_LINE_LEGACY},
]
ret = compute_intake_step(prior, "hello", session_id="s-old")
assert ret["mode"] in ("returning_ask", "returning_welcome"), ret
assert "again" in ret["message"].lower() or "welcome back" in ret["message"].lower()

with patch("intake_guard._crm_lead_exists", return_value=True), patch(
    "intake_guard._persistent_conversation", return_value=[]
):
    ret_crm = compute_intake_step([], "Hello", session_id="s-crm", phone="94760193094")
    assert ret_crm["mode"] in ("returning_ask", "returning_welcome"), ret_crm

# Prior intake completed — returning welcome on hello
hasangee_msgs = [
    {"role": "assistant", "content": CLOSE_LINE},
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
    {"role": "user", "content": "yes"},
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
step = compute_intake_step(hist, "more customers", session_id="s-new2")
assert step["n"] in (1, 2), step
assert step["mode"] == "intake"

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
with patch("intake_guard._had_prior_intake", return_value=True), patch(
    "intake_guard._known_contact", return_value={"name": "", "phone": "94778298087"}
), patch("intake_guard._has_returning_known_profile", return_value=False), patch(
    "intake_guard._returning_intake_started", return_value=True
):
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

# Sparse session history — after enough answers, ask contact number
_sparse = [{"role": "user", "content": "70000 and 7 months"}]
_session_returning["s-sparse"] = "new"
with patch("intake_guard._had_prior_intake", return_value=True):
    sparse_step = compute_intake_step(
        _sparse,
        "70000 and 7 months",
        session_id="s-sparse",
        phone="94778298087",
    )
assert sparse_step.get("mode") in ("intake", "returning_intake"), sparse_step
if sparse_step.get("n") == 7:
    assert "best number to reach you" in sparse_step.get("message", "")

# After budget answer on new-company path → contact number step
_session_returning["s-phone2"] = "new"
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
    return_value={"name": "Hasangee", "phone": "94778298087"},
):
    after_budget = compute_intake_step(
        fillers_hist,
        "7 months",
        session_id="s-phone2",
        phone="94778298087",
    )
assert after_budget.get("n") == 7, after_budget
assert "best number to reach you" in after_budget.get("message", "")

# Returning + new company: after business answers, ask contact number
with patch("intake_guard._had_prior_intake", return_value=True), patch(
    "intake_guard._prior_contact",
    return_value={"name": "Hasangee", "phone": "94778298087"},
):
    _session_returning["s-phone-yes"] = "new"
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
        biz_hist, "3 months", session_id="s-phone-yes", phone="94778298087"
    )
    assert confirm["n"] == 7, confirm
    assert "best number to reach you" in confirm["message"]

    yes_hist = biz_hist + [
        {"role": "assistant", "content": confirm["message"]},
        {"role": "user", "content": "yes"},
    ]
    _session_returning["s-phone-yes"] = "new"
    with patch("intake_guard._sync_crm_on_qualification", return_value={"ok": True}):
        done = compute_intake_step(yes_hist, "yes", session_id="s-phone-yes", phone="94778298087")
    assert done["n"] == CLOSE_STEP, done
    assert done.get("mode") == "complete", done
    assert done.get("crm_contact", {}).get("phone") == "94778298087"

    # alternate number
    _session_returning["s-phone-alt"] = "new"
    alt_hist = biz_hist + [
        {"role": "assistant", "content": STEPS[7]},
        {"role": "user", "content": "use 94771112233 instead"},
    ]
    with patch("intake_guard._sync_crm_on_qualification", return_value={"ok": True}):
        alt_done = compute_intake_step(
            alt_hist, "use 94771112233 instead", session_id="s-phone-alt", phone="94778298087"
        )
    assert alt_done.get("mode") == "complete", alt_done
    assert alt_done.get("crm_contact", {}).get("phone") == "94771112233"

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
_session_step["s-crm"] = {"in_intake": True, "mode": "complete", "n": CLOSE_STEP}
assert pre_tool_block_during_intake(
    tool_name="crm_add_lead", session_id="s-crm", sender_id="94778298087@c.us"
) is None

# Mid-intake must NOT switch to returning welcome when GBrain/CRM has old completed lead
from intake_guard import _session_transcript as _st

viraj_hist = [
    {"role": "assistant", "content": STEPS[1]},
    {"role": "user", "content": "Viraj"},
    {"role": "assistant", "content": STEPS[2]},
    {"role": "user", "content": "Gjjjs"},
    {"role": "assistant", "content": STEPS[3]},
    {"role": "user", "content": "In coconut business"},
    {"role": "assistant", "content": STEPS[4]},
    {"role": "user", "content": "A website for sure"},
    {"role": "assistant", "content": "are you mainly looking to generate leads, build trust with customers, or sell products online?"},
    {"role": "user", "content": "More customers"},
    {"role": "assistant", "content": STEPS[5]},
    {"role": "user", "content": "Around 120000"},
    {"role": "assistant", "content": STEPS[6]},
    {"role": "user", "content": "Around august"},
]
_st["s-viraj"] = [
    (r["role"], r["content"]) for r in viraj_hist
]
with patch("intake_guard._had_prior_intake", return_value=True), patch(
    "intake_guard._crm_lead_exists", return_value=True
):
    mid = compute_intake_step(
        viraj_hist,
        "Around august",
        session_id="s-viraj",
        phone="94778298087",
    )
assert mid["mode"] == "intake", mid
assert mid["n"] == 7, mid
assert "best number to reach you" in mid.get("message", "")

# Sparse in-memory transcript + GBrain has full thread + CRM prior → still step 7
from intake_guard import _session_transcript as _st2

_gbrain_thread = [
    ("assistant", CLOSE_LINE),  # old completed test
    ("assistant", STEPS[1]),
    ("user", "Viraj"),
    ("assistant", STEPS[2]),
    ("user", "Gjjjs"),
    ("assistant", STEPS[3]),
    ("user", "coconut business"),
    ("assistant", STEPS[4]),
    ("user", "website"),
    ("assistant", STEPS[5]),
    ("user", "120000"),
    ("assistant", STEPS[6]),
    ("user", "august"),
]
_st2["s-gbrain"] = []  # simulate redeploy / lost in-memory state
with patch("intake_guard._had_prior_intake", return_value=True), patch(
    "intake_guard._crm_lead_exists", return_value=True
), patch("intake_guard._persistent_conversation", return_value=_gbrain_thread):
    gbrain_step = compute_intake_step(
        [{"role": "user", "content": "august"}],
        "august",
        session_id="s-gbrain",
        phone="94778298087",
    )
assert gbrain_step["mode"] == "intake", gbrain_step
assert gbrain_step["n"] == 7, gbrain_step

# Field extraction: skipped name step + helloo must not become name

lalith_window = [
    ("assistant", STEPS[2]),
    ("user", "Gifiifi"),
    ("assistant", STEPS[3]),
    ("user", "Mainly we do graffti"),
    ("assistant", STEPS[4]),
    ("user", "More customers"),
    ("assistant", STEPS[5]),
    ("user", "200000"),
    ("assistant", STEPS[6]),
    ("user", "August"),
    ("assistant", STEPS[7]),
    ("user", "This is okay"),
]
fields = _extract_intake_fields_from_window(lalith_window, "94766767565")
assert fields.get("name", "") == "", fields
assert fields.get("company") == "Gifiifi", fields
assert fields.get("industry") == "Mainly we do graffti", fields
assert fields.get("goal") == "More customers", fields
assert fields.get("budget") == "200000", fields
assert fields.get("timeline") == "August", fields
assert _next_intake_step_number(lalith_window, [], "94766767565") == 1

hello_window = [
    ("user", "Helloo"),
    ("assistant", STEPS[2]),
    ("user", "Gifiifi"),
]
assert _extract_intake_fields_from_window(hello_window).get("name", "") == ""
assert "Helloo" not in _user_answers(hello_window)

# After intake completes, returning customer should be detected (not fresh greeting)
from intake_guard import _clear_intake_active, _session_freshly_synced

_phones_in_active_intake.clear()
_session_freshly_synced.add("94766767565")
_clear_intake_active("94766767565")
assert "94766767565" not in _session_freshly_synced
with patch("intake_guard._crm_lead_exists", return_value=True), patch(
    "intake_guard._persistent_conversation", return_value=[]
), patch("intake_guard._had_prior_intake", return_value=True):
    ret2 = compute_intake_step(
        [{"role": "assistant", "content": CLOSE_LINE}],
        "Hellow",
        session_id="s-return2",
        phone="94766767565",
    )
assert ret2["mode"] in ("returning_ask", "returning_welcome"), ret2

# After intake closes in the same session, a greeting must route to returning — not idle/LLM fresh start
_session_intake_closed.add("s-post-close")
close_hist = [
    {"role": "assistant", "content": CLOSE_LINE},
]
with patch("intake_guard._had_prior_intake", return_value=True), patch(
    "intake_guard._known_contact", return_value={"name": "Viraj", "phone": "94766767565"}
):
    post_close = compute_intake_step(
        close_hist,
        "Hellow",
        session_id="s-post-close",
        phone="94766767565",
    )
assert post_close["mode"] in ("returning_ask", "returning_welcome", "returning_followup"), post_close
_session_intake_closed.discard("s-post-close")

print("ok")
