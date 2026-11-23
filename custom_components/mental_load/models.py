"""Typed data models for the Mental Load Analyzer integration."""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any

from .const import (
    ALL_FACTORS,
    LEVEL_CRITICAL,
    LEVEL_EMOJIS,
    LEVEL_HIGH,
    LEVEL_LABELS_DE,
    LEVEL_LABELS_EN,
    LEVEL_LOW,
    LEVEL_MEDIUM,
)


def score_to_level(score: int) -> str:
    """Map a 0-100 mental load score to a severity level."""
    clamped = max(0, min(100, int(score)))
    if clamped < 35:
        return LEVEL_LOW
    if clamped < 65:
        return LEVEL_MEDIUM
    if clamped < 85:
        return LEVEL_HIGH
    return LEVEL_CRITICAL


def level_emoji(level: str) -> str:
    """Return the visual badge emoji for a load level."""
    return LEVEL_EMOJIS.get(level, "⚪")


def level_label(level: str, language: str = "de") -> str:
    """Return localized label for a load level."""
    labels = LEVEL_LABELS_DE if language.startswith("de") else LEVEL_LABELS_EN
    return labels.get(level, level.title())


@dataclass(slots=True)
class CalendarEventInput:
    """Normalized calendar event read from a source HA calendar entity."""

    uid: str
    summary: str
    start: datetime.datetime
    end: datetime.datetime
    all_day: bool = False
    description: str = ""
    location: str = ""
    source_calendar: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "summary": self.summary,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "all_day": self.all_day,
            "description": self.description,
            "location": self.location,
            "source_calendar": self.source_calendar,
        }


@dataclass(slots=True)
class HiddenTask:
    """An invisible head-task uncovered from a calendar event."""

    uid: str
    parent_event_summary: str
    title: str
    category: str  # "prep" | "followup" | "travel" | "packing" | "mental_note"
    start: datetime.datetime
    end: datetime.datetime
    load_contribution: int = 10
    checklist: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "parent_event_summary": self.parent_event_summary,
            "title": self.title,
            "category": self.category,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "load_contribution": self.load_contribution,
            "checklist": list(self.checklist),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HiddenTask:
        return cls(
            uid=str(data["uid"]),
            parent_event_summary=str(data.get("parent_event_summary", "")),
            title=str(data.get("title", "")),
            category=str(data.get("category", "prep")),
            start=datetime.datetime.fromisoformat(str(data["start"])),
            end=datetime.datetime.fromisoformat(str(data["end"])),
            load_contribution=int(data.get("load_contribution", 10)),
            checklist=[str(x) for x in data.get("checklist", [])],
        )


@dataclass(slots=True)
class DayLoadAnalysis:
    """Complete daily mental load analysis and uncovered invisible work."""

    date: datetime.date
    score: int
    level: str
    badge_summary: str
    reasoning: str
    factor_breakdown: dict[str, int]
    hidden_tasks: list[HiddenTask] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    top_stressor: str = ""
    provider_used: str = ""
    cached: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date.isoformat(),
            "score": self.score,
            "level": self.level,
            "badge_summary": self.badge_summary,
            "reasoning": self.reasoning,
            "factor_breakdown": dict(self.factor_breakdown),
            "hidden_tasks": [task.to_dict() for task in self.hidden_tasks],
            "recommendations": list(self.recommendations),
            "top_stressor": self.top_stressor,
            "provider_used": self.provider_used,
            "cached": self.cached,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DayLoadAnalysis:
        breakdown = {
            factor: int(data.get("factor_breakdown", {}).get(factor, 0))
            for factor in ALL_FACTORS
        }
        return cls(
            date=datetime.date.fromisoformat(str(data["date"])),
            score=int(data.get("score", 0)),
            level=str(data.get("level", LEVEL_LOW)),
            badge_summary=str(data.get("badge_summary", "")),
            reasoning=str(data.get("reasoning", "")),
            factor_breakdown=breakdown,
            hidden_tasks=[
                HiddenTask.from_dict(item)
                for item in data.get("hidden_tasks", [])
            ],
            recommendations=[str(x) for x in data.get("recommendations", [])],
            top_stressor=str(data.get("top_stressor", "")),
            provider_used=str(data.get("provider_used", "")),
            cached=bool(data.get("cached", False)),
        )


@dataclass(slots=True)
class FeedbackEntry:
    """User calibration feedback stored persistently to adapt future AI prompts."""

    timestamp: str
    date_str: str
    previous_score: int
    perceived_score: int
    adjustment: str
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "date_str": self.date_str,
            "previous_score": self.previous_score,
            "perceived_score": self.perceived_score,
            "adjustment": self.adjustment,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FeedbackEntry:
        return cls(
            timestamp=str(data.get("timestamp", "")),
            date_str=str(data.get("date_str", "")),
            previous_score=int(data.get("previous_score", 50)),
            perceived_score=int(data.get("perceived_score", 50)),
            adjustment=str(data.get("adjustment", "accurate")),
            note=str(data.get("note", "")),
        )
