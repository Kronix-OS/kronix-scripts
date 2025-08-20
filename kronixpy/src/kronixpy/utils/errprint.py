import io
import string
import atexit
from enum import IntFlag

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


def pdebug(msg: str, end: str | None = "\n", where: Print = Print.BOTH):
    global _debug
    if _debug:
        if where & Print.TERM:
            print(f"{PDEBUG_START}{msg}{PDEBUG_END}", end=end)
        if where & Print.LOGFILE:
            _handle_logfile(f"{LOG_PDEBUG_START}{msg}{LOG_PDEBUG_END}" + (end or ""))
    return None


def pinfo(msg: str, end: str | None = "\n", where: Print = Print.BOTH):
    if where & Print.TERM:
        print(f"{PINFO_START}{msg}{PINFO_END}", end=end)
    if where & Print.LOGFILE:
        _handle_logfile(f"{LOG_PINFO_START}{msg}{LOG_PINFO_END}" + (end or ""))
    return None


def pwarning(msg: str, end: str | None = "\n", where: Print = Print.BOTH):
    if where & Print.TERM:
        print(f"{PWARNING_START}{msg}{PWARNING_END}", end=end)
    if where & Print.LOGFILE:
        _handle_logfile(f"{LOG_PWARNING_START}{msg}{LOG_PWARNING_END}" + (end or ""))
    return None


def perror(msg: str, end: str | None = "\n", where: Print = Print.BOTH):
    if where & Print.TERM:
        print(f"{PERROR_START}{msg}{PERROR_END}", end=end)
    if where & Print.LOGFILE:
        _handle_logfile(f"{LOG_PERROR_START}{msg}{LOG_PERROR_END}" + (end or ""))
    return None


def make_tty_link(text: str, url: str) -> str:
    template = string.Template(f"{ESC}]8;;${{link}}{ESC}\\${{text}}{ESC}]8;;{ESC}\\\n")
    return template.safe_substitute(link=url, text=text)
