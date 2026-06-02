"""Enforce Xenko WhatsApp intake style and block premature closes."""

from __future__ import annotations

import logging

from .intake_guard import (
    pre_llm_intake_context,
    pre_tool_block_during_intake,
    transform_whatsapp_output,
)

logger = logging.getLogger(__name__)


def register(ctx) -> None:
    ctx.register_hook("pre_llm_call", pre_llm_intake_context)
    ctx.register_hook("pre_tool_call", pre_tool_block_during_intake)
    ctx.register_hook("transform_llm_output", transform_whatsapp_output)
    logger.warning("xenko-whatsapp-guard: intake guard + output sanitizer enabled")
