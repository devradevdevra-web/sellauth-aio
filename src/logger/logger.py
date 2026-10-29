

from __future__ import annotations

import os
import re
import shutil
import sys
import threading
from datetime import datetime
from typing import Iterable, Optional

from colorama import Fore, init

init(autoreset=False)

BOLD = "\033[1m"
RESET = "\033[0m"
WHITE = "\033[97m"
PINK = "\033[38;5;198m"
RED = Fore.LIGHTRED_EX
YELLOW = Fore.YELLOW
CYAN = Fore.LIGHTCYAN_EX
GREEN = Fore.LIGHTGREEN_EX
BLUE = Fore.LIGHTBLUE_EX
MAGENTA = Fore.LIGHTMAGENTA_EX

_LEVEL_W = 7
_CONSOLE_ENABLED = True
_MIN_LEVEL = 20
_SHOW_FIELDS = True
_USE_COLOR = not bool(os.getenv("NO_COLOR"))
_OUTPUT_LOCK = threading.RLock()
_PANEL_PADDING: Optional[str] = None

_LEVELS = {
    "TRACE": 5,
    "DEBUG": 10,
    "INFO": 20,
    "SUCCESS": 25,
    "WARN": 30,
    "ERROR": 40,
}
_ICONS = {
    "TRACE": "›",
    "DEBUG": "·",
    "INFO": "●",
    "SUCCESS": "✓",
    "WARN": "▲",
    "ERROR": "✖",
    "INPUT": "?",
}
_COLORS = {
    "TRACE": CYAN,
    "DEBUG": BLUE,
    "INFO": PINK,
    "SUCCESS": GREEN,
    "WARN": YELLOW,
    "ERROR": RED,
    "INPUT": CYAN,
}
_PUBLIC_ADDRESS_LABELS = {"address", "crypto_address", "payment_address"}
_SENSITIVE_LABELS = {
    "api_key",
    "authorization",
    "body",
    "discord_username",
    "email",
    "key",
    "password",
    "secret",
    "token",
    "user",
    "user_id",
    "username",
    "wallet",
    "wallet_address",
}
_SECRET_PATTERN = re.compile(
    r"(?i)(bearer\s+|(?:api[_ -]?key|authorization|password|secret|token)\s*[:=]\s*)\S+"
)
_QUERY_PATTERN = re.compile(
    r"([?&](?:token|key|secret|signature|auth|password)=)[^\s&]+", re.I
)


def _time() -> str:
    
    return datetime.now().strftime("%I:%M %p")


def _stamp() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def _trim(arg: str, trim: bool) -> str:
    return arg[:40] if trim and len(arg) > 40 else arg


def _redact_text(value: object) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ")
    text = _SECRET_PATTERN.sub(r"\1<redacted>", text)
    return _QUERY_PATTERN.sub(r"\1<redacted>", text)


def _mask_value(label: object, value: object, trim: bool) -> str:
    label_text = str(label).strip().casefold().replace("-", "_").replace(" ", "_")
    if label_text in _PUBLIC_ADDRESS_LABELS:
        value_text = _redact_text(value)
    elif label_text in _SENSITIVE_LABELS:
        value_text = "<redacted>"
    elif label_text in {"wallet", "wallet_address"}:
        raw = _redact_text(value)
        value_text = f"{raw[:6]}…{raw[-4:]}" if len(raw) > 12 else "<redacted>"
    else:
        value_text = _redact_text(value)
    return _trim(value_text, trim)


def _normalize_fields(fields: Optional[Iterable]) -> list[tuple[str, str]]:
    normalized = []
    for item in fields or []:
        try:
            label, value = item
        except (TypeError, ValueError):
            normalized.append(("Context", _redact_text(item)))
            continue
        normalized.append((str(label), str(value)))
    return normalized


def _color(value: str) -> str:
    return value if _USE_COLOR else ""


def configure(settings: Optional[dict] = None) -> None:
    
    global _CONSOLE_ENABLED, _MIN_LEVEL, _SHOW_FIELDS, _USE_COLOR, _PANEL_PADDING

    settings = settings or {}
    level = str(settings.get("level", "INFO")).strip().upper()
    _MIN_LEVEL = _LEVELS.get(level, _LEVELS["INFO"])
    _CONSOLE_ENABLED = bool(settings.get("console", True))
    _SHOW_FIELDS = bool(settings.get("show_fields", True))
    _USE_COLOR = bool(settings.get("color", _USE_COLOR)) and not bool(
        os.getenv("NO_COLOR")
    )
    _PANEL_PADDING = None


