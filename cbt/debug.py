import os
import sys
import threading
import traceback
from io import TextIOWrapper
from threading import Lock

from . import Config, util
from .ansi import ANSI


class Debug:
    _ready: bool = False
    _debug_file: TextIOWrapper | None = None
    _debug_on: bool = False
    _lock: Lock = Lock()

    @staticmethod
    def ready():
        Debug._debug_on = Config.getbool("debug_on") or False
        if not Debug._ready and Debug._debug_on:
            try:
                Debug._debug_file = open(Config.getstr("debug_file") or "debug_file", "a", buffering=1)
                Debug._ready = True
                sys.excepthook = Debug._exc_handler
                threading.excepthook = Debug._th_exc_handler
            except Exception as e:
                print(repr(e), file=sys.stderr)
                sys.exit(3)

    @staticmethod
    def mode():
        Debug._chk_file()
        return Debug._debug_on and Debug._ready

    @staticmethod
    def write(message="=== == === == ==="):
        Debug._chk_file()
        if Debug._ready and Debug._debug_file:
            with Debug._lock:
                Debug._debug_file.write(util.time_str() + ": " + ANSI.remove_ansi(message) + "\n")
                Debug._debug_file.flush()

    @staticmethod
    def writetb():
        Debug._chk_file()
        if Debug._ready and Debug._debug_file:
            with Debug._lock:
                Debug._debug_file.write(util.time_str() + "\n\n" + ANSI.remove_ansi(traceback.format_exc()) + "\n\n")
                Debug._debug_file.flush()

    @staticmethod
    def close():
        Debug._chk_file()
        if Debug._ready and Debug._debug_file:
            with Debug._lock:
                Debug._debug_file.close()
                Debug._debug_file = None
                Debug._ready = False

    @staticmethod
    def _chk_file():
        if Debug._ready and Debug._debug_file:
            if os.fstat(Debug._debug_file.fileno()).st_nlink == 0:
                Debug._ready = False
                Debug.ready()
                Debug.write()
                Debug.write("Debug-file deleted")

    @staticmethod
    def _exc_handler(exc_type, exc_value, exc_traceback):
        if Debug._ready and Debug._debug_file:
            with Debug._lock:
                Debug._debug_file.write(util.time_str() + "\n\n")
                traceback.print_exception(exc_type, exc_value, exc_traceback, file=Debug._debug_file)
                Debug._debug_file.write("\n\n")
                Debug._debug_file.flush()

    @staticmethod
    def _th_exc_handler(args):
        Debug._exc_handler(args.exc_type, args.exc_value, args.exc_traceback)
