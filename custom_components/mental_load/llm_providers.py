"""Unified Multi-LLM Provider Client for Mental Load Analyzer."""
from __future__ import annotations

import asyncio
import datetime
import logging
import os
import shutil
from typing import Any

from .const import (
    CONF_AGY_BINARY,
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_MODEL,
    CONF_PROVIDER,
    CONF_TEMPERATURE,
    DEFAULT_AGY_BINARY,
    DEFAULT_BASE_URLS,
    DEFAULT_MODELS,
    DEFAULT_TEMPERATURE,
    PROVIDER_ANTIGRAVITY_CLI,
    PROVIDER_ANTHROPIC,
    PROVIDER_CUSTOM_OPENAI,
    PROVIDER_GOOGLE,
    PROVIDER_HA_CONVERSATION,
    PROVIDER_HEURISTIC,
    PROVIDER_MISTRAL,
    PROVIDER_OPENAI,
)
from .models import CalendarEventInput, DayLoadAnalysis, FeedbackEntry
from .prompt_builder import (
    analyze_day_heuristic,
    build_batch_user_prompt,
    build_day_user_prompt,
    build_project_breakdown_prompt,
    build_system_prompt,
    decompose_project_todos_heuristic,
    parse_llm_batch_response,
    parse_llm_day_response,
    parse_project_breakdown_response,
)

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 45
BATCH_REQUEST_TIMEOUT_SECONDS = 120


def _format_exc(err: Exception) -> str:
    """Return a non-empty exception label even for bare exceptions like TimeoutError()."""
    msg = str(err).strip()
    return f"{type(err).__name__}: {msg}" if msg else type(err).__name__


class LLMProviderError(Exception):
    """Raised when an external LLM provider call fails or returns invalid data."""


