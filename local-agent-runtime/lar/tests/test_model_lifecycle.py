"""Tests for ModelLifecycle."""

import json
from pathlib import Path
from datetime import datetime, timezone

import pytest

from lar.model_lifecycle import ModelLifecycle


def test_register_and_check(tmp_path: Path):
    state = tmp_path / "lifecycle.json"
    lifecycle = ModelLifecycle(state_path=state)
    rec = lifecycle.register_or_update("qwen3.5:9b", provider="ollama", price_check=True)

    assert rec.model_id == "qwen3.5:9b"
    assert rec.provider == "ollama"
    assert rec.status == "active"
    assert rec.last_price_check_at is not None

    health = lifecycle.check_health()
    assert health["healthy"] is True
    assert "qwen3.5:9b" in health["summary"]["active"]


def test_deprecation_alert(tmp_path: Path):
    state = tmp_path / "lifecycle.json"
    lifecycle = ModelLifecycle(state_path=state, deprecation_warning_days=30)
    lifecycle.register_or_update("old-model", provider="openai")
    eol = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    eol_str = eol.replace(day=eol.day + 5).isoformat()

    rec = lifecycle.mark_deprecated("old-model", estimated_eol_at=eol_str)
    assert rec is not None
    assert rec.status == "deprecated"

    health = lifecycle.check_health()
    assert health["healthy"] is False
    assert any(a["severity"] == "critical" for a in health["alerts"])


def test_simulate_fable_deprecation(tmp_path: Path):
    state = tmp_path / "lifecycle.json"
    lifecycle = ModelLifecycle(state_path=state)
    result = lifecycle.simulate_fable_deprecation("gpt-4o", days_until_eol=7)

    assert result["simulated"] is True
    assert result["model_id"] == "gpt-4o"
    assert result["days_until_eol"] == 7
    assert result["record"]["status"] == "deprecated"

    health = lifecycle.check_health()
    assert health["healthy"] is False


def test_state_persistence(tmp_path: Path):
    state = tmp_path / "lifecycle.json"
    lifecycle = ModelLifecycle(state_path=state)
    lifecycle.register_or_update("kimi-k2.7", provider="ollama", note="smoke test")

    lifecycle2 = ModelLifecycle(state_path=state)
    assert "kimi-k2.7" in lifecycle2.models
    assert lifecycle2.models["kimi-k2.7"].notes[0].endswith("smoke test")
