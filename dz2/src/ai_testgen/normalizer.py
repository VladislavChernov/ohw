"""Deterministic requirement normalizer.

The generator contract expects a title, an optional URL and explicit ``BR-N``
requirement ids: without them ``prompt._requirement_rule`` is skipped, test
cases lose their ``requirement`` field and the coverage matrix has nothing to
reference. Real inputs come from web pages, tables and meeting notes, so this
module turns them into the canonical form **without an LLM**: the same bytes
and arguments always produce the same text.

Entry points:

- :func:`decode_bytes` — bytes to text with encoding detection;
- :func:`normalize_text` — text to a canonical requirements document;
- :func:`normalize_bytes` — both steps, reporting the detected encoding.

The shape of the canonical document is configurable: defaults live in
:class:`NormalizerSettings` and can be overridden by ``normalizer.toml``
(:func:`load_settings`) or by the widgets in the workbench.
"""

from __future__ import annotations

import codecs
import os
import re
import tomllib
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_URL_PATTERN = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_LIST_ITEM = re.compile(r"^(\s*)(?:[-*+]|\d+[.)])\s+(.*)$")
_TABLE_SEPARATOR = re.compile(r"^\s*\|?[\s:|-]*-[\s:|-]*\|[\s:|-]*$")
_REQUIREMENTS_HEADING = re.compile(r"^(?:требовани\w*|requirements?)\b", re.IGNORECASE)
_URL_ONLY = re.compile(
    r"^(?:url|сайт|target site|адрес)?\s*:?\s*https?://\S+$",
    re.IGNORECASE,
)

_BOMS: tuple[tuple[bytes, str], ...] = (
    (b"\x00\x00\xfe\xff", "utf-32"),
    (b"\xff\xfe\x00\x00", "utf-32"),
    (b"\xef\xbb\xbf", "utf-8-sig"),
    (b"\xff\xfe", "utf-16"),
    (b"\xfe\xff", "utf-16"),
)
_RUSSIAN = frozenset("абвгдеёжзийклмнопрстуфхцчшщъыьэюяАБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ")
_JUNK = frozenset("¤¦§¨©ª«¬®¯°±²³´µ¶·¸¹º»¼½¾¿„…†‡€‰‹›€™№")

DEFAULT_SETTINGS_FILE = "normalizer.toml"
SETTINGS_ENV = "AI_TESTGEN_NORMALIZER_CONFIG"
SETTING_KEYS = (
    "id_prefix",
    "requirements_heading",
    "url_label",
    "fallback_title",
    "encodings",
    "paragraph_min_length",
)


class NormalizationError(Exception):
    """Raised when bytes or text cannot become a requirements document."""


class NormalizerSettingsError(Exception):
    """Raised when ``normalizer.toml`` cannot be read or holds a bad value."""


@dataclass(frozen=True)
class NormalizerSettings:
    """Parameters of the canonical form, previously hardcoded in this module."""

    id_prefix: str = "BR"
    requirements_heading: str = "Requirements"
    url_label: str = "URL"
    fallback_title: str = "Требования"
    encodings: tuple[str, ...] = ("cp1251", "cp866")
    paragraph_min_length: int = 20

    def __post_init__(self) -> None:
        if re.fullmatch(r"[A-Za-zА-Яа-яЁё]{1,10}", self.id_prefix) is None:
            raise NormalizerSettingsError(
                f"id_prefix must be 1-10 letters, got {self.id_prefix!r}"
            )
        for field_name in ("requirements_heading", "url_label", "fallback_title"):
            value = getattr(self, field_name)
            if not value.strip() or "\n" in value:
                raise NormalizerSettingsError(
                    f"{field_name} must be a non-empty single line"
                )
        if not self.encodings:
            raise NormalizerSettingsError("encodings must list at least one codec")
        for name in self.encodings:
            try:
                codecs.lookup(name)
            except LookupError as exc:
                raise NormalizerSettingsError(f"unknown codec in encodings: {name!r}") from exc
        if self.paragraph_min_length < 1:
            raise NormalizerSettingsError("paragraph_min_length must be positive")

    @property
    def patterns(self) -> _Patterns:
        return _patterns(self.id_prefix)


