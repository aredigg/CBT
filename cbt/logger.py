import os

from .ansi import ANSI
from .util import get_time, seconds_ago, time_str


class Logger:
    DBG: str = "debug"
    INF: str = "information"
    WRN: str = "warnings"
    ERR: str = "errors"

    def __init__(self, log_file=None, max_len=100, flush_lines=10, flush_interval=60) -> None:
        self.__max_len = max_len
        self.__messages = {
            Logger.DBG: [],
            Logger.INF: [],
            Logger.WRN: [],
            Logger.ERR: [],
        }
        self.__flush_config = (flush_interval, flush_lines)
        self.__last_flush = get_time()
        self.__log_buffer = []
        self.__log_file = log_file
        self.__download_file_name = ""

    def __trim(self):
        for q in self.__messages:
            while len(self.__messages[q]) > self.__max_len:
                self.__messages[q].pop(0)

    def __split(self, message):
        extractor = ""
        channel = ""
        parts = message.split()
        if parts[0].startswith("[") and parts[0].endswith("]"):
            extractor = parts[0][1:-1]
            if len(parts) > 1:
                if extractor not in ["download"] and parts[1].endswith(":"):
                    channel = parts[1][:-1]
                    if len(parts) > 2:
                        message = " ".join(parts[2:])
                else:
                    message = " ".join(parts[1:])
        return extractor, channel, message

    def __append(self, kind, message):
        message = ANSI.remove_ansi(message)
        if message.startswith("ERROR: "):
            self.__append(Logger.ERR, message.removeprefix("ERROR: "))
        elif message.startswith("[download] "):
            if "Destination:" in message:
                try:
                    _, _, self.__download_file_name = message.strip().split()
                except ValueError:
                    pass
            self.__append(Logger.DBG, message.replace("[download] ", "Download: "))
        else:
            message = self.__split(message)
            self.__messages[kind].append(message)
            self.__write_to_log(kind, message)
            self.__trim()

    def __write_to_log(self, kind, message):
        extractor, channel, message = message
        self.__log_buffer.append((kind, extractor, channel, message))
        if self.__log_file is not None and (
            len(self.__log_buffer) > self.__flush_config[0] or seconds_ago(self.__last_flush, self.__flush_config[1])
        ):
            if not os.path.isfile(self.__log_file):
                with open(self.__log_file, "w") as lf:
                    lf.write("Timestamp;Kind;Extractor;Channel;Message\n")
            with open(self.__log_file, "a") as lf:
                lf.write(f"{time_str()};{kind.upper():1.1};{extractor};{channel};{message}\n")

    def debug(self, message):
        self.__append(Logger.DBG, message)

    def info(self, message):
        self.__append(Logger.INF, message)

    def warning(self, message):
        self.__append(Logger.WRN, message)

    def error(self, message):
        self.__append(Logger.ERR, message)

    def waiting(self, kind=None):
        if kind is not None:
            return len(self.__messages[kind]) > 0
        return sum([len(self.__messages[q]) for q in self.__messages]) > 0

    def messages(self, kind=None) -> list[tuple[str, str, str]]:
        if kind is not None:
            if self.__messages[kind]:
                result = self.__messages[kind].copy()
                self.__messages[kind].clear()
                return result
            return []
        result = [
            q + "> " + message for q, messages in self.__messages.items() for _, _, message in messages if messages
        ]
        [messages.clear() for messages in self.__messages.values()]  # Comprehende?
        return result

    def download_filename(self):
        return self.__download_file_name

    def set_download_filename(self, filename):
        self.__download_file_name = filename or ""
