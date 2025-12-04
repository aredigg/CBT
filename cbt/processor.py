from dataclasses import replace
from typing import TYPE_CHECKING, cast

from yt_dlp import YoutubeDL
from yt_dlp.utils import RejectedVideoReached

from . import Config
from .display import CurrentChannel, StatusBarMessage
from .logger import Logger

if TYPE_CHECKING:
    from yt_dlp import _Params


class Processor:
    def __init__(self, slot_index, lock, queue) -> None:
        self.__slot_index = slot_index
        self.__process_lock = lock
        self.__process_lock_status = False
        self.__response_queue = queue
        self.__error_message = None
        Config.load()
        if output_directory := Config.getstr("output_directory"):
            Config.add_paths(home=output_directory + "/home")
        if temporary_storage := Config.getstr("temporary_storage"):
            Config.add_paths(temp=temporary_storage + "/temp")
            log_file = f"{temporary_storage}/logfile.{slot_index:02}"
            self.__logger = Logger(log_file=log_file)
        else:
            self.__logger = Logger()
        self.__minimum_duration: int = (Config.getint("minimum_duration") or 15) * 60
        options = Config.ydl_defaults
        options["logger"] = self.__logger
        options["progress_hooks"] = [self.__common_hook]
        options["postprocessor_hooks"] = [self.__common_hook]
        options = cast("_Params", dict(options))
        self.__processor = YoutubeDL(options)
        self.__current = None

    def extract(self, prefix, channel_name) -> bool:
        if info := self.__processor.extract_info(prefix + channel_name, download=False, process=False):
            self.__current = CurrentChannel(
                *(self.__get_details(info) + self.__get_best_format(info.get("formats") or {}) + self.__get_status({}))
            )
            if minimum_resolution := Config.getint("minimum_resolution"):
                if self.__current.height < minimum_resolution:
                    self.__error_message = f"Resolution {self.__current.width}x{self.__current.height} too low"
                    return False
            return True
        return False

    def get_error(self) -> tuple[bool, tuple[str, str, str]]:
        if self.__logger.waiting(Logger.ERR):
            return True, self.__logger.messages(Logger.ERR)[-1]
        if self.__error_message:
            return True, ("", "", self.__error_message)
        return False, ("", "", "")

    def get_filename(self) -> str:
        return self.__logger.download_filename()

    def get_resolution(self):
        return (
            f"{self.__current.width}x{self.__current.height}"
            if self.__current is not None and self.__current.width is not None and self.__current.height is not None
            else ""
        )

    def get_bitrate(self):
        return f"{self.__current.tbr}" if self.__current is not None and self.__current.tbr is not None else ""

    def download(self) -> tuple[bool, tuple[str, str, str]]:
        result = False
        response_message = ""
        if self.__current is not None:
            try:
                self.__response_queue.put((self.__slot_index, replace(self.__current)))
                result = self.__processor.download([self.__current.original_url])
            except RejectedVideoReached as e:
                response_message = str(e)
            except Exception as e:
                response_message = repr(e)
            finally:
                try:
                    self.__response_queue.put(
                        (
                            Config.MSG_DISP,
                            StatusBarMessage(
                                important="Notice", message=f"Releasing lock (Slot {self.__slot_index + 1})"
                            ),
                        )
                    )
                    self.__process_lock.release()
                    self.__process_lock_status = False
                    self.__response_queue.put(
                        (
                            Config.MSG_DISP,
                            StatusBarMessage(
                                important="Notice", message=f"Released lock (Slot {self.__slot_index + 1})"
                            ),
                        )
                    )
                except AssertionError:
                    pass  # lock not owned
                except ValueError:
                    pass
        return not bool(result), ("", "", response_message)

    def __common_hook(self, data):
        self.__current = CurrentChannel(
            *(
                self.__get_details(data.get("info_dict") or {})
                + self.__get_format(data.get("info_dict") or {})
                + self.__get_status(data)
            )
        )
        self.__response_queue.put((self.__slot_index, replace(self.__current)))
        if self.__current.elapsed and self.__current.downloaded_bytes:
            self.__current.tbr = int((self.__current.downloaded_bytes * 8 / self.__current.elapsed) / 1024)
        if self.__current.processor == "progress" and self.__current.status == "finished":
            if self.__current.elapsed < self.__minimum_duration:
                self.__current.elapsed = int(self.__current.elapsed)
                raise RejectedVideoReached(
                    f"Duration too short, {self.__current.elapsed / 60:02.0f}:{self.__current.elapsed % 60:02.0f}"
                )
            self.__response_queue.put(
                (
                    Config.MSG_DISP,
                    StatusBarMessage(important="Notice", message=f"Acquiring lock (Slot {self.__slot_index + 1})"),
                )
            )
            self.__process_lock_status = True
            self.__process_lock.acquire()
            self.__response_queue.put(
                (
                    Config.MSG_DISP,
                    StatusBarMessage(important="Notice", message=f"Acquired lock (Slot {self.__slot_index + 1})"),
                )
            )

    def __get_status(self, data):
        return (
            data.get("status") or "",
            data.get("postprocessor") or "progress",
            data.get("filename") or "",
            data.get("elapsed") or 0.0,
            data.get("downloaded_bytes") or 0,
            data.get("total_bytes") or 0,
            data.get("speed") or 0.0,
            data.get("_percent") or 0.0,
        )

    def __get_details(self, info):
        return (
            info.get("id") or "",
            info.get("title") or "",
            info.get("thumbnail") or "",
            info.get("is_live") or False,
            info.get("age_limit") or 0,
            info.get("webpage_url") or "",
            info.get("original_url") or "",
            info.get("webpage_url_basename") or "",
            info.get("webpage_url_domain") or "",
            info.get("extractor") or "",
            info.get("extractor_key") or "",
            info.get("playlist") or "",
            info.get("playlist_index") or 0,
            info.get("display_id") or "",
            info.get("fulltitle") or "",
            info.get("release_year") or "",
            info.get("live_status") or "",
            info.get("epoch") or 0,
            info.get("_filename") or "",
            info.get("__real_download") or False,
            info.get("__finaldir") or "",
            info.get("filepath") or "",
            info.get("__files_to_move") or "",
        )

    def __get_best_format(self, formats, extension="mp4"):
        best_height = 0
        best_tbr = 0
        selected_format = None
        for format in formats:
            if (format.get("ext") or "") == extension:
                height = format.get("height") or 0
                tbr = format.get("tbr") or 0
                if height > best_height and tbr >= best_tbr:
                    best_height = height
                    best_tbr = tbr
                    selected_format = format
        return self.__get_format(selected_format)

    def __get_format(self, format):
        return (
            format.get("width") or 0,
            format.get("height") or 0,
            format.get("fps") or 0,
            format.get("asr") or 0,
            format.get("audio_channels") or 0,
            format.get("dynamic_range") or "SDR",
            (format.get("vcodec") or "----")[:4],
            (format.get("acodec") or "----")[:4],
            format.get("ext") or "---",
            format.get("format_id") or "",
            format.get("protocol") or "",
            format.get("tbr") or format.get("vbr") or 0,
        )