@dataclass(frozen=True)
class _Patterns:
    br: re.Pattern[str]
    br_heading: re.Pattern[str]
    inline_br: re.Pattern[str]
    id_column: re.Pattern[str]


@lru_cache(maxsize=16)
def _patterns(prefix: str) -> _Patterns:
    """Patterns for the configured prefix plus the historical ``BR`` one.

    Existing documents keep their ``BR-N`` ids even after ``id_prefix`` is
    switched to ``REQ``: only newly assigned ids use the configured prefix.
    """
    names = ["BR"] if prefix.upper() == "BR" else ["BR", prefix]
    alternation = "|".join(re.escape(name) for name in names)
    return _Patterns(
        br=re.compile(rf"\b(?:{alternation})-(\d+)\b", re.IGNORECASE),
        br_heading=re.compile(
            rf"^((?:{alternation})-\d+)\s*[:.\-—]?\s*(.*)$", re.IGNORECASE
        ),
        inline_br=re.compile(
            rf"^(?:[-*+]\s+)?(?:\*\*)?((?:{alternation})-\d+)(?:\*\*)?"
            r"\s*[:.\-—)]?\s*(.*)$",
            re.IGNORECASE,
        ),
        id_column=re.compile(
            rf"^(?:id|№|номер|идентификатор|br|{re.escape(prefix.lower())}"
            r"|требование.*id)$",
            re.IGNORECASE,
        ),
    )


def default_settings_path(cwd: Path | None = None) -> Path | None:
    """``--normalizer-config`` > env var > ``normalizer.toml`` in the cwd."""
    override = os.environ.get(SETTINGS_ENV)
    if override:
        return Path(override)
    candidate = (cwd or Path.cwd()) / DEFAULT_SETTINGS_FILE
    return candidate if candidate.is_file() else None


