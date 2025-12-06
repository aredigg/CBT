import os
import signal
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, replace
from datetime import datetime
from queue import Empty as EmptyQueue
from queue import Queue
from threading import Event, Lock, Thread

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
    sequence: int


@dataclass
class CapturedProcess:
    slot_index: int | None
    pid: int
    match: str | None
    time: datetime
    filename: str | None
    filesize: int


@dataclass
class FileInfo:
    slot_index: int
    filename: str
    filesize: int
    expected: int


class SubprocessMonitor:
    count = 20

    def __init__(self, response_queue: Queue) -> None:
        self.__response_queue = response_queue
        self.__subprocesses: dict[int, CapturedProcess] = {}
        self.__filenames: dict[int, str] = {}
        self.__shutdown = False
        self.__hung_process_timeout = Config.getint("subprocess_timeout") or 36000
        self.__monitor_thread = Thread(target=self.__monitor_loop, daemon=True)
        self.__monitor_thread.start()

    def __monitor_loop(self):
        counter = SubprocessMonitor.count
        while not self.__shutdown:
            try:
                receiver, response = self.__response_queue.get(timeout=Config.POLL)
                if receiver == Config.MSG_SLOT:
                    if isinstance(response, FileInfo):
                        self.__filenames[response.slot_index] = response.filename
                    elif isinstance(response, int):
                        self.__kill_process(response)
                else:
                    self.__response_queue.put((receiver, response))
            except EmptyQueue:
                pass
            if counter < 0:
                self.__process_dead_processes()
                self.__scan_for_children()
                self.__update_filesizes()
                counter = SubprocessMonitor.count
            time.sleep(Config.POLL)
            counter -= 1

    def __update_filesizes(self):
        for pid, proc in self.__subprocesses.items():
            if proc.filename:
                filesize = None
                for filename in [proc.filename, proc.filename + ".part"]:
                    try:
                        if filesize is None:
                            filesize = os.path.getsize(filename)
                    except (FileNotFoundError, OSError):
                        pass
                    if filesize is not None and filesize > proc.filesize:
                        self.__subprocesses[pid] = replace(proc, filesize=filesize)
                        self.__response_queue.put(
                            (
                                Config.MSG_DISP,
                                FileInfo(
                                    slot_index=proc.slot_index if proc.slot_index is not None else -1,
                                    filename=proc.filename,
                                    filesize=filesize or proc.filesize,
                                    expected=0,
                                ),
                            )
                        )

    def __scan_for_children(self):
        result = subprocess.run(["pgrep", "-P", str(os.getpid())], capture_output=True, text=True)
        if result.returncode != 0:
            return
        child_pids = (int(p) for p in result.stdout.strip().split("\n") if p.strip().isdigit())
        child_pids = set(child_pids) - set(self.__subprocesses)
        for pid in child_pids:
            try:
                result = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True)
                if result.returncode == 0:
                    self.__subprocesses[pid] = CapturedProcess(
                        slot_index=None,
                        pid=pid,
                        match=result.stdout.strip(),
                        time=util.get_time(),
                        filename=None,
                        filesize=0,
                    )
                    # self.__response_queue.put(
                    #     (
                    #         Config.MSG_DISP,
                    #         StatusBarMessage(
                    #             important="Notice",
                    #             message=f"Captured Child Process PID {pid}",
                    #         ),
                    #     )
                    # )
            except ValueError:
                pass
        for slot_index, filename in self.__filenames.items():
            for pid, proc in self.__subprocesses.items():
                if proc.slot_index is None and proc.match and filename and filename in proc.match:
                    self.__subprocesses[pid] = CapturedProcess(
                        slot_index=slot_index,
                        pid=pid,
                        match=proc.match,
                        time=proc.time,
                        filename=filename,
                        filesize=proc.filesize,
                    )
                    self.__response_queue.put((slot_index, Config.REC))

    def __process_dead_processes(self):
        dead_processes = []
        for pid, proc in self.__subprocesses.items():
            try:
                os.kill(pid, 0)
                elapsed_seconds = (util.get_time() - proc.time).total_seconds()
                if elapsed_seconds > self.__hung_process_timeout:
                    os.kill(pid, signal.SIGTERM)
                    time.sleep(Config.KILL)
                    os.kill(pid, 0)
                    os.kill(pid, signal.SIGKILL)
                    dead_processes.append(pid)
            except OSError:
                dead_processes.append(pid)
        for pid in dead_processes:
            if pid in self.__subprocesses.items():
                if slot_index := self.__subprocesses[pid].slot_index:
                    self.__response_queue.put((slot_index, Config.UNR))
                    self.__response_queue.put((slot_index, Config.FIN))
                del self.__subprocesses[pid]

    def __kill_process(self, slot_index: int):
        for pid, proc in self.__subprocesses.items():
            if proc.slot_index == slot_index:
                try:
                    os.kill(pid, signal.SIGTERM)
                    time.sleep(Config.KILL)
                    os.kill(pid, 0)
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
                self.__response_queue.put((slot_index, Config.UNR))
                self.__response_queue.put((slot_index, Config.FIN))
                del self.__subprocesses[pid]

    def shutdown(self):
        self.__shutdown = True


