import os
import queue
import signal
import time
from dataclasses import dataclass, replace
from datetime import datetime
from multiprocessing import Process, Queue
from threading import Lock, Thread

from . import Config, util
from .processor import CapturedProcess, Processor


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
                receiver, response = self.__response_queue.get(timeout=1.0)
                if receiver == -2:
                    if isinstance(response, CapturedProcess):
                        self.__subprocesses[response.slot_index] = response
                else:
                    self.__response_queue.put((receiver, response))
            except queue.Empty:
                time.sleep(0.5)

            dead_processes = []
            for slot_index, sub in list(self.__subprocesses.items()):
                try:
                    os.kill(sub.pid, 0)
                    elapsed_seconds = (util.get_time() - sub.time).total_seconds()
                    if elapsed_seconds > self.__hung_process_timeout:
                        os.kill(sub.pid, signal.SIGTERM)
                        time.sleep(60)
                        os.kill(sub.pid, signal.SIGKILL)
                        dead_processes.append(slot_index)
                except OSError:
                    dead_processes.append(slot_index)

            for slot_index in dead_processes:
                if slot_index in self.__subprocesses:
                    del self.__subprocesses[slot_index]
                    self.__response_queue.put((slot_index, None))
            time.sleep(1.0)

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

    def __worker_callback(self):
        while not self.__shutdown:
            try:
                slot_index, channel_status = self.__response_queue.get(timeout=0.05)
                if slot_index == self.__index and channel_status is None:
                    if self.__busy:
                        self.__busy = False
                        if self.__active_channel is not None:
                            self.__active_channel = None
                else:
                    self.__response_queue.put((slot_index, channel_status))
                time.sleep(0.05)
            except queue.Empty:
                time.sleep(0.05)

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
            res_queue.put((-3, replace(channel)))
            res_queue.put((slot_index, replace(slot_status)))
            res_queue.put((slot_index, None))
            time.sleep(0.2)
        except KeyboardInterrupt:
            active = False
            time.sleep(0.2)
