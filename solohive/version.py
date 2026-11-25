"""Версия агента для паспорта прогона: тег/коммит git, либо версия пакета."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from . import __version__

ROOT = Path(__file__).resolve().parents[1]


def _git(*args: str) -> str:
    try:
        out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=5)
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def agent_commit() -> str:
    """Короткий SHA коммита (+ тег и признак незакоммиченных изменений), если агент запущен из git-репозитория."""
    env = os.getenv("SOLOHIVE_COMMIT")
    if env:
        return env
    sha = _git("rev-parse", "--short", "HEAD")
    if not sha:
        return "нет данных git"
    tag = _git("describe", "--tags", "--exact-match")
    dirty = _git("status", "--porcelain", "--untracked-files=no")
    return f"{sha}" + (f" (тег {tag})" if tag else "") + (" + локальные изменения" if dirty else "")


def agent_version() -> str:
    return __version__
