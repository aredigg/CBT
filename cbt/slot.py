import os
import queue
import signal
import subprocess
import time
from dataclasses import dataclass, replace
from datetime import datetime
from multiprocessing import Process, Queue
from threading import Lock, Thread

from . import Config, util
from .display import StatusBarMessage
from .processor import Processor


@dataclass
class CurrentSlot:
    index: int
    channel_name: str
    channel_rank: int
    previous_download: datetime | None
    is_downloading: bool
    is_active: bool
    is_complete: bool
    has_error: bool | None
    status_message: tuple[str, str, str]


@dataclass
class CapturedProcess:
    slot_index: int
    pid: int
    children: list[int] | None
    time: datetime


class SubprocessMonitor:
    def __init__(self, response_queue: Queue) -> None:
        self.__response_queue = response_queue
        self.__subprocesses: dict[int, CapturedProcess] = {}
        self.__shutdown = False
        self.__hung_process_timeout = Config.getint("subprocess_timeout") or 36000
        self.__monitor_thread = Thread(target=self.__monitor_loop, daemon=True)
        self.__monitor_thread.start()

    def __monitor_loop(self):
        while not self.__shutdown:
            try:
                receiver, response = self.__response_queue.get(timeout=Config.POLL)
                if receiver == Config.MSG_SLOT:
                    if isinstance(response, CapturedProcess):
                        self.__subprocesses[response.slot_index] = response
                        self.__response_queue.put(
                            (
                                Config.MSG_DISP,
                                StatusBarMessage(
                                    important="Notice",
                                    message=f"Captured Process PID {response.pid} (Slot {response.slot_index + 1})",
                                ),
                            )
                        )
                    elif isinstance(response, int):
                        self.__kill_process(response)
                else:
                    self.__response_queue.put((receiver, response))
            except queue.Empty:
                pass
            self.__scan_for_children()
            self.__process_dead_processes()
            time.sleep(Config.POLL)

    def __scan_for_children(self):
        for slot_index, sub in self.__subprocesses.items():
            if sub.pid and sub.children is None:
                child_pids = (
                    subprocess.run(["pgrep", "-P", str(sub.pid)], capture_output=True, text=True)
                    .stdout.strip()
                    .split("\n")
                )
                # We could check for ffmpeg, but actually all children should be killed
                children = [int(c) for c in child_pids if c.isnumeric()]
                if children:
                    self.__subprocesses[slot_index] = CapturedProcess(
                        slot_index=slot_index, pid=sub.pid, children=children, time=util.get_time()
                    )
                    self.__response_queue.put((slot_index, Config.REC))
                    self.__response_queue.put(
                        (
                            Config.MSG_DISP,
                            StatusBarMessage(
                                important="Notice",
                                message=f"Captured Child Process PIDs {','.join(child_pids)} (Slot {slot_index + 1})",
                            ),
                        )
                    )

    def __process_dead_processes(self):
        dead_processes = []
        for slot_index, sub in self.__subprocesses.items():
            for pid in sub.children or []:
                try:
                    os.kill(pid, 0)
                    elapsed_seconds = (util.get_time() - sub.time).total_seconds()
                    if elapsed_seconds > self.__hung_process_timeout:
                        os.kill(pid, signal.SIGTERM)
                        time.sleep(Config.KILL)
                        os.kill(pid, 0)
                        os.kill(pid, signal.SIGKILL)
                        dead_processes.append(slot_index)
                except OSError:
                    dead_processes.append(slot_index)
        for slot_index in dead_processes:
            if slot_index in self.__subprocesses:
                self.__subprocesses[slot_index].children = None
                self.__response_queue.put((slot_index, None))

    def __kill_process(self, slot_index: int):
        if slot_index in self.__subprocesses:
            sub = self.__subprocesses[slot_index]
            for pid in sub.children or []:
                try:
                    os.kill(pid, signal.SIGTERM)
                    time.sleep(Config.KILL)
                    os.kill(pid, 0)
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
                    self.__subprocesses[slot_index].children = None

    def shutdown(self):
        self.__shutdown = True


