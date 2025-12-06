import os
import queue
import sys
import time
import traceback
from dataclasses import asdict, dataclass
from datetime import datetime
from queue import Queue
from threading import RLock, Thread

from . import Config, util
from .ansi import ANSI
from .display import DisplayController, HealthBar, StatusBarMessage
from .health import Health
from .slot import Slot, SubprocessMonitor


@dataclass
class Channel:
    name: str
    rank: int | str | None
    last_download: datetime | str | None
    last_complete: datetime | str | None
    last_attempt: datetime | str | None
    last_error: datetime | str | None
    last_error_message: str | None
    last_resolution: str | None
    last_bitrate: str | None

    def __post_init__(self):
        self.rank = int(self.rank) if self.rank is not None else None
        self.last_download = (
            util.str_time(self.last_download)
            if self.last_download is not None and self.last_download != "None"
            else None
        )
        self.last_complete = (
            util.str_time(self.last_complete)
            if self.last_complete is not None and self.last_complete != "None"
            else None
        )
        self.last_attempt = (
            util.str_time(self.last_attempt) if self.last_attempt is not None and self.last_attempt != "None" else None
        )
        self.last_error = (
            util.str_time(self.last_error) if self.last_error is not None and self.last_error != "None" else None
        )
        self.last_error_message = self.last_error_message if self.last_error_message != "None" else None
        self.last_resolution = self.last_resolution if self.last_resolution != "None" else None
        self.last_bitrate = self.last_bitrate if self.last_bitrate != "None" else None


