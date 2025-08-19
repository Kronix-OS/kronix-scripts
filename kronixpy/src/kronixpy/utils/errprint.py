import io
import string
import atexit
import time
from enum import IntFlag
from typing import Callable, Optional
from threading import RLock
from .mutex import Mutex

ESC: str = "\033"

# fmt: off
PDEBUG_START:   str = f"{ESC}[1;34m  [DEBUG]{ESC}[0m  "
PDEBUG_END:     str =  ""
PINFO_START:    str = f"{ESC}[1;32m  [INFO ]{ESC}[0m  "
PINFO_END:      str =  ""
PWARNING_START: str = f"{ESC}[1;33m  [WARN ]{ESC}[0m  "
PWARNING_END:   str =  ""
PERROR_START:   str = f"{ESC}[1;31m  [ERROR]{ESC}[0m  "
PERROR_END:     str =  ""

LOG_PDEBUG_START:   str = "  [DEBUG]  "
LOG_PDEBUG_END:     str = ""
LOG_PINFO_START:    str = "  [INFO ]  "
LOG_PINFO_END:      str = ""
LOG_PWARNING_START: str = "  [WARN ]  "
LOG_PWARNING_END:   str = ""
LOG_PERROR_START:   str = "  [ERROR]  "
LOG_PERROR_END:     str = ""
# fmt: on

_debug: bool = False
_logfile: io.TextIOWrapper | None = None
_bottom_text = None
_last_spinner_update = 0
_spinner_index = 0
_global_io_lock = RLock()

SPINNER = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
SPINNER_NS_INTERVAL = 250 * 1000 * 1000  # ms * 1000 * 1000 = ns


@atexit.register
def _flush_logfile():
    global _logfile
    if _logfile is not None:
        _logfile.flush()
    return None


def _handle_logfile(msg: str):
    global _logfile
    if _logfile is not None:
        _logfile.write(msg)
    return None


def set_logfile(filename: str):
    global _logfile
    _logfile = open(filename, "w")
    return None


def get_logfile() -> io.TextIOWrapper | None:
    global _logfile
    return _logfile


def set_debug_mode(active: bool = True):
    global _debug
    _debug = active
    return None


def get_debug_mode() -> bool:
    global _debug
    return _debug


class Print(IntFlag):
    TERM = 1 << 0
    LOGFILE = 1 << 1
    BOTH = TERM | LOGFILE


def _do_update_spinner():
    global _global_io_lock, _bottom_text, _last_spinner_update, _spinner_index
    global SPINNER, SPINNER_NS_INTERVAL
    with _global_io_lock:
        if _bottom_text is not None:
            currtime = time.time_ns()
            if currtime - _last_spinner_update > SPINNER_NS_INTERVAL:
                _last_spinner_update = currtime
                _spinner_index = (_spinner_index + 1) % len(SPINNER)
    return None


def _erase_bottom_text():
    global _global_io_lock, _bottom_text
    with _global_io_lock:
        if _bottom_text is not None:
            _del_line()
    return None


def _write_bottom_text():
    global _global_io_lock, _bottom_text
    with _global_io_lock:
        if _bottom_text is not None:
            _do_update_spinner()
            print(f"{_bottom_text} {SPINNER[_spinner_index]}", end=None)
    return None


def praw(msg: str, end: Optional[str] = "\n", where: Print = Print.BOTH):
    global _global_io_lock, _bottom_text
    with _global_io_lock:
        if where & Print.TERM:
            _erase_bottom_text()
            print(msg, end=end)
            _write_bottom_text()
        if where & Print.LOGFILE:
            _handle_logfile(msg + (end or ""))
    return None


def pdebug(msg: str, end: Optional[str] = "\n", where: Print = Print.BOTH):
    global _global_io_lock, _debug, _bottom_text
    with _global_io_lock:
        if _debug:
            praw(msg=f"{PDEBUG_START}{msg}{PDEBUG_END}", end=end, where=where)
    return None


def pinfo(msg: str, end: Optional[str] = "\n", where: Print = Print.BOTH):
    return praw(f"{PINFO_START}{msg}{PINFO_END}", end=end, where=where)


def pwarning(msg: str, end: Optional[str] = "\n", where: Print = Print.BOTH):
    return praw(f"{PWARNING_START}{msg}{PWARNING_END}", end=end, where=where)


def perror(msg: str, end: Optional[str] = "\n", where: Print = Print.BOTH):
    return praw(f"{PERROR_START}{msg}{PERROR_END}", end=end, where=where)


def make_tty_link(text: str, url: str) -> str:
    template = string.Template(f"{ESC}]8;;${{link}}{ESC}\\${{text}}{ESC}]8;;{ESC}\\\n")
    return template.safe_substitute(link=url, text=text)


def register_bottom_text(text: str):
    global _bottom_text, _last_spinner_update, _spinner_index, _global_io_lock
    with _global_io_lock:
        _bottom_text = text
        _write_bottom_text()
        _handle_logfile(f"{text}\n")
    return None


def _del_line():
    return print(f"{ESC}[2K\r", end=None)


def update_spinner():
    return praw("", end=None, where=Print.TERM)


def unregister_bottom_text():
    global _bottom_text, _global_io_lock
    with _global_io_lock:
        if _bottom_text is None:
            return None
        _del_line()
        _bottom_text = None
    return None
