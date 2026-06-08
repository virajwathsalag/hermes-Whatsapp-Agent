---
name: client-qualifier
description: "Marketing lead intake — 6-step qualification without email"
version: 2.1.0
metadata:
  hermes:
    tags: [xenko, whatsapp, sales, intake]
---

# Client Qualifier — Marketing Lead Intake

## When to use
On WhatsApp when someone asks for marketing help, leads, website, or says they want to work with Xenko.

## The 6-Step Intake (no email)

Run these in order, ONE question per message, wait for reply before next:

1. **Name** — "hi there. thanks for reaching out. i'd be happy to help. what's your name?"
2. **Company + what they sell** — "nice to meet you [name]. what's your company called and what do you sell?"
3. **Goal** — "got it. what are you trying to achieve? more customers, brand awareness, or something else?"
4. **Budget + timeline** — "one more thing — do you have a budget and timeline in mind?"
5. **Their email** — "got it. what's the best email to reach you at?"

After step 5: `our team will be in touch`

## NEVER skip steps
- Do NOT close after just name
- Do NOT ask name then immediately close
- Ask ALL 5 questions before closing

## For returning clients (existing project)
If they have done a project before, BEFORE starting intake:
- "hey [name]! are you reaching out about your existing project or a new one?"

If new: run full intake
If existing: skip intake, connect to their project

## PITFALL: Post-email behavior
After email (step 5), ALWAYS close immediately.
- DO NOT ask about existing projects after email
- DO NOT offer more help
- The intake is complete — close with "our team will be in touch"

## Close message (exact)
`our team will be in touch`

NOT "thank you for sharing" — that's in the NEVER SAY list.

## CRM After Close
After closing, call:
```
crm_add_lead(
  name="{name}",
  phone="{whatsapp_number}",
  email="{email}",
  company="{company}",
  what_they_sell="{what_they_sell}",
  goal="{goal}",
  budget_timeline="{budget} / {timeline}",
  source="WhatsApp",
  lead_status="New"
)
```

End conversation — do not send follow-up messages.