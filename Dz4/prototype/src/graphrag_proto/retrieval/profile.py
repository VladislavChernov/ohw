"""Загрузчик активного Domain Profile для ретривера (L1-01: домен-агностичность).

Порядок: активный домен и YAML профиля из Config Service (CONFIG_URL), иначе — локальный
каталог `DOMAIN_PROFILES_DIR` (fallback как в glossary_service, docs/04).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_DOMAIN = "it"


class ProfileError(ValueError):
    pass


def _is_safe_domain(name: str) -> bool:
    return bool(name) and not ("/" in name or "\\" in name or name.startswith("."))


def _profiles_dir() -> Path:
    env = os.environ.get("DOMAIN_PROFILES_DIR")
    return Path(env) if env else Path("domain_profiles")


class DomainProfileLoader:
    """Источник профилей: Config Service (remote) -> локальный YAML (fallback)."""

    def __init__(self, config_url: str = "", profiles_dir: Path | None = None, default: str = DEFAULT_DOMAIN) -> None:
        self._config_url = config_url.rstrip("/") if config_url else ""
        self._profiles_dir = profiles_dir or _profiles_dir()
        self._default = default

    def active_domain(self) -> str:
        if self._config_url:
            try:
                with urllib.request.urlopen(f"{self._config_url}/api/v1/config/domain/active", timeout=3) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                domain = data.get("domain")
                if isinstance(domain, str) and _is_safe_domain(domain) and domain:
                    return domain
            except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError):
                pass
        return self._default

    def load(self, domain: str | None = None) -> dict[str, Any]:
        domain = domain or self.active_domain()
        if not _is_safe_domain(domain):
            raise ProfileError(f"небезопасное имя домена: {domain!r}")
        if self._config_url:
            try:
                with urllib.request.urlopen(
                    f"{self._config_url}/api/v1/config/domain/profile/{domain}", timeout=3
                ) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                if isinstance(data, dict):
                    return data
            except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError):
                pass
        path = self._profiles_dir / f"domain_profile.{domain}.yaml"
        if not path.is_file():
            raise ProfileError(f"профиль '{domain}' не найден")
        import yaml

        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if not isinstance(data, dict):
            raise ProfileError(f"профиль '{domain}' должен быть YAML-маппингом")
        return data