"""Tool schemas exposed to the model (write-only CRM)."""

CRM_ADD_LEAD = {
    "name": "crm_add_lead",
    "description": (
        "Save or update a sales lead in the company CRM after you have collected "
        "contact details. Call this once before closing the conversation. "
        "Does not read or list existing records."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Lead full name",
            },
            "phone": {
                "type": "string",
                "description": "Phone number (WhatsApp number if known)",
            },
            "email": {
                "type": "string",
                "description": "Email address if provided",
            },
            "company": {
                "type": "string",
                "description": "Company name if provided",
            },
            "notes": {
                "type": "string",
                "description": "Short summary: challenge, budget, timeline (max ~500 chars)",
            },
            "what_they_sell": {
                "type": "string",
                "description": "What the business sells (from intake step 1)",
            },
            "goal": {
                "type": "string",
                "description": "Main marketing goal (intake step 2)",
            },
            "budget_timeline": {
                "type": "string",
                "description": "Budget and timeline (intake step 3)",
            },
            "qualification_label": {
                "type": "string",
                "enum": ["HIGH", "MEDIUM", "LOW"],
                "description": "Internal score from rubric before CRM write",
            },
            "lead_status": {
                "type": "string",
                "description": "Airtable Status: New, Qualified, Disqualified, etc.",
            },
            "source": {
                "type": "string",
                "description": "Lead source (default WhatsApp)",
            },
        },
        "required": ["name", "phone"],
    },
}
