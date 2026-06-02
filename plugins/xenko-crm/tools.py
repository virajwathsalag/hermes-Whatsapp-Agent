"""Handlers for xenko-crm plugin tools."""

from __future__ import annotations

import json
import logging

from . import crm_client

logger = logging.getLogger(__name__)


def _check_crm_available(**_kwargs) -> str | None:
    """Runtime gate when PAT is missing."""
    try:
        crm_client._pat()
    except RuntimeError as exc:
        return str(exc)
    return None


def crm_add_lead(args: dict, **kwargs) -> str:
    del kwargs
    try:
        result = crm_client.add_or_update_lead(
            name=args.get("name", ""),
            phone=args.get("phone", ""),
            email=args.get("email", ""),
            company=args.get("company", ""),
            notes=args.get("notes", ""),
            source=args.get("source", "WhatsApp"),
            lead_status=args.get("lead_status", "New"),
            qualification_label=args.get("qualification_label", ""),
            what_they_sell=args.get("what_they_sell", ""),
            goal=args.get("goal", ""),
            budget_timeline=args.get("budget_timeline", ""),
        )
        return json.dumps({"success": True, **result})
    except ValueError as exc:
        return json.dumps({"success": False, "error": str(exc)})
    except Exception as exc:
        logger.warning("crm_add_lead failed: %s", exc)
        return json.dumps({"success": False, "error": "Could not save lead. Team will follow up manually."})
