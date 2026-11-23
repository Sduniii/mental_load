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
    PROVIDER_ANTIGRAVITY_CLI,
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
    assert session.calls[0]["json"]["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert result.score > 50


async def test_batch_analysis_single_call_and_missing_day_fallback() -> None:
    d1 = datetime.date(2026, 11, 23)
    d2 = datetime.date(2026, 11, 24)
    batch_payload = json.dumps({"days": {d1.isoformat(): json.loads(SAMPLE_LLM_JSON)}})

    class _SeqSession:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []
            self._responses = [
                _FakeResponse(200, {"choices": [{"message": {"content": batch_payload}}]}),
                _FakeResponse(200, {"choices": [{"message": {"content": SAMPLE_LLM_JSON}}]}),
            ]

        def post(self, url: str, headers: dict[str, str], json: dict[str, Any]) -> _FakeResponse:
            self.calls.append({"url": url, "headers": headers, "json": json})
            return self._responses.pop(0)

    session = _SeqSession()
    client = LLMProviderClient(hass=None, session=session)
    cfg = {CONF_PROVIDER: PROVIDER_OPENAI, CONF_API_KEY: "test-key"}

    results = await client.async_analyze_days_batch(
        [
            (d1, _sample_events(), {}, True),
            (d2, _sample_events(), {}, False),
        ],
        cfg,
        [],
    )
    assert set(results.keys()) == {d1.isoformat(), d2.isoformat()}
    # 1st call returned batch (d1 present, d2 missing); 2nd call cleanly fell back only for missing d2
    assert len(session.calls) == 2

    # When batch includes both days, strictly 1 HTTP call is made
    full_batch = json.dumps(
        {
            "days": {
                d1.isoformat(): json.loads(SAMPLE_LLM_JSON),
                d2.isoformat(): json.loads(SAMPLE_LLM_JSON),
            }
        }
    )
    single_session = _FakeSession(
        _FakeResponse(200, {"choices": [{"message": {"content": full_batch}}]})
    )
    single_client = LLMProviderClient(hass=None, session=single_session)
    full_results = await single_client.async_analyze_days_batch(
        [(d1, _sample_events(), {}, True), (d2, _sample_events(), {}, False)],
        cfg,
        [],
    )
    assert set(full_results.keys()) == {d1.isoformat(), d2.isoformat()}
    assert len(single_session.calls) == 1


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


async def test_antigravity_cli_bridge_mode() -> None:
    session = _FakeSession(
        _FakeResponse(200, {"choices": [{"message": {"content": SAMPLE_LLM_JSON}}]})
    )
    client = LLMProviderClient(hass=None, session=session)
    result = await client.async_analyze_day(
        datetime.date(2026, 11, 23),
        _sample_events(),
        {},
        {
            CONF_PROVIDER: PROVIDER_ANTIGRAVITY_CLI,
            CONF_BASE_URL: "http://192.168.1.106:11435/v1",
            CONF_MODEL: "gemini-3.8-flash-medium",
        },
        [],
    )
    assert result.provider_used == PROVIDER_ANTIGRAVITY_CLI
    assert session.calls[0]["url"] == "http://192.168.1.106:11435/v1/chat/completions"
    assert session.calls[0]["json"]["model"] == "gemini-3.8-flash-medium"
    assert len(result.hidden_tasks) == 1


async def test_antigravity_cli_subprocess_mode(monkeypatch: Any) -> None:
    import asyncio
    import shutil

    captured_cmd: list[str] = []

    class _FakeProc:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return SAMPLE_LLM_JSON.encode("utf-8"), b""

    async def _fake_exec(*args: str, **kwargs: Any) -> _FakeProc:
        captured_cmd.extend(args)
        return _FakeProc()

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/local/bin/agy")
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)

    client = LLMProviderClient(hass=None)
    result = await client.async_analyze_day(
        datetime.date(2026, 11, 23),
        _sample_events(),
        {},
        {
            CONF_PROVIDER: PROVIDER_ANTIGRAVITY_CLI,
            CONF_MODEL: "gemini-4-argon-medium",
        },
        [],
    )
    assert result.provider_used == PROVIDER_ANTIGRAVITY_CLI
    assert captured_cmd[:5] == [
        "/usr/local/bin/agy",
        "--disable-slash-commands",
        "--model",
        "gemini-4-argon-medium",
        "--print",
    ]
    assert len(result.hidden_tasks) == 1


def test_bridge_session_bundle_roundtrip(tmp_path: Any) -> None:
    from tools.agy_bridge_server import export_session_bundle, import_session_bundle

    src_home = tmp_path / "src"
    dst_home = tmp_path / "dst"
    (src_home / ".gemini" / "antigravity-cli").mkdir(parents=True)
    (src_home / ".gemini" / "google_accounts.json").write_text('{"active":"test@example.com"}')
    (src_home / ".gemini" / "antigravity-cli" / "jetski_state.pbtxt").write_text("token: abc")

    bundle = export_session_bundle(src_home)
    imported = import_session_bundle(bundle, dst_home)
    assert ".gemini/google_accounts.json" in imported
    assert ".gemini/antigravity-cli/jetski_state.pbtxt" in imported
    assert (dst_home / ".gemini" / "google_accounts.json").read_text() == '{"active":"test@example.com"}'


def test_extract_wrapped_oauth_url() -> None:
    from tools.agy_bridge_server import extract_oauth_url

    sample = (
        "Open the URL below in your browser:\n"
        "────────\n"
        " https://accounts.google.com/o/oauth2/auth?access_type=offline&prompt\n"
        " =consent&state=XYZ\n"
        "────────\n"
    )
    assert extract_oauth_url(sample) == "https://accounts.google.com/o/oauth2/auth?access_type=offline&prompt=consent&state=XYZ"

    osc8 = (
        "\x1b]8;id=123;https://accounts.google.com/o/oauth2/auth?scope=a+b&state=1\x07"
        "https://accounts.google.com/o/oauth2/auth?scope=a\x1b]8;;\x07\n"
        "\x1b]8;id=123;https://accounts.google.com/o/oauth2/auth?scope=a+b&state=1\x07"
        "+b&state=1\x1b]8;;\x07\n"
    )
    extracted = extract_oauth_url(osc8)
    assert extracted == "https://accounts.google.com/o/oauth2/auth?scope=a+b&state=1"
    assert extracted.count("scope=") == 1
