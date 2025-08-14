PDEBUG_START:   str = "\033[1;34m  [DEBUG]\033[0m  "
PDEBUG_END:     str = ""
PINFO_START:    str = "\033[1;32m  [INFO ]\033[0m  "
PINFO_END:      str = ""
PWARNING_START: str = "\033[1;33m  [WARN ]\033[0m  "
PWARNING_END:   str = ""
PERROR_START:   str = "\033[1;31m  [ERROR]\033[0m  "
PERROR_END:     str = ""

LOG_PDEBUG_START:   str = "  [DEBUG]  "
LOG_PDEBUG_END:     str = ""
LOG_PINFO_START:    str = "  [INFO ]  "
LOG_PINFO_END:      str = ""
LOG_PWARNING_START: str = "  [WARN ]  "
LOG_PWARNING_END:   str = ""
LOG_PERROR_START:   str = "  [ERROR]  "
LOG_PERROR_END:     str = ""

import io
import string

_debug: bool = False
_logfile: io.TextIOWrapper | None = None

def _handle_logfile(msg: str) -> None:
    global _logfile
    if _logfile is not None:
        _logfile.write(msg)
        _logfile.flush()
    return None

def set_logfile(filename: str) -> None:
    global _logfile
    _logfile = open(filename, "w")
    return None

def get_logfile() -> io.TextIOWrapper | None:
    global _logfile
    return _logfile

def set_debug_mode(active: bool = True) -> None:
    global _debug
    _debug = active
    return None

def get_debug_mode() -> bool:
    global _debug
    return _debug

def pdebug(msg: str, end: str | None = '\n') -> None:
    global _debug
    if _debug:
        print(f"{PDEBUG_START}{msg}{PDEBUG_END}", end=end)
        _handle_logfile(f"{LOG_PDEBUG_START}{msg}{LOG_PDEBUG_END}" + end if end is not None else "")
    return None

def pinfo(msg: str, end: str | None = '\n') -> None:
    print(f"{PINFO_START}{msg}{PINFO_END}", end=end)
    _handle_logfile(f"{LOG_PINFO_START}{msg}{LOG_PINFO_END}" + end if end is not None else "")
    return None

def pwarning(msg: str, end: str | None = '\n') -> None:
    print(f"{PWARNING_START}{msg}{PWARNING_END}", end=end)
    _handle_logfile(f"{LOG_PWARNING_START}{msg}{LOG_PWARNING_END}" + end if end is not None else "")
    return None

def perror(msg: str, end: str | None = '\n') -> None:
    print(f"{PERROR_START}{msg}{PERROR_END}", end=end)
    _handle_logfile(f"{LOG_PERROR_START}{msg}{LOG_PERROR_END}" + end if end is not None else "")
    return None

def make_tty_link(text: str, url: str) -> str:
    template = string.Template('\033]8;;${link}\033\\${text}\033]8;;\033\\\n')
    return template.safe_substitute(link=url, text=text)