class Slot:
    __static_index = 0

    def __init__(self, response_queue, postprocess_lock, prefix) -> None:
        self.__slot_index: int = Slot.__static_index
        Slot.__static_index += 1
        self.__busy: bool = False
        self.__shutdown: bool = False
        self.__waiting_channel = None
        self.__active_channel = None
        self.__sequence = -1
        self.__channel_event: Event = Event()
        self.__response_queue: Queue = response_queue
        self.__postprocess_lock: Lock = postprocess_lock
        self.__prefix = prefix
        self.__processor = Processor(self.__slot_index, self.__postprocess_lock, self.__response_queue)
        self.__worker_thread = Thread(target=self.__worker_loop, daemon=True, name=f"Slot-{self.__slot_index}")
        self.__worker_thread.start()
        self.__monitor_thread = Thread(
            target=self.__worker_monitor, daemon=True, name=f"Slot-monitor-{self.__slot_index}"
        )
        self.__monitor_thread.start()

    def __worker_loop(self):
        while not self.__shutdown:
            if self.__channel_event.wait(timeout=Config.POLL) and self.__waiting_channel is not None:
                channel = self.__waiting_channel
                self.__waiting_channel = None
                self.__channel_event.clear()
                try:
                    slot_status = CurrentSlot(
                        index=self.__slot_index,
                        channel_name=channel.name,
                        channel_rank=channel.rank if isinstance(channel.rank, int) else 5,
                        previous_download=channel.last_complete
                        if isinstance(channel.last_complete, datetime)
                        else None,
                        is_downloading=True,
                        is_active=not self.__shutdown,
                        is_complete=False,
                        has_error=None,
                        status_message=("", "", ""),
                        sequence=self.__sequence,
                    )
                    self.__sequence += 1
                    channel.last_attempt = util.get_time()
                    if self.__processor.extract(self.__prefix, channel.name):
                        channel.last_download = util.get_time()
                        channel.last_resolution = self.__processor.get_resolution()
                        channel.last_bitrate = self.__processor.get_bitrate()
                        self.__response_queue.put((self.__slot_index, replace(slot_status)))
                        slot_status.has_error, slot_status.status_message = self.__processor.download()
                        slot_status.is_complete = not slot_status.has_error
                    else:
                        slot_status.has_error, slot_status.status_message = self.__processor.get_error()
                        slot_status.is_complete = False
                        slot_status.is_downloading = False
                        self.__remove_temp_file()
                    if slot_status.is_complete:
                        channel.last_complete = util.get_time()
                        channel.last_resolution = self.__processor.get_resolution()
                        channel.last_bitrate = self.__processor.get_bitrate()
                    else:
                        channel.last_error = util.get_time()
                        channel.last_error_message = slot_status.status_message[2]
                    self.__response_queue.put((Config.MSG_CHAN, replace(channel)))
                    self.__response_queue.put((self.__slot_index, replace(slot_status)))
                    self.__response_queue.put((self.__slot_index, Config.FIN))
                    self.__response_queue.put(
                        (
                            Config.MSG_DISP,
                            StatusBarMessage(
                                important="Notice", message=f"{channel.name}: {slot_status.status_message[2] or 'OK'}"
                            ),
                        )
                    )
                except KeyboardInterrupt:
                    self.__response_queue.put(
                        (Config.MSG_DISP, StatusBarMessage(important="Notice", message="KeyboardInterrupt"))
                    )
                    self.shutdown()
                except Exception as e:
                    print(traceback.format_exc(), file=sys.stderr)
                    self.__response_queue.put((Config.MSG_DISP, StatusBarMessage(important="Error", message=str(e))))
                finally:
                    self.__busy = False
                    time.sleep(Config.POLL)
            else:
                self.__channel_event.clear()

    def __remove_temp_file(self):
        if filename := self.__processor.get_filename():
            try:
                for fn in [filename, filename + ".part"]:
                    os.remove(fn)
                    self.__processor.set_filename(None)
                    self.__processor.get_logger().info(f"Removed temporary file {fn}")
            except FileNotFoundError as e:
                self.__processor.get_logger().debug(str(e))
            except Exception as e:
                print(traceback.format_exc(), file=sys.stderr)

                self.__processor.get_logger().error(repr(e))

    def __worker_monitor(self):
        while not self.__shutdown:
            filename = None
            if self.__busy:
                filename = self.__processor.get_filename() if self.__processor.get_filename() != filename else None
                self.__response_queue.put((Config.MSG_SLOT, FileInfo(self.__slot_index, filename or "", 0, 0)))
            else:
                filename = None
            time.sleep(1)

    def busy(self) -> bool:
        return self.__busy

    def slot_index(self) -> int:
        return self.__slot_index

    def get_name(self) -> str:
        if self.__active_channel is not None:
            return self.__active_channel.name
        return ""

    def check_name(self, name) -> bool:
        return name == self.get_name()

    def shutdown(self):
        self.__shutdown = True
        self.__channel_event.set()

    def process(self, channel):
        self.__busy = True
        self.__active_channel = channel
        self.__waiting_channel = channel
        channel.last_attempt = util.get_time()
        self.__channel_event.set()
