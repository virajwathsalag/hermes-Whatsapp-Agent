"""Xenko CRM plugin — write-only lead capture for restricted WhatsApp agents."""

from __future__ import annotations

import logging

from . import schemas, tools

logger = logging.getLogger(__name__)


def register(ctx) -> None:
    ctx.register_tool(
        name="crm_add_lead",
        toolset="xenko_crm",
        schema=schemas.CRM_ADD_LEAD,
        handler=tools.crm_add_lead,
        check_fn=tools._check_crm_available,
        description="Save lead to Airtable CRM (create or update by phone).",
        emoji="📇",
    )
    logger.info("xenko-crm: registered crm_add_lead (toolset xenko_crm)")
