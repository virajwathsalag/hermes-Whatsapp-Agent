"""Unit tests for qualified-lead CRM pipeline (no Airtable calls)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from crm_client import (  # noqa: E402
    _build_deal_name,
    _build_next_action,
    _parse_budget_timeline,
    infer_qualification_label,
    parse_intake_answers,
)


def test_parse_budget_timeline():
    p = _parse_budget_timeline("70k and within 8 months")
    assert p["budget_amount"] == 70_000
    assert p["timeline_months"] == 8
    assert p["close_date"]


def test_infer_qualification():
    assert infer_qualification_label("70k, 8 months") == "HIGH"
    assert infer_qualification_label("5k next month") in ("MEDIUM", "LOW")


def test_parse_intake_answers():
    answers = [
        "Nilan we sell clothes",
        "more customers",
        "70k within 8 months",
        "Hasangee Gunasinghe",
        "hasangee@example.com",
    ]
    out = parse_intake_answers(answers)
    assert out["company"] == "Nilan"
    assert out["email"] == "hasangee@example.com"
    assert "70k" in out["budget_timeline"]


def test_build_deal_and_next_action():
    deal = _build_deal_name("Silvo", "launch shoe brand")
    assert "Silvo" in deal
    action = _build_next_action(
        company="Silvo",
        what_they_sell="shoes",
        goal="launch brand",
        budget_timeline="80k, 7 months",
        name="Alex",
    )
    assert "Silvo" in action
    assert "80k" in action


if __name__ == "__main__":
    test_parse_budget_timeline()
    test_infer_qualification()
    test_parse_intake_answers()
    test_build_deal_and_next_action()
    print("ok")
