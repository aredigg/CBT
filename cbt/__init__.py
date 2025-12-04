__version__ = "3.01"
__author__ = "The Duck"
__program_name__ = "Channel Broadcast Tracker"


class Config:
    settings = {}
    logger = None

    POLL = 0.05
    KILL = 1.00

    REC = "RECORDING"
    UNR = "UNRESPONSIVE"
    FIN = "FINISHED"

    MSG_DISP = -1
    MSG_SLOT = -2
    MSG_CHAN = -3

    ydl_defaults = {
        "ignoreerrors": True,
        "live_from_start": False,
        "multistreams": True,
        "retries": 5,
        "sleep_interval": 10,
        "max_sleep_interval": 60,
        "sleep_interval_requests": 0.01,
        "paths": {},
        "cookiesfrombrowser": ("safari", None, None, None),
        "outtmpl": "%(epoch>%Y-%m)s/%(epoch>W%W)s/%(epoch>%a)s/%(id)s.%(ext)s",
        "writesubtitles": True,
        "writeautomaticsub": False,
        "subtitleslangs": ["all"],
        "writedescription": False,
        "writeinfojson": False,
        "hls_prefer_native": True,
        "external_downloader_args": {"ffmpeg": ["-loglevel", "quiet", "-hide_banner", "-nostats"]},
        "downloader_args": {
            "ffmpeg": ["-loglevel", "quiet", "-hide_banner", "-nostats"],
            "ffmpeg_i": ["-rw_timeout", "30000000"],
        },
        "postprocessor_args": {"ffmpeg": ["-loglevel", "error", "-hide_banner", "-nostats"]},
    }

    @staticmethod
    def load():
        with open("config.ini") as config:
            for ln in config:
                if not ln.startswith("#"):
                    ln = ln.split("#")[0]  # TODO We have to check if this will suffice
                    if ln.count("=") > 0:
                        id, value = ln.strip().split("=")
                        id = id.strip()
                        value = value.strip()
                        Config.settings[id] = value

    @staticmethod
    def save():
        with open("config.ini", "w") as config:
            for key, value in Config.settings.items():
                config.write(f"{key} = {value}\n")

    @staticmethod
    def __get(setting: str) -> float | int | str | bool | None:
        value = Config.settings.get(setting)
        if value is None or value == "None":
            return None
        try:
            return int(value)
        except ValueError:
            ...
        try:
            return float(value)
        except ValueError:
            ...
        if isinstance(value, str):
            if value.casefold() in ["true", "yes"]:
                return True
            if value.casefold() in ["false", "no"]:
                return False
        return value

    @staticmethod
    def getint(setting: str) -> int | None:
        value = Config.__get(setting)
        if isinstance(value, int):
            return value
        return None

    @staticmethod
    def getstr(setting: str) -> str | None:
        value = Config.__get(setting)
        if isinstance(value, str):
            return value
        return None

    @staticmethod
    def set_args(args: list[str]):
        for i in range(1, len(args) - 1):
            if args[i] == "=" and i < len(args) - 1:
                Config.settings[args[i - 1]] = args[i + 1]
        Config.save()

    @staticmethod
    def add_paths(home=None, temp=None):
        if home is not None:
            Config.ydl_defaults["paths"]["home"] = home
        if temp is not None:
            Config.ydl_defaults["paths"]["temp"] = temp
