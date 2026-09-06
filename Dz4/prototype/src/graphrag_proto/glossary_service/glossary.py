from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class GlossaryError(ValueError):
    """Ошибка загрузки или валидации словаря."""


class Glossary:
    """Словарь домена: синонимы -> каноническое имя + variants + unicode_map."""

    def __init__(self, raw: dict[str, Any]) -> None:
        self._raw = raw
        self._alias_index: dict[str, str] = {}
        self._alias_origins: dict[str, set[str]] = {}
        self._canonical_variants: dict[str, list[str]] = {}

        for section in ("terms", "data_types", "complexity_aliases"):
            for entry in raw.get(section) or []:
                canonical = entry.get("canonical_name")
                aliases = entry.get("aliases") or []
                if not isinstance(canonical, str):
                    raise GlossaryError(f"секция {section}: отсутствует canonical_name")
                self._register(str(canonical), str(canonical))
                for alias in aliases:
                    self._register(str(alias), str(canonical))

        for canonical, fns in (raw.get("function_synonyms") or {}).items():
            if isinstance(fns, list):
                self._register(str(canonical), str(canonical))
                for fn in fns:
                    self._register(str(fn), str(canonical))

        for unicode_char, replacement in (raw.get("unicode_map") or {}).items():
            self._alias_index[f"unicode::{unicode_char}"] = str(replacement)

    def _register(self, alias: str, canonical: str) -> None:
        self._alias_index[alias] = canonical
        self._alias_origins.setdefault(alias.lower(), set()).add(canonical)
        variants = self._canonical_variants.setdefault(canonical, [canonical])
        if alias not in variants:
            variants.append(alias)

    def resolve(self, tag: str) -> str | None:
        """Тег -> каноническое имя; точное совпадение, затем без учёта регистра."""
        exact = self._alias_index.get(tag)
        if exact is not None:
            return exact
        lowered = tag.lower()
        for alias, canonical in self._alias_index.items():
            if alias.lower() == lowered:
                return canonical
        return None

    def resolve_with_variants(self, tag: str) -> tuple[str | None, list[str]]:
        canonical = self.resolve(tag)
        if canonical is None:
            return None, []
        return canonical, self._canonical_variants.get(canonical, [canonical])

    def raw_dict(self) -> dict[str, Any]:
        """Исходный словарь (словарь домена как есть)."""
        return self._raw

    def unicode_map(self) -> dict[str, str]:
        entries = self._raw.get("unicode_map") or {}
        return {str(k): str(v) for k, v in entries.items()}

    def duplicates(self) -> list[dict[str, Any]]:
        """Теги, ведущие более чем к одному каноническому имени."""
        return [
            {"tag": tag, "canonical_names": sorted(origins)}
            for tag, origins in sorted(self._alias_origins.items())
            if len(origins) > 1
        ]


def load_glossary(path: Path) -> Glossary:
    if not path.is_file():
        raise GlossaryError(f"словарь '{path.name}' не найден")
    with path.open("r", encoding="utf-8") as fh:
        try:
            raw = yaml.safe_load(fh)
        except yaml.YAMLError as exc:
            raise GlossaryError(f"'{path.name}': невалидный YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise GlossaryError(f"'{path.name}': словарь должен быть YAML-маппингом")
    return Glossary(raw)