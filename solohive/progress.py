"""Вывод хода прогона в консоль (rich, если установлен)."""

from __future__ import annotations

import time

try:
    from rich.console import Console
    _console: Console | None = Console(highlight=False)
except ImportError:  # pragma: no cover
    _console = None


class Progress:
    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.t0 = time.monotonic()

    def elapsed(self) -> float:
        return time.monotonic() - self.t0

    def print(self, text: str, style: str | None = None) -> None:
        if not self.enabled:
            return
        if _console:
            _console.print(text, style=style)
        else:
            import re
            print(re.sub(r"\[/?[a-z ]+\]", "", text))

    def step(self, icon: str, name: str, info: str) -> None:
        self.print(f"  [dim]{self.elapsed():6.1f}s[/dim]  {icon} [bold]{name:<22}[/bold] → {info}")

    def note(self, text: str) -> None:
        self.print(f"  [dim]{self.elapsed():6.1f}s  ⏳ {text}[/dim]")

    def warn(self, text: str) -> None:
        self.print(f"  ⚠️  [yellow]{text}[/yellow]")
