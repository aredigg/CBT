import os
import socket
import subprocess
import sys
from dataclasses import dataclass
from importlib.metadata import version

from . import Config, util


class Health:
    DRIVE_OUTD = "O"
    DRIVE_TEMP = "T"
    TEST_IP = "1.1.1.1"

    @dataclass
    class Drive:
        free_percent: float
        directory: str | None
        low_space: bool
        available: bool
        inaccesible: bool

    @dataclass
    class Version:
        current: str | None
        latest: str | None
        update_available: bool
        mismatch: bool

    @dataclass
    class Internet:
        local_addr: str | None
        link_up: bool
        error: str | None

    def __init__(self) -> None:
        self.__output_directory: str | None = Config.getstr("output_directory")
        self.__temporary_storage: str | None = Config.getstr("temporary_storage")
        self.__free_percent_limit: float = Config.getint("free_percent_limit") or 0.1
        self.__last_update_check: Health.Version | None = None
        self.__last_update_time = util.get_time()

    def disk_health(self):
        drives = {
            Health.DRIVE_OUTD: Health.Drive(0.0, self.__output_directory, False, False, True),
            Health.DRIVE_TEMP: Health.Drive(0.0, self.__temporary_storage, False, False, True),
        }
        try:
            for id, drive in drives.items():
                if drive.directory is not None:
                    available = os.path.exists(drive.directory)
                    inaccesible = not os.access(drive.directory, os.W_OK)

                    stat = os.statvfs(drive.directory)
                    percent_free = stat.f_bfree / stat.f_blocks
                    drives[id] = Health.Drive(
                        percent_free, drive.directory, percent_free < self.__free_percent_limit, available, inaccesible
                    )
        except FileNotFoundError:
            pass
        return drives

    def ytdlp_version(self):
        if self.__last_update_check is None or not util.same_date(self.__last_update_time):
            current = version("yt_dlp")
            installed = None
            latest = None
            result = subprocess.run(
                [sys.executable, "-m", "pip", "index", "versions", "yt-dlp"], capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                for ln in result.stdout.split("\n"):
                    if "INSTALLED:" in ln:
                        _, installed = ln.strip().split()
                    if "LATEST:" in ln:
                        _, latest = ln.strip().split()
            self.__last_update_check = Health.Version(current, latest, current != latest, current == installed)
            self.__last_update_time = util.get_time()
        return self.__last_update_check

    def internet(self):
        up = False
        addr = None
        err = None
        try:
            s = socket.create_connection((Health.TEST_IP, 53), timeout=10)
            up = True
            addr = s.getsockname()[0]
        except TimeoutError as e:
            err = str(e)
        except ConnectionRefusedError as e:
            err = str(e)
            up = True
        except OSError as e:
            err = str(e)
            pass
        except Exception as e:
            err = repr(e)
        return Health.Internet(addr, up, err)