def load_settings(path: Path | None = None) -> NormalizerSettings:
    """Read ``normalizer.toml``; an absent file means the built-in defaults."""
    resolved = path if path is not None else default_settings_path()
    if resolved is None:
        return NormalizerSettings()
    if not resolved.is_file():
        raise NormalizerSettingsError(f"файл настроек не найден: {resolved}")
    try:
        raw = tomllib.loads(resolved.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise NormalizerSettingsError(f"некорректный TOML в {resolved}: {exc}") from exc
    except OSError as exc:
        raise NormalizerSettingsError(f"не удалось прочитать {resolved}: {exc}") from exc

    unknown = set(raw) - set(SETTING_KEYS)
    if unknown:
        print(
            f"warning: {resolved}: ignoring unknown keys: {', '.join(sorted(unknown))}",
            flush=True,
        )

    def string(key: str, default: str) -> str:
        value = raw.get(key, default)
        if not isinstance(value, str):
            raise NormalizerSettingsError(f"{resolved}: '{key}' must be a string")
        return value

    encodings = raw.get("encodings", list(NormalizerSettings.encodings))
    if not isinstance(encodings, (list, tuple)) or not all(
        isinstance(item, str) for item in encodings
    ):
        raise NormalizerSettingsError(
            f"{resolved}: 'encodings' must be a list of codec names"
        )
    length = raw.get("paragraph_min_length", NormalizerSettings.paragraph_min_length)
    if isinstance(length, bool) or not isinstance(length, int):
        raise NormalizerSettingsError(
            f"{resolved}: 'paragraph_min_length' must be an integer"
        )

    try:
        return NormalizerSettings(
            id_prefix=string("id_prefix", NormalizerSettings.id_prefix),
            requirements_heading=string(
                "requirements_heading", NormalizerSettings.requirements_heading
            ),
            url_label=string("url_label", NormalizerSettings.url_label),
            fallback_title=string("fallback_title", NormalizerSettings.fallback_title),
            encodings=tuple(encodings),
            paragraph_min_length=length,
        )
    except NormalizerSettingsError as exc:
        raise NormalizerSettingsError(f"{resolved}: {exc}") from exc


@dataclass(frozen=True)
class Requirement:
    """One requirement extracted from the source document."""

    id: str
    text: str
    source: str


@dataclass(frozen=True)
class NormalizationResult:
    """Canonical document plus everything the caller needs to explain it."""

    text: str
    title: str
    url: str | None
    encoding: str
    requirements: tuple[Requirement, ...]
    warnings: tuple[str, ...]
    strategy: str
    changed: bool


@dataclass(frozen=True)
class _Heading:
    index: int
    level: int
    title: str


# --------------------------------------------------------------------------
# Decoding
# --------------------------------------------------------------------------


def decode_bytes(
    data: bytes,
    settings: NormalizerSettings | None = None,
) -> tuple[str, str]:
    """Return ``(text, encoding)``; text is UTF-8-decoded for the caller.

    Order: BOM, strict UTF-8, then the configured single-byte codecs. Those
    decode any byte sequence, so the winner is the one with more Russian
    letters and fewer junk characters (``„®Єг¬Ґ­в`` versus ``документация``).
    """
    settings = settings or NormalizerSettings()
    if not data:
        return "", "utf-8"

    for bom, encoding in _BOMS:
        if data.startswith(bom):
            try:
                name = encoding.removesuffix("-sig")
                return data.decode(encoding), name
            except UnicodeDecodeError:
                continue

    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass

    candidates: dict[str, str] = {}
    for name in settings.encodings:
        try:
            candidates[name] = data.decode(name)
        except UnicodeDecodeError:
            continue
    if not candidates:
        raise NormalizationError(
            "не удалось декодировать файл: ни одна из кодировок "
            f"({', '.join(settings.encodings)}) не подошла"
        )
    order = {name: index for index, name in enumerate(settings.encodings)}
    encoding = max(
        candidates,
        key=lambda name: (_cyrillic_score(candidates[name]), -order[name]),
    )
    return candidates[encoding], encoding


def _cyrillic_score(text: str) -> int:
    letters = sum(1 for char in text if char in _RUSSIAN)
    junk = sum(1 for char in text if char in _JUNK)
    controls = sum(
        1
        for char in text
        if unicodedata.category(char) == "Cc" and char not in "\n\r\t"
    )
    return letters - 2 * junk - 3 * controls


def find_url(text: str) -> str | None:
    """First URL in the text, without trailing punctuation."""
    match = _URL_PATTERN.search(text)
    return match.group(0).rstrip(".,;:") if match else None


# --------------------------------------------------------------------------
# Normalization
# --------------------------------------------------------------------------


def normalize_bytes(
    data: bytes,
    url: str | None = None,
    *,
    settings: NormalizerSettings | None = None,
) -> NormalizationResult:
    """Decode ``data`` and normalize it."""
    settings = settings or NormalizerSettings()
    text, encoding = decode_bytes(data, settings)
    return normalize_text(text, url, encoding=encoding, settings=settings)


def normalize_text(
    raw: str,
    url: str | None = None,
    *,
    encoding: str = "utf-8",
    settings: NormalizerSettings | None = None,
) -> NormalizationResult:
    """Turn arbitrary requirement text into the canonical BR-N document."""
    settings = settings or NormalizerSettings()
    cleaned = _clean(raw)
    if not cleaned.strip():
        raise NormalizationError("документ пуст: нет текста требований")

    lines = cleaned.split("\n")
    headings = _headings(lines)
    title, title_index = _title(lines, headings, settings)

    extracted = (
        _from_br_headings(lines, headings, settings)
        or _from_table(lines, settings)
        or _from_list(lines, headings, settings)
        or _from_inline(lines, settings)
        or _from_paragraphs(lines, title_index, settings)
    )
    if extracted is None:
        raise NormalizationError("не удалось найти ни одного требования")

    requirements, stop, strategy = extracted
    requirements = _assign_ids(requirements, settings)
    requirements, warnings = _dedupe(requirements, settings)

    resolved_url = url or find_url(cleaned)
    if resolved_url is None:
        warnings.append("URL не найден: строка **URL:** не записана")
    if strategy == "paragraph":
        warnings.append(
            "структура не распознана: требования выделены по абзацам, проверьте результат"
        )
    preamble = _preamble(lines[:stop], title_index, settings)
    rendered = _render(title, resolved_url, preamble, requirements, settings)

    return NormalizationResult(
        text=rendered,
        title=title,
        url=resolved_url,
        encoding=encoding,
        requirements=tuple(requirements),
        warnings=tuple(warnings),
        strategy=strategy,
        changed=rendered.strip() != cleaned.strip(),
    )


def _clean(text: str) -> str:
    """Normalize line endings, width and whitespace without touching wording."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\ufeff", "").replace("\u200b", "").replace("\xa0", " ")
    text = unicodedata.normalize("NFC", text)
    text = "".join(
        char
        for char in text
        if char in "\n\t" or unicodedata.category(char) != "Cc"
    )
    lines = [line.rstrip() for line in text.split("\n")]
    collapsed: list[str] = []
    blank = False
    for line in lines:
        if line.strip():
            blank = False
            collapsed.append(line)
        elif not blank:
            blank = True
            collapsed.append("")
    return "\n".join(collapsed).strip("\n")


def _headings(lines: list[str]) -> list[_Heading]:
    result: list[_Heading] = []
    in_fence = False
    for index, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _HEADING.match(line)
        if match:
            result.append(_Heading(index, len(match.group(1)), match.group(2).strip()))
    return result


def _title(
    lines: list[str],
    headings: list[_Heading],
    settings: NormalizerSettings,
) -> tuple[str, int | None]:
    for heading in headings:
        if heading.level == 1 and _leading_br(heading.title, settings) is None:
            return heading.title, heading.index
    # No H1: a short first line is a title, not a requirement.
    for index, line in enumerate(lines):
        text = line.strip()
        if not text or text.startswith(("#", "|", "-", "*", "+")):
            continue
        if _is_url_only(text) or len(text) > 80 or settings.patterns.br.search(text):
            break
        return text, index
    return settings.fallback_title, None


def _is_url_only(line: str) -> bool:
    return _URL_ONLY.match(line.replace("*", "").strip()) is not None


def _leading_br(
    text: str, settings: NormalizerSettings
) -> tuple[str, str] | None:
    match = settings.patterns.br_heading.match(text.strip())
    if match is None:
        return None
    return match.group(1).upper(), match.group(2).strip()


def _from_br_headings(
    lines: list[str],
    headings: list[_Heading],
    settings: NormalizerSettings,
) -> tuple[list[Requirement], int, str] | None:
    br_headings = [
        heading for heading in headings if _leading_br(heading.title, settings)
    ]
    if not br_headings:
        return None

    requirements: list[Requirement] = []
    for heading in br_headings:
        parsed = _leading_br(heading.title, settings)
        assert parsed is not None
        identifier, remainder = parsed
        body = _demote_headings(_br_body(lines, headings, heading, settings))
        text = _join_blocks([remainder, _block(body)])
        requirements.append(Requirement(identifier, text, "heading"))
    return requirements, br_headings[0].index, "heading"


def _br_body(
    lines: list[str],
    headings: list[_Heading],
    heading: _Heading,
    settings: NormalizerSettings,
) -> list[str]:
    """Body of a BR section: stop at the next sibling or at another BR heading.

    Deeper headings belong to the requirement, so ``### Details`` inside
    ``## BR-1`` stays part of BR-1 instead of becoming a dangling section.
    """
    end = len(lines)
    for other in headings:
        if other.index <= heading.index:
            continue
        if other.level <= heading.level or _leading_br(other.title, settings) is not None:
            end = other.index
            break
    return lines[heading.index + 1 : end]


def _demote_headings(lines: list[str]) -> list[str]:
    """Turn headings inside a requirement body into bold lines.

    A body heading must not look like a section boundary on the next run,
    otherwise re-normalizing the canonical document would cut the text.
    Bold text carries the same wording without changing the structure.
    """
    result: list[str] = []
    for line in lines:
        match = _HEADING.match(line)
        result.append(f"**{match.group(2).strip()}**" if match else line)
    return result


def _from_table(
    lines: list[str], settings: NormalizerSettings
) -> tuple[list[Requirement], int, str] | None:
    for start, header, rows in _tables(lines):
        id_column = next(
            (
                index
                for index, name in enumerate(header)
                if settings.patterns.id_column.match(name.strip())
            ),
            None,
        )
        requirements: list[Requirement] = []
        for cells in rows:
            identifier, text = _table_requirement(cells, id_column, settings)
            if identifier is None:
                continue
            requirements.append(Requirement(identifier, text, "table"))
        if requirements:
            return requirements, start, "table"
    return None


def _tables(
    lines: list[str],
) -> list[tuple[int, list[str], list[list[str]]]]:
    blocks: list[tuple[int, list[str], list[list[str]]]] = []
    index = 0
    while index + 1 < len(lines):
        if lines[index].strip().startswith("|") and _TABLE_SEPARATOR.match(lines[index + 1]):
            start = index
            header = _cells(lines[index])
            rows: list[list[str]] = []
            index += 2
            while index < len(lines) and lines[index].strip().startswith("|"):
                rows.append(_cells(lines[index]))
                index += 1
            blocks.append((start, header, rows))
            continue
        index += 1
    return blocks


def _cells(line: str) -> list[str]:
    stripped = line.strip().removeprefix("|").removesuffix("|")
    return [cell.strip() for cell in stripped.split("|")]


def _table_requirement(
    cells: list[str], id_column: int | None, settings: NormalizerSettings
) -> tuple[str | None, str]:
    """ID and description of one table row; ``None`` when the row has no BR id."""
    br = settings.patterns.br
    identifier: str | None = None
    rest: list[str] = []
    if id_column is not None and id_column < len(cells):
        match = br.fullmatch(cells[id_column].strip())
        if match is not None:
            identifier = match.group(0).upper()
        rest = [cell for index, cell in enumerate(cells) if index != id_column]
    if identifier is None and cells:
        match = br.fullmatch(cells[0].strip())
        if match is not None:
            identifier = match.group(0).upper()
            rest = cells[1:]
    if identifier is None:
        return None, ""
    text = " — ".join(_squash(cell) for cell in rest if cell.strip())
    return identifier, text


def _from_list(
    lines: list[str],
    headings: list[_Heading],
    settings: NormalizerSettings,
) -> tuple[list[Requirement], int, str] | None:
    for heading in headings:
        if not _is_requirements_heading(heading.title, settings):
            continue
        body = _section_body(lines, headings, heading)
        items = _list_items(body)
        if items:
            return (
                [_item_requirement(text, settings) for text in items],
                heading.index,
                "list",
            )
    return None


def _is_requirements_heading(title: str, settings: NormalizerSettings) -> bool:
    stripped = title.strip()
    if _REQUIREMENTS_HEADING.match(stripped) is not None:
        return True
    return stripped.casefold() == settings.requirements_heading.strip().casefold()


def _section_body(lines: list[str], headings: list[_Heading], heading: _Heading) -> list[str]:
    end = len(lines)
    for other in headings:
        if other.index > heading.index and other.level <= heading.level:
            end = other.index
            break
    return lines[heading.index + 1 : end]


def _list_items(body: list[str]) -> list[str]:
    """Top-level list items; nested lines and continuations join their parent."""
    groups: list[list[str]] = []
    indents: list[int] = []
    for line in body:
        match = _LIST_ITEM.match(line)
        if match is not None:
            indent = len(match.group(1))
            if groups and indent > indents[-1]:
                groups[-1].append(match.group(2).strip())
            else:
                groups.append([match.group(2).strip()])
                indents.append(indent)
        elif line.strip() and groups and line[:1] in (" ", "\t"):
            groups[-1].append(line.strip())
    return [" ".join(part for part in group if part).strip() for group in groups]


def _item_requirement(text: str, settings: NormalizerSettings) -> Requirement:
    parsed = _leading_br(text, settings)
    if parsed is not None:
        identifier, remainder = parsed
        return Requirement(identifier, remainder, "list")
    return Requirement("", text, "list")


def _from_inline(
    lines: list[str], settings: NormalizerSettings
) -> tuple[list[Requirement], int, str] | None:
    requirements: list[Requirement] = []
    first: int | None = None
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.lstrip()
        if stripped.startswith(("#", "|")):
            index += 1
            continue
        match = settings.patterns.inline_br.match(stripped)
        if match is None:
            index += 1
            continue
        if first is None:
            first = index
        identifier = match.group(1).upper()
        parts = [match.group(2).strip()]
        index += 1
        while index < len(lines):
            following = lines[index]
            if not following.strip():
                break
            if settings.patterns.inline_br.match(following.lstrip()) is not None:
                break
            if following.lstrip().startswith(("#", "|")):
                break
            parts.append(following.strip())
            index += 1
        requirements.append(Requirement(identifier, _block(parts), "inline"))
    if not requirements or first is None:
        return None
    return requirements, first, "inline"


def _from_paragraphs(
    lines: list[str], start: int | None, settings: NormalizerSettings
) -> tuple[list[Requirement], int, str] | None:
    begin = (start + 1) if start is not None else 0
    blocks = re.split(r"\n\s*\n", "\n".join(lines[begin:]))
    requirements: list[Requirement] = []
    for block in blocks:
        rows = [
            row.strip()
            for row in block.splitlines()
            if row.strip()
            and not row.lstrip().startswith(("#", "|"))
            and not _is_url_only(row)
        ]
        text = _block(rows)
        if len(text) < settings.paragraph_min_length:
            continue
        requirements.append(Requirement("", text, "paragraph"))
    if not requirements:
        return None
    return requirements, begin, "paragraph"


def _assign_ids(
    requirements: list[Requirement], settings: NormalizerSettings
) -> list[Requirement]:
    prefix = settings.id_prefix.upper()
    explicit = {item.id.strip().upper() for item in requirements if item.id}
    next_number = 1
    assigned: list[Requirement] = []
    for item in requirements:
        if item.id:
            assigned.append(item)
            continue
        while f"{prefix}-{next_number}" in explicit:
            next_number += 1
        identifier = f"{prefix}-{next_number}"
        explicit.add(identifier)
        assigned.append(Requirement(identifier, item.text, item.source))
    return assigned


def _dedupe(
    requirements: list[Requirement], settings: NormalizerSettings
) -> tuple[list[Requirement], list[str]]:
    unique: list[Requirement] = []
    seen: set[str] = set()
    duplicates: list[str] = []
    empty: list[str] = []
    for item in requirements:
        text = _clean(item.text).strip()
        if not text:
            empty.append(item.id)
            continue
        key = item.id.strip().upper()
        if key in seen:
            duplicates.append(item.id)
            continue
        seen.add(key)
        unique.append(Requirement(item.id, text, item.source))

    warnings: list[str] = []
    if duplicates:
        warnings.append(
            "дубликаты требований отброшены: " + ", ".join(_unique(duplicates))
        )
    if empty:
        warnings.append(
            "требования с пустым текстом отброшены: " + ", ".join(_unique(empty))
        )
    return unique, warnings


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _preamble(
    lines: list[str], title_index: int | None, settings: NormalizerSettings
) -> str:
    kept: list[str] = []
    for index, line in enumerate(lines):
        if index == title_index:
            continue
        if _is_url_only(line):
            continue
        heading = _HEADING.match(line)
        if heading is not None and _is_requirements_heading(heading.group(2), settings):
            continue
        kept.append(line)
    return _clean("\n".join(kept)).strip()


def _render(
    title: str,
    url: str | None,
    preamble: str,
    requirements: list[Requirement],
    settings: NormalizerSettings,
) -> str:
    blocks = [f"# {title}"]
    if url:
        blocks.append(f"**{settings.url_label}:** {url}")
    if preamble:
        blocks.append(preamble)
    blocks.append(f"## {settings.requirements_heading}")
    for item in requirements:
        blocks.append(f"### {item.id}\n{item.text}".rstrip())
    return "\n\n".join(blocks) + "\n"


def _block(parts: list[str]) -> str:
    """One block: lines joined by a newline (soft breaks are preserved)."""
    return _clean("\n".join(parts)).strip()


def _join_blocks(parts: list[str]) -> str:
    return "\n\n".join(part for part in (_clean(item).strip() for item in parts) if part)


def _squash(text: str) -> str:
    return " ".join(text.split())
