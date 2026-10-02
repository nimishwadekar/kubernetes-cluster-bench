from __future__ import annotations

import sys
from typing import TextIO


RED = "\033[0;31m"
YELLOW = "\033[0;33m"
ORANGE = "\033[38;5;208m"
RESET = "\033[0m"


def supports_color(stream: TextIO) -> bool:
    return stream.isatty()


def format_message(label: str, message: str, color: str, stream: TextIO) -> str:
    if supports_color(stream):
        return f"{color}{label}:{RESET} {message}"
    return f"{label}: {message}"


def format_error(message: str, stream: TextIO) -> str:
    return format_message("ERROR", message, RED, stream)


def colored_options(options: tuple[str, ...], stream: TextIO) -> str:
    option_text = ", ".join(options)
    if supports_color(stream):
        return f"{YELLOW}{option_text}{RESET}"
    return option_text


def warning(message: str) -> None:
    print(format_message("WARNING", message, ORANGE, sys.stderr), file=sys.stderr)
