"""Tests for Multi-LLM provider adapters and automatic heuristic fallback."""
from __future__ import annotations

import datetime
import json
from typing import Any
from unittest.mock import AsyncMock

from custom_components.mental_load.const import (
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_MODEL,
    CONF_PROVIDER,
    PROVIDER_ANTHROPIC,
    PROVIDER_CUSTOM_OPENAI,
    PROVIDER_GOOGLE,
    PROVIDER_HA_CONVERSATION,
    PROVIDER_HEURISTIC,
    PROVIDER_MISTRAL,
    PROVIDER_OPENAI,
)
from custom_components.mental_load.llm_providers import LLMProviderClient
from custom_components.mental_load.models import CalendarEventInput


SAMPLE_LLM_JSON = json.dumps(
    {
        "score": 78,
        "top_stressor": "Kinderarzt + Meeting",
        "reasoning": "Dichter Zeitplan mit medizinischer Vorbereitung und Ortswechsel.",
        "factor_breakdown": {
            "pacing_context": 80,
            "social_battery": 65,
            "mobility": 70,
            "ha_context": 60,
            "hidden_work": 85,
        },
        "hidden_tasks": [
            {
                "parent_event_summary": "Kinderarzt",
                "title": "U-Heft & Karte einpacken",
                "category": "prep",
                "offset_type": "before",
                "offset_minutes": 20,
                "duration_minutes": 20,
                "load_contribution": 15,
                "checklist": ["U-Heft", "Versichertenkarte"],
            }
        ],
        "recommendations": ["15 Min Pause nach Rückkehr"],
    }
)


class _FakeResponse:
    def __init__(self, status: int, payload: dict[str, Any], text_body: str = "") -> None:
        self.status = status
        self._payload = payload
        self._text = text_body

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def json(self) -> dict[str, Any]:
        return self._payload

    async def text(self) -> str:
        return self._text


class _FakeSession:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, headers: dict[str, str], json: dict[str, Any]) -> _FakeResponse:
        self.calls.append({"url": url, "headers": headers, "json": json})
        return self.response


def _sample_events() -> list[CalendarEventInput]:
    tz = datetime.timezone(datetime.timedelta(hours=1))
    return [
        CalendarEventInput(
            uid="ev1",
            summary="Kinderarzt",
            start=datetime.datetime(2026, 11, 23, 15, 0, tzinfo=tz),
            end=datetime.datetime(2026, 11, 23, 16, 0, tzinfo=tz),
            location="Praxis Dr. Meyer",
            source_calendar="calendar.google",
        )
    ]


async def test_openai_mistral_and_custom_adapters() -> None:
    for provider, expected_url_part in (
        (PROVIDER_OPENAI, "api.openai.com/v1/chat/completions"),
        (PROVIDER_MISTRAL, "api.mistral.ai/v1/chat/completions"),
        (PROVIDER_CUSTOM_OPENAI, "http://localhost:11434/v1/chat/completions"),
    ):
        session = _FakeSession(
            _FakeResponse(200, {"choices": [{"message": {"content": SAMPLE_LLM_JSON}}]})
        )
        client = LLMProviderClient(hass=None, session=session)
        cfg = {
            CONF_PROVIDER: provider,
            CONF_API_KEY: "test-key" if provider != PROVIDER_CUSTOM_OPENAI else "",
            CONF_BASE_URL: "http://localhost:11434/v1" if provider == PROVIDER_CUSTOM_OPENAI else "",
        }
        result = await client.async_analyze_day(
            datetime.date(2026, 11, 23), _sample_events(), {}, cfg, []
        )
        assert result.provider_used == provider
        assert expected_url_part in session.calls[0]["url"]
        assert len(result.hidden_tasks) == 1
        assert "U-Heft" in result.hidden_tasks[0].title


async def test_anthropic_adapter() -> None:
    session = _FakeSession(
        _FakeResponse(200, {"content": [{"type": "text", "text": SAMPLE_LLM_JSON}]})
    )
    client = LLMProviderClient(hass=None, session=session)
    result = await client.async_analyze_day(
        datetime.date(2026, 11, 23),
        _sample_events(),
        {},
        {CONF_PROVIDER: PROVIDER_ANTHROPIC, CONF_API_KEY: "ant-key", CONF_MODEL: "claude-3-5-haiku-latest"},
        [],
    )
    assert result.provider_used == PROVIDER_ANTHROPIC
    assert session.calls[0]["headers"]["x-api-key"] == "ant-key"
    assert result.score > 50


async def test_google_gemini_adapter() -> None:
    session = _FakeSession(
        _FakeResponse(
            200,
            {"candidates": [{"content": {"parts": [{"text": SAMPLE_LLM_JSON}]}}]},
        )
    )
    client = LLMProviderClient(hass=None, session=session)
    result = await client.async_analyze_day(
        datetime.date(2026, 11, 23),
        _sample_events(),
        {},
        {CONF_PROVIDER: PROVIDER_GOOGLE, CONF_API_KEY: "gem-key", CONF_MODEL: "gemini-2.0-flash"},
        [],
    )
    assert result.provider_used == PROVIDER_GOOGLE
    assert "gemini-2.0-flash:generateContent?key=gem-key" in session.calls[0]["url"]


async def test_ha_conversation_adapter(monkeypatch: Any) -> None:
    import homeassistant.components.conversation as conv

    fake_resp = type("Resp", (), {"response": type("R", (), {"speech": {"plain": {"speech": SAMPLE_LLM_JSON}}})()})()
    monkeypatch.setattr(conv, "async_converse", AsyncMock(return_value=fake_resp))

    client = LLMProviderClient(hass=object())
    result = await client.async_analyze_day(
        datetime.date(2026, 11, 23),
        _sample_events(),
        {},
        {CONF_PROVIDER: PROVIDER_HA_CONVERSATION, CONF_MODEL: "conversation.home_assistant"},
        [],
    )
    assert result.provider_used == PROVIDER_HA_CONVERSATION
    assert len(result.hidden_tasks) == 1


async def test_api_failure_falls_back_to_heuristic() -> None:
    session = _FakeSession(_FakeResponse(500, {}, text_body="Internal Server Error"))
    client = LLMProviderClient(hass=None, session=session)
    result = await client.async_analyze_day(
        datetime.date(2026, 11, 23),
        _sample_events(),
        {"open_todos": ["Milch kaufen"]},
        {CONF_PROVIDER: PROVIDER_OPENAI, CONF_API_KEY: "bad-key"},
        [],
    )
    assert result.provider_used == f"{PROVIDER_HEURISTIC}_fallback"
    assert result.score > 0
    # Heuristic uncovers travel + prep + followup for Kinderarzt with location
    assert len(result.hidden_tasks) >= 2
