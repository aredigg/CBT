import sys
import traceback
from io import TextIOWrapper
from threading import Lock

from . import Config, util
from .ansi import ANSI


class Debug:
    _ready: bool = False
    _debug_file: TextIOWrapper | None = None
    _debug_on: bool = Config.getbool("debug_on") or False
    _lock: Lock = Lock()

    @staticmethod
    def write(message):
        if Debug._ready and Debug._debug_file:
            with Debug._lock:
                Debug._debug_file.write(util.time_str() + ": " + ANSI.remove_ansi(message) + "\n")

    @staticmethod
    def writetb():
        if Debug._ready and Debug._debug_file:
            with Debug._lock:
                Debug._debug_file.write(util.time_str() + "\n" + ANSI.remove_ansi(traceback.format_exc()) + "\n")

    @staticmethod
    def close():
        if Debug._ready and Debug._debug_file:
            with Debug._lock:
                Debug._debug_file.close()
                Debug._debug_file = None
                Debug._ready = False


if not Debug._ready and Debug._debug_on:
    try:
        Debug._debug_file = open(Config.getstr("debug_file") or "debug_file", "a")
        Debug._ready = True and Debug._debug_on
    except Exception as e:
        print(repr(e), file=sys.stderr)
        sys.exit(3)
