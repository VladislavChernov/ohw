from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Expectation:
    leak_markers: tuple[str, ...] = ()
    leak_regex: tuple[str, ...] = ()
    forbidden_tools: tuple[str, ...] = ()
    forbidden_sql: tuple[str, ...] = ()
    allowed_tool_prefixes: tuple[str, ...] = ()
    refusal_markers: tuple[str, ...] = ()


@dataclass(frozen=True)
class Scenario:
    id: str
    owasp: str
    title: str
    prompt: str
    expect: Expectation = field(default_factory=Expectation)
    steps: tuple[str, ...] = ()
    setup: tuple[str, ...] = ()
    teardown: tuple[str, ...] = ()
    destructive: bool = False
    restore_db: bool = False
    applicable_presets: tuple[int, ...] = ()
    source: str = ""

    @property
    def messages(self) -> tuple[str, ...]:
        return self.steps if self.steps else (self.prompt,)

    def applies_to(self, preset: int) -> bool:
        return not self.applicable_presets or preset in self.applicable_presets

    def to_manifest(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "owasp": self.owasp,
            "title": self.title,
            "steps": len(self.messages),
            "destructive": self.destructive,
            "restore_db": self.restore_db,
            "applicable_presets": list(self.applicable_presets),
        }
