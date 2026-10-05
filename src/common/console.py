from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import TextIO


@dataclass(frozen=True)
class Colors:
    """ANSI colors shared by the command-line tools."""

    red: str
    green: str
    yellow: str
    cyan: str
    orange: str
    reset: str


_ANSI_COLORS = Colors(
    red="\033[0;31m",
    green="\033[0;32m",
    yellow="\033[0;33m",
    cyan="\033[0;36m",
    orange="\033[38;5;208m",
    reset="\033[0m",
)
_NO_COLORS = Colors(red="", green="", yellow="", cyan="", orange="", reset="")


def supports_color(stream: TextIO) -> bool:
    """Return whether a stream is attached to a terminal."""

    return stream.isatty()


def terminal_colors(*streams: TextIO) -> Colors:
    """Enable colors when at least one output stream is attached to a terminal."""

    return _ANSI_COLORS if any(supports_color(stream) for stream in streams) else _NO_COLORS


def format_message(label: str, message: str, color: str, stream: TextIO) -> str:
    if supports_color(stream):
        return f"{color}{label}:{_ANSI_COLORS.reset} {message}"
    return f"{label}: {message}"


def format_error(message: str, stream: TextIO) -> str:
    return format_message("ERROR", message, _ANSI_COLORS.red, stream)


def colored_options(options: tuple[str, ...], stream: TextIO) -> str:
    option_text = ", ".join(options)
    if supports_color(stream):
        return f"{_ANSI_COLORS.yellow}{option_text}{_ANSI_COLORS.reset}"
    return option_text


def warning(message: str, stream: TextIO | None = None) -> None:
    output = sys.stderr if stream is None else stream
    print(format_message("WARNING", message, _ANSI_COLORS.orange, output), file=output)