class LLMProviderClient:
    """Async adapter supporting OpenAI, Anthropic, Mistral, Google Gemini, Custom/Ollama, and HA Conversation."""

    def __init__(self, hass: Any, session: Any | None = None) -> None:
        self.hass = hass
        self._session = session

    def _get_session(self) -> Any:
        if self._session is not None:
            return self._session
        from homeassistant.helpers.aiohttp_client import async_get_clientsession

        return async_get_clientsession(self.hass)

    async def async_analyze_day(
        self,
        target_date: datetime.date,
        events: list[CalendarEventInput],
        ha_context: dict[str, Any],
        config: dict[str, Any],
        feedback_history: list[FeedbackEntry],
        include_live_context: bool = True,
    ) -> DayLoadAnalysis:
        """Analyze one day using the configured AI provider with automatic heuristic fallback."""
        provider = str(config.get(CONF_PROVIDER, PROVIDER_HEURISTIC))
        if provider == PROVIDER_HEURISTIC:
            return analyze_day_heuristic(
                target_date=target_date,
                events=events,
                ha_context=ha_context,
                config=config,
                feedback_history=feedback_history,
                provider_label=PROVIDER_HEURISTIC,
            )

        system_prompt = build_system_prompt(config, feedback_history, batch_mode=False)
        user_prompt = build_day_user_prompt(
            target_date,
            events,
            ha_context,
            config,
            include_live_context=include_live_context,
        )

        try:
            raw_response = await self._async_dispatch_provider(
                provider=provider,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                config=config,
            )
            return parse_llm_day_response(
                raw_text=raw_response,
                target_date=target_date,
                events=events,
                config=config,
                provider_used=provider,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "LLM provider '%s' failed for %s (%s); falling back to local heuristic engine",
                provider,
                target_date.isoformat(),
                _format_exc(err),
            )
            return analyze_day_heuristic(
                target_date=target_date,
                events=events,
                ha_context=ha_context,
                config=config,
                feedback_history=feedback_history,
                provider_label=f"{PROVIDER_HEURISTIC}_fallback",
            )

    async def async_analyze_days_batch(
        self,
        days_items: list[tuple[datetime.date, list[CalendarEventInput], dict[str, Any], bool]],
        config: dict[str, Any],
        feedback_history: list[FeedbackEntry],
    ) -> dict[str, DayLoadAnalysis]:
        """Analyze up to 7 uncached days in a single LLM request, falling back cleanly per missing day."""
        if not days_items:
            return {}
        if len(days_items) == 1:
            day_date, day_events, day_ctx, include_live = days_items[0]
            res = await self.async_analyze_day(
                target_date=day_date,
                events=day_events,
                ha_context=day_ctx,
                config=config,
                feedback_history=feedback_history,
                include_live_context=include_live,
            )
            return {day_date.isoformat(): res}

        provider = str(config.get(CONF_PROVIDER, PROVIDER_HEURISTIC))
        if provider == PROVIDER_HEURISTIC:
            return {
                day_date.isoformat(): analyze_day_heuristic(
                    target_date=day_date,
                    events=day_events,
                    ha_context=day_ctx,
                    config=config,
                    feedback_history=feedback_history,
                    provider_label=PROVIDER_HEURISTIC,
                )
                for day_date, day_events, day_ctx, _ in days_items
            }

        days_map = {day_date.isoformat(): (day_date, day_events) for day_date, day_events, _, _ in days_items}
        results: dict[str, DayLoadAnalysis] = {}
        try:
            system_prompt = build_system_prompt(config, feedback_history, batch_mode=True)
            user_prompt = build_batch_user_prompt(days_items, config)
            max_tok = min(4096, max(1500, 650 * len(days_items)))
            raw_response = await self._async_dispatch_provider(
                provider=provider,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                config=config,
                max_tokens=max_tok,
                timeout_seconds=BATCH_REQUEST_TIMEOUT_SECONDS,
            )
            results = parse_llm_batch_response(
                raw_text=raw_response,
                days_map=days_map,
                config=config,
                provider_used=provider,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "LLM batch request (%s, %d days) failed (%s); falling back per day",
                provider,
                len(days_items),
                _format_exc(err),
            )

        for day_date, day_events, day_ctx, include_live in days_items:
            day_str = day_date.isoformat()
            if day_str not in results:
                results[day_str] = await self.async_analyze_day(
                    target_date=day_date,
                    events=day_events,
                    ha_context=day_ctx,
                    config=config,
                    feedback_history=feedback_history,
                    include_live_context=include_live,
                )
        return results

    async def async_breakdown_projects(
        self,
        project_items: list[dict[str, Any]],
        config: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Decompose open project To-Dos into small schedulable sub-tasks with fallback."""
        if not project_items:
            return []
        provider = str(config.get(CONF_PROVIDER, PROVIDER_HEURISTIC))
        if provider == PROVIDER_HEURISTIC:
            return decompose_project_todos_heuristic(project_items, config)

        system_prompt, user_prompt = build_project_breakdown_prompt(project_items, config)
        try:
            raw_response = await self._async_dispatch_provider(
                provider=provider,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                config=config,
            )
            parsed = parse_project_breakdown_response(raw_response, config, project_items=project_items)
            return parsed or decompose_project_todos_heuristic(project_items, config)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "LLM provider '%s' failed project breakdown (%s); using heuristic breakdown",
                provider,
                _format_exc(err),
            )
            return decompose_project_todos_heuristic(project_items, config)

    async def _async_dispatch_provider(
        self,
        provider: str,
        system_prompt: str,
        user_prompt: str,
        config: dict[str, Any],
        max_tokens: int = 2048,
        timeout_seconds: int = REQUEST_TIMEOUT_SECONDS,
    ) -> str:
        """Dispatch prompt to the selected provider endpoint."""
        model = str(config.get(CONF_MODEL) or DEFAULT_MODELS.get(provider, ""))
        api_key = str(config.get(CONF_API_KEY) or "").strip()
        temperature = float(config.get(CONF_TEMPERATURE, DEFAULT_TEMPERATURE))
        base_url = str(
            config.get(CONF_BASE_URL)
            or ("" if provider == PROVIDER_ANTIGRAVITY_CLI else DEFAULT_BASE_URLS.get(provider, ""))
        ).rstrip("/")

        if provider == PROVIDER_ANTIGRAVITY_CLI:
            return await self._call_antigravity_cli(
                base_url=base_url,
                api_key=api_key,
                model=model or DEFAULT_MODELS[PROVIDER_ANTIGRAVITY_CLI],
                temperature=temperature,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                config=config,
                timeout_seconds=timeout_seconds,
            )
        if provider in (PROVIDER_OPENAI, PROVIDER_MISTRAL, PROVIDER_CUSTOM_OPENAI):
            return await self._call_openai_compatible(
                provider=provider,
                base_url=base_url,
                api_key=api_key,
                model=model,
                temperature=temperature,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                timeout_seconds=timeout_seconds,
            )
        if provider == PROVIDER_ANTHROPIC:
            return await self._call_anthropic(
                base_url=base_url or DEFAULT_BASE_URLS[PROVIDER_ANTHROPIC],
                api_key=api_key,
                model=model,
                temperature=temperature,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=max_tokens,
                timeout_seconds=timeout_seconds,
            )
        if provider == PROVIDER_GOOGLE:
            return await self._call_google_gemini(
                base_url=base_url or DEFAULT_BASE_URLS[PROVIDER_GOOGLE],
                api_key=api_key,
                model=model,
                temperature=temperature,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                timeout_seconds=timeout_seconds,
            )
        if provider == PROVIDER_HA_CONVERSATION:
            return await self._call_ha_conversation(
                agent_id=model or DEFAULT_MODELS[PROVIDER_HA_CONVERSATION],
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
        raise LLMProviderError(f"Unsupported provider: {provider}")

    async def _post_json(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_seconds: int = REQUEST_TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """Execute an async HTTP POST request and return parsed JSON."""
        session = self._get_session()
        async with asyncio.timeout(timeout_seconds):
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    raise LLMProviderError(f"HTTP {resp.status} from {url}: {body[:200]}")
                return await resp.json()

    async def _call_openai_compatible(
        self,
        provider: str,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float,
        system_prompt: str,
        user_prompt: str,
        timeout_seconds: int = REQUEST_TIMEOUT_SECONDS,
    ) -> str:
        """Call OpenAI, Mistral, or Ollama/Custom OpenAI-compatible chat completions API."""
        if provider in (PROVIDER_OPENAI, PROVIDER_MISTRAL) and not api_key:
            raise LLMProviderError(f"API key is required for {provider}")

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload: dict[str, Any] = {
            "model": model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if provider in (PROVIDER_OPENAI, PROVIDER_MISTRAL):
            payload["response_format"] = {"type": "json_object"}

        data = await self._post_json(
            f"{base_url}/chat/completions",
            headers,
            payload,
            timeout_seconds=timeout_seconds,
        )
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as err:
            raise LLMProviderError(f"Malformed {provider} response structure: {err}") from err
        return str(content)

    async def _call_anthropic(
        self,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 2048,
        timeout_seconds: int = REQUEST_TIMEOUT_SECONDS,
    ) -> str:
        """Call Anthropic Messages API with ephemeral system prompt caching."""
        if not api_key:
            raise LLMProviderError("API key is required for Anthropic")

        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
        payload = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": [
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "messages": [{"role": "user", "content": user_prompt}],
        }
        data = await self._post_json(
            f"{base_url}/messages",
            headers,
            payload,
            timeout_seconds=timeout_seconds,
        )
        try:
            blocks = data["content"]
            text_parts = [b["text"] for b in blocks if isinstance(b, dict) and b.get("type") == "text"]
            if not text_parts:
                raise KeyError("No text blocks in Anthropic response")
            return "\n".join(text_parts)
        except (KeyError, TypeError) as err:
            raise LLMProviderError(f"Malformed Anthropic response: {err}") from err

    async def _call_google_gemini(
        self,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float,
        system_prompt: str,
        user_prompt: str,
        timeout_seconds: int = REQUEST_TIMEOUT_SECONDS,
    ) -> str:
        """Call Google Gemini generateContent REST API."""
        if not api_key:
            raise LLMProviderError("API key is required for Google Gemini")

        url = f"{base_url}/models/{model}:generateContent?key={api_key}"
        headers = {"Content-Type": "application/json"}
        payload = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": temperature,
                "responseMimeType": "application/json",
            },
        }
        data = await self._post_json(
            url,
            headers,
            payload,
            timeout_seconds=timeout_seconds,
        )
        try:
            parts = data["candidates"][0]["content"]["parts"]
            return "".join(str(p.get("text", "")) for p in parts)
        except (KeyError, IndexError, TypeError) as err:
            raise LLMProviderError(f"Malformed Google Gemini response: {err}") from err

    async def _call_antigravity_cli(
        self,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float,
        system_prompt: str,
        user_prompt: str,
        config: dict[str, Any],
        timeout_seconds: int = REQUEST_TIMEOUT_SECONDS,
    ) -> str:
        """Call Antigravity CLI (`agy`) either via HTTP bridge or local subprocess."""
        if base_url:
            return await self._call_openai_compatible(
                provider=PROVIDER_ANTIGRAVITY_CLI,
                base_url=base_url,
                api_key=api_key,
                model=model,
                temperature=temperature,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                timeout_seconds=timeout_seconds,
            )

        configured_bin = str(config.get(CONF_AGY_BINARY) or DEFAULT_AGY_BINARY).strip()
        agy_bin = shutil.which(configured_bin)
        if not agy_bin and os.path.isfile(configured_bin) and os.access(configured_bin, os.X_OK):
            agy_bin = configured_bin
        if not agy_bin:
            fallback_path = os.path.expanduser("~/.local/bin/agy")
            if os.path.isfile(fallback_path) and os.access(fallback_path, os.X_OK):
                agy_bin = fallback_path
        if not agy_bin:
            raise LLMProviderError(
                "Antigravity CLI binary 'agy' not found locally; set Bridge Base-URL (e.g. http://<host>:11435/v1)"
            )

        combined_prompt = f"{system_prompt}\n\n{user_prompt}"
        cmd = [
            agy_bin,
            "--disable-slash-commands",
            "--model",
            model,
            "--print",
            combined_prompt,
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=max(timeout_seconds, REQUEST_TIMEOUT_SECONDS * 2),
            )
        except TimeoutError as err:
            proc.kill()
            await proc.wait()
            raise LLMProviderError("Antigravity CLI timed out") from err

        if proc.returncode != 0:
            err_msg = stderr.decode("utf-8", errors="replace").strip()
            raise LLMProviderError(f"agy exited with code {proc.returncode}: {err_msg[:200]}")

        output = stdout.decode("utf-8", errors="replace").strip()
        if not output:
            raise LLMProviderError("Antigravity CLI returned empty output")
        return output

    async def _call_ha_conversation(
        self,
        agent_id: str,
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        """Invoke a configured Home Assistant Conversation agent."""
        from homeassistant.components import conversation

        combined_prompt = f"{system_prompt}\n\n{user_prompt}"
        result = await conversation.async_converse(
            hass=self.hass,
            text=combined_prompt,
            conversation_id=None,
            context=None,
            agent_id=agent_id,
        )
        try:
            return str(result.response.speech["plain"]["speech"])
        except (AttributeError, KeyError, TypeError) as err:
            raise LLMProviderError(f"Malformed HA Conversation response: {err}") from err