class Slot:
    __slot_index = 0

    def __init__(self, queue, lock, prefix) -> None:
        self.__index: int = Slot.__slot_index
        Slot.__slot_index += 1
        self.__busy: bool = False
        self.__shutdown: bool = False
        self.__active_channel = None
        self.__channel_queue: Queue = Queue()
        self.__response_queue: Queue = queue
        self.__postprocess_lock: Lock = lock
        self.__prefix = prefix

        self.__process = Process(
            target=worker_process,
            args=(self.__index, self.__prefix, self.__channel_queue, self.__response_queue, self.__postprocess_lock),
            daemon=True,
        )
        self.__process.start()
        self.__callback = Thread(target=self.__worker_callback, daemon=True)
        self.__callback.start()
        pid = self.__process.pid or 0
        self.__response_queue.put(
            (Config.MSG_SLOT, CapturedProcess(slot_index=self.__index, pid=pid, children=None, time=util.get_time()))
        )

    def __worker_callback(self):
        while not self.__shutdown:
            try:
                slot_index, channel_status = self.__response_queue.get(timeout=Config.POLL)
                if slot_index == self.__index and channel_status is None:
                    if self.__busy:
                        self.__busy = False
                        if self.__active_channel is not None:
                            self.__active_channel = None
                else:
                    self.__response_queue.put((slot_index, channel_status))
                time.sleep(Config.POLL)
            except queue.Empty:
                pass

    def busy(self) -> bool:
        return self.__busy

    def index(self) -> int:
        return self.__index

    def get_name(self) -> str:
        if self.__active_channel is not None:
            return self.__active_channel.name
        return ""

    def check_name(self, name) -> bool:
        return name == self.get_name()

    def shutdown(self):
        self.__shutdown = True
        self.__channel_queue.put(None)

    def process(self, channel):
        self.__busy = True
        self.__active_channel = channel
        channel.last_attempt = util.get_time()
        self.__channel_queue.put(channel)


def worker_process(slot_index: int, prefix: str, chn_queue: Queue, res_queue: Queue, lock: Lock):
    active = True
    processor = Processor(slot_index, lock, res_queue)
    while active:
        try:
            channel = chn_queue.get()
            if channel is None:
                active = False
                break
            slot_status = CurrentSlot(
                index=slot_index,
                channel_name=channel.name,
                channel_rank=channel.rank if isinstance(channel.rank, int) else 5,
                previous_download=channel.last_complete if isinstance(channel.last_complete, datetime) else None,
                is_downloading=True,
                is_active=active,
                is_complete=False,
                has_error=None,
                status_message=("", "", ""),
            )
            channel.last_attempt = util.get_time()
            if processor.extract(prefix, channel.name):
                channel.last_download = util.get_time()
                channel.last_resolution = processor.get_resolution()
                channel.last_bitrate = processor.get_bitrate()
                res_queue.put((slot_index, replace(slot_status)))
                slot_status.has_error, slot_status.status_message = processor.download()
                slot_status.is_complete = not slot_status.has_error
            else:
                slot_status.has_error, slot_status.status_message = processor.get_error()
                slot_status.is_complete = False
                slot_status.is_downloading = False
            if slot_status.is_complete:
                channel.last_complete = util.get_time()
                channel.last_resolution = processor.get_resolution()
                channel.last_bitrate = processor.get_bitrate()
            else:
                channel.last_error = util.get_time()
                channel.last_error_message = slot_status.status_message[2]
            res_queue.put((Config.MSG_CHAN, replace(channel)))
            res_queue.put((slot_index, replace(slot_status)))
            res_queue.put((slot_index, None))
            time.sleep(Config.POLL)
        except KeyboardInterrupt:
            active = False
            time.sleep(Config.POLL)
