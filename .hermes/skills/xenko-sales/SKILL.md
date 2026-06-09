---
name: xenko-sales
description: "Xenko WhatsApp sales — load with client-qualifier for web, marketing, and discovery leads."
version: 2.0.0
metadata:
  hermes:
    tags: [xenko, whatsapp, sales, web, marketing]
---

# Xenko Sales (WhatsApp)

For any business enquiry on WhatsApp (website, marketing, growth, pricing):

1. Load `skill_view(name='client-qualifier')` and follow it exactly.
2. Read vault `Hermes/client-qualification/conversation-paths.md`, `returning-customers.md`, `questions.md`, `scoring-rubric.md`, and `qualification-framework.md`.

## Order

1. Detect lead type (web / marketing / unsure / price inquiry)
2. Collect: name → company → industry → outcome → budget → finish month → contact number (one question per message)
3. Score internally (qualified vs not) — user never sees the score
4. If qualified: confirm contact number, then CRM, then warm handoff
5. If not qualified: one polite exit line from qualification-framework.md
6. Web budget below 100,000 LKR: flag `BELOW_MIN_WEB_BUDGET` in CRM notes, continue and close normally

Do not ask for contact number before business fields are answered in the **current** conversation.