class Channels:
    __headers = [
        "# Channel",
        "Rank",
        "Last Download",
        "Last Complete",
        "Last Attempt",
        "Last Error",
        "Last Error Message",
        "Resolution",
        "Bitrate",
    ]
    __header = ";".join(__headers) + "\n"
    __header_len = len(__headers)

    def __init__(self) -> None:
        self.__status_value = 0
        self.__status = None
        self.__running = True
        self.__health_interval = Config.getint("health_interval") or 1
        self.__last_health_check = None
        self.__channels = []
        self.__prefix = None
        self.__offline_window = Config.getint("offline_window")
        try:
            if channels_file := Config.getstr("channels_file"):
                with open(channels_file) as f:
                    self.__channels = [
                        Channel(*p)
                        for ln in f
                        if (s := ln.strip())
                        and not s.startswith("#")
                        and (p := s.split(";"))
                        and len(p) >= Channels.__header_len
                    ]
            else:
                self.__status = "Channels file not set"
                self.__status_value = 2
            self.__add_channels()
        except FileNotFoundError:
            if not self.__add_channels():
                self.__status = "Channels file not found"
                self.__status_value = 1
        try:
            if channels_prefix := Config.getstr("channel_prefix"):
                with open(channels_prefix) as f:
                    self.__prefix = f.readline().strip()
            else:
                self.__status = "Channel prefix file not set"
                self.__status_value = 2
        except FileNotFoundError:
            self.__status = "Channel prefix file not found"
            self.__status_value = 1
        if len(self.__channels) < 1:
            self.__status = "No channels imported"
            self.__status_value = 3
        self.__remove_duplicates()

    def __remove_duplicates(self):
        seen = {}
        result = []
        for channel in self.__channels:
            if channel.name not in seen:
                seen[channel.name] = len(result)
                result.append(channel)
            else:
                index = seen[channel.name]
                keep: Channel = result[index]
                result[index] = Channel(
                    name=keep.name,
                    rank=keep.rank,
                    last_attempt=keep.last_attempt if keep.last_attempt is not None else channel.last_attempt,
                    last_bitrate=keep.last_bitrate if keep.last_bitrate is not None else channel.last_bitrate,
                    last_complete=keep.last_complete if keep.last_complete is not None else channel.last_complete,
                    last_download=keep.last_download if keep.last_download is not None else channel.last_download,
                    last_error=keep.last_error if keep.last_error is not None else channel.last_error,
                    last_error_message=keep.last_error_message
                    if keep.last_error_message is not None
                    else channel.last_error_message,
                    last_resolution=keep.last_resolution
                    if keep.last_resolution is not None
                    else channel.last_resolution,
                )
        self.__channels = result

    def __slot_callback(self, q):
        while self.__running:
            try:
                receiver, response = q.get(timeout=Config.POLL)
                if receiver == Config.MSG_CHAN:
                    if isinstance(response, Channel):
                        for i in range(len(self.__channels)):
                            if self.__channels[i].name == response.name:
                                self.__channels[i] = response
                else:
                    q.put((receiver, response))
                time.sleep(Config.POLL)
            except queue.Empty:
                pass

    def __add_channels(self) -> bool:
        if channels_file := Config.getstr("channels_file"):
            try:
                with open(channels_file + "_") as f:
                    for ln in f:
                        if (s := ln.strip().split(";")[0]) and not s.startswith("#"):
                            self.__channels.append(Channel(s, 5, None, None, None, None, None, None, None))
                self.__save_channels()
                os.remove(channels_file + "_")
            except FileNotFoundError:
                return False
        else:
            return False
        return True

    def __format(self, value):
        return util.time_str(value) if isinstance(value, datetime) else str(value)

    def __save_channels(self):
        if channels_file := Config.getstr("channels_file"):
            try:
                with open(channels_file, "w") as f:
                    f.write(self.__header)
                    for channel in self.__channels:
                        f.write(";".join(self.__format(v) for v in asdict(channel).values()) + "\n")
            except FileNotFoundError:
                pass

    def __get_rank(self, index: int) -> int:
        if index <= 25:
            return 0
        if index <= 50:
            return 1
        if index <= 100:
            return 2
        if index <= 200:
            return 3
        if index <= 300:
            return 4
        return 5

    def __create_slots(self, q, lock) -> list[Slot]:
        if number_of_slots := Config.getint("number_of_slots"):
            return [Slot(q, lock, self.__prefix) for _ in range(number_of_slots)]
        self.__status = "Number of slots not set"
        self.__status_value = 2
        return []

    def __next_channel(self, current_index, slots, recursions):
        if recursions > 3:
            time.sleep(
                10
            )  # simple hack, we can recurse almost 1000 times, so sooner or later it should be more channels
        ch = self.__channels[current_index]
        while isinstance(ch.last_complete, datetime) and util.same_date(ch.last_complete):
            current_index = (current_index + 1) % len(self.__channels)
            ch = self.__channels[current_index]
        for slot in slots:
            if self.__running and slot.check_name(ch.name):
                current_index, ch = self.__next_channel(
                    (current_index + 1) % len(self.__channels), slots, recursions + 1
                )
        return current_index, ch

    def manager(self) -> bool:
        slots = []
        lock = RLock()
        q = Queue()
        controller = DisplayController(q)
        callback = Thread(target=self.__slot_callback, args=[q], daemon=True)
        callback.start()
        subprocess_monitor = SubprocessMonitor(q)
        slots = self.__create_slots(q, lock)
        self.__running = True
        offline_checkpoint = util.get_time()
        channel_index = 0
        health = Health()
        while self.__running and self.__status_value == 0:
            try:
                self.__health_check(q, health)
                for slot in slots:
                    channel_index, channel = self.__next_channel(channel_index, slots, 0)
                    if controller.shutdown_requested():
                        self.__running = False
                    if self.__running and not slot.busy():
                        channel.rank = self.__get_rank(channel_index)
                        slot.process(channel)
                        channel_index = (channel_index + 1) % len(self.__channels)
                    if util.hours_ago(offline_checkpoint, self.__offline_window):
                        self.__save_channels()
                        offline_checkpoint = util.get_time()
                        channel_index = 0
            except KeyboardInterrupt:
                time.sleep(Config.KILL)
                self.__running = False
            except RecursionError as e:
                self.__status_value = 1
                self.__status = str(e)
                self.__running = False
            except Exception as e:
                self.__status_value = 1
                self.__status = repr(e)
                print(traceback.format_exc(), file=sys.stderr)
                self.__running = False
        for slot in slots:
            slot.shutdown()
        subprocess_monitor.shutdown()
        self.__save_channels()
        controller.halt()
        controller = None
        return self.__status_value == 0

    def status(self) -> int:
        return self.__status_value

    def status_message(self) -> str:
        if self.__status:
            return self.__status
        return "OK"

    def __health_check(self, q, health: Health):
        if not self.__last_health_check or util.mins_ago(self.__last_health_check, self.__health_interval):
            self.__last_health_check = util.get_time()
            health_bar = [f"Channels: {len(self.__channels)}"]
            health_bar.append("| Free space:")
            drives: dict[str, Health.Drive] = health.disk_health()
            for drive in drives.values():
                if drive.low_space:
                    q.put(
                        (
                            Config.MSG_DISP,
                            StatusBarMessage(
                                important="Warning",
                                message=f"Drive space for {drive.directory}: {drive.free_percent:.1%} left",
                            ),
                        )
                    )
                if not drive.available:
                    q.put(
                        (
                            Config.MSG_DISP,
                            StatusBarMessage(
                                important="Warning", message=f"Drive for {drive.directory}: not available"
                            ),
                        )
                    )
                if drive.inaccesible:
                    q.put(
                        (
                            Config.MSG_DISP,
                            StatusBarMessage(
                                important="Warning", message=f"Drive for {drive.directory}: not inaccesible"
                            ),
                        )
                    )
                if not drive.available or drive.inaccesible:
                    health_bar.append(ANSI.Blink + ANSI.Red + "✕✕✕" + ANSI.DefaultColor + ANSI.ResetBlink)
                elif drive.low_space:
                    health_bar.append(ANSI.Red + f"{drive.free_percent:.0%}" + ANSI.DefaultColor)
                else:
                    health_bar.append(f"{drive.free_percent:.0%}")
            inet: Health.Internet = health.internet()
            if inet.error is not None:
                q.put(
                    (
                        Config.MSG_DISP,
                        StatusBarMessage(
                            important="Notice",
                            message=f"Network likely {'up, but' if inet.link_up else 'down,'} with error: {inet.error}",
                        ),
                    )
                )
            health_bar.append("|")
            health_bar.append(
                f"ᯤ{ANSI.Green + '✓' + ANSI.DefaultColor if inet.link_up else ANSI.Red + '✕' + ANSI.DefaultColor}"
            )
            health_bar.append(f"({inet.local_addr if inet.local_addr else '✕.✕.✕.✕'})")
            ytdlp_version: Health.Version = health.ytdlp_version()
            if ytdlp_version.update_available:
                q.put(
                    (
                        Config.MSG_DISP,
                        StatusBarMessage(
                            important="Notice", message=f"YT-DLP has an upgrade available: {ytdlp_version.latest}"
                        ),
                    )
                )
                health_bar.append(f"| YT-DLP {ytdlp_version.latest} update available")
            q.put(
                (
                    Config.MSG_DISP,
                    HealthBar(bar=health_bar),
                )
            )