def _print(
    level: str,
    color: str,
    message: str,
    fields: Optional[list] = None,
    trim: bool = True,
    *,
    force: bool = False,
) -> None:
    if not force and _LEVELS.get(level, _LEVELS["INFO"]) < _MIN_LEVEL:
        return

    normalized = _normalize_fields(fields)
    safe_message = _redact_text(message)
    if not _CONSOLE_ENABLED:
        return

    timestamp = _stamp()
    icon = _ICONS.get(level, "•")
    visible_prefix = f"{timestamp} │ {icon} {level:<7} │ "
    console_color = _color(color)

    detail_rows = []
    if normalized and _SHOW_FIELDS:
        indent = " " * len(visible_prefix)
        labelled = [f"{label}:" for label, _ in normalized]
        max_width = max(len(label) for label in labelled)
        for index, ((label, value), rendered_label) in enumerate(
            zip(normalized, labelled)
        ):
            branch = "└─" if index == len(normalized) - 1 else "├─"
            safe_value = _mask_value(label, value, trim)
            detail_rows.append(
                (branch, rendered_label.ljust(max_width), safe_value)
            )

    left_padding = _panel_padding()
    output_lines = [
        (
            f"{left_padding}{_color(BOLD)}{_color(WHITE)}{timestamp}"
            f"{_color(WHITE)} │ {console_color}{icon} {level:<7}"
            f"{_color(WHITE)} │ {console_color}{safe_message}{_color(RESET)}"
        )
    ]

    for branch, rendered_label, safe_value in detail_rows:
        output_lines.append(
            f"{left_padding}{indent}{_color(WHITE)}{branch} "
            f"{console_color}{rendered_label}{_color(RESET)} {safe_value}"
        )

    with _OUTPUT_LOCK:
        sys.stdout.write("\n".join(output_lines) + "\n")
        sys.stdout.flush()


def _terminal_width() -> int:
    
    try:
        return max(1, shutil.get_terminal_size(fallback=(100, 24)).columns)
    except OSError:
        return 100


def _panel_padding() -> str:
    
    global _PANEL_PADDING
    with _OUTPUT_LOCK:
        if _PANEL_PADDING is None:
            terminal_width = _terminal_width()
            content_width = min(96, max(52, terminal_width - 4))
            _PANEL_PADDING = " " * max(
                0, (terminal_width - content_width) // 2
            )
        return _PANEL_PADDING


def banner(title: str, subtitle: str = "", fields: Optional[list] = None) -> None:
    
    if not _CONSOLE_ENABLED:
        return
    safe_title = _redact_text(title)
    safe_subtitle = _redact_text(subtitle)
    width = max(
        52,
        min(96, len(safe_title) + 12, len(safe_subtitle) + 12 if safe_subtitle else 52),
    )
    line = "═" * width
    left_padding = _panel_padding()
    output_lines = [
        f"\n{left_padding}{_color(PINK)}╔{line}╗",
        (
            f"{left_padding}║  {_color(BOLD)}{_color(WHITE)}"
            f"{safe_title[: width - 4].center(width - 4)}{_color(RESET)}  ║"
        ),
    ]
    if safe_subtitle:
        output_lines.append(
            (
                f"{left_padding}║  {_color(WHITE)}"
                f"{safe_subtitle[: width - 4].center(width - 4)}{_color(RESET)}  ║"
            )
        )
    output_lines.append(f"{left_padding}╚{line}╝{_color(RESET)}")
    with _OUTPUT_LOCK:
        sys.stdout.write("\n".join(output_lines) + "\n")
        sys.stdout.flush()
    if fields:
        _print("INFO", CYAN, "Runtime configuration", fields)


def section(title: str) -> None:
    
    _print("INFO", MAGENTA, title, force=True)


def progress(
    message: str, fields: Optional[list] = None, *, done: bool = False
) -> None:
    
    if not _CONSOLE_ENABLED:
        return
    normalized = _normalize_fields(fields)
    summary = " | ".join(
        f"{label}={_mask_value(label, value, True)}" for label, value in normalized
    )
    suffix = f" │ {summary}" if summary else ""
    left_padding = _panel_padding()
    line = (
        f"{left_padding}{_stamp()} │ {_color(CYAN)}↻ {_color(WHITE)}"
        f"{_redact_text(message)}{suffix}{_color(RESET)}"
    )
    with _OUTPUT_LOCK:
        sys.stdout.write("\r" + line + " " * 8 + ("\n" if done else ""))
        sys.stdout.flush()


def exception(message: str, exc: BaseException, fields: Optional[list] = None) -> None:
    
    exception_fields = list(fields or [])
    exception_fields.append(("Exception", f"{type(exc).__name__}: {_redact_text(exc)}"))
    _print("ERROR", RED, message, exception_fields, trim=False)


def success(message: str, fields: list = None, trim: bool = True) -> None:
    _print("SUCCESS", GREEN, message, fields, trim)


def info(message: str, fields: list = None, trim: bool = True) -> None:
    _print("INFO", PINK, message, fields, trim)


def debug(message: str, fields: list = None, trim: bool = True) -> None:
    _print("DEBUG", BLUE, message, fields, trim)


def trace(message: str, fields: list = None) -> None:
    _print("TRACE", CYAN, message, fields)


def warn(message: str, fields: list = None, trim: bool = True) -> None:
    _print("WARN", YELLOW, message, fields, trim)


def error(message: str, fields: list = None, trim: bool = True) -> None:
    _print("ERROR", RED, message, fields, trim)


def capchaerror(message: str, fields: list = None, trim: bool = True) -> None:
    
    _print("ERROR", RED, message, fields, trim)


def take_input(message: str) -> str:
    safe_message = _redact_text(message)
    return input(
        f"{_color(BOLD)}{_color(WHITE)}{_time()} │ "
        f"{_color(CYAN)}{_ICONS['INPUT']} {'INPUT'.ljust(_LEVEL_W)}"
        f"{_color(WHITE)} │ {safe_message}{_color(RESET)}"
    )


def timestamp() -> str:
    return _time()
