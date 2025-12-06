import queue
import select
import sys
import termios
import time
import tty
from dataclasses import dataclass, fields
from datetime import datetime
from importlib.metadata import version
from shutil import get_terminal_size as size
from threading import Thread

from . import Config, __program_name__, __version__, util
from .ansi import ANSI
from .debug import Debug


@dataclass
class CurrentChannel:
    id: str
    title: str
    thumbnail: str
    is_live: bool
    age_limit: int
    webpage_url: str
    original_url: str
    webpage_url_basename: str
    webpage_url_domain: str
    extractor: str
    extractor_key: str
    playlist: str
    playlist_index: int
    display_id: str
    fulltitle: str
    release_year: str
    live_status: str
    epoch: int
    _filename: str
    _real_download: bool
    _finaldir: str
    filepath: str
    _files_to_move: str
    width: int
    height: int
    fps: int
    asr: int
    audio_channels: int
    dynamic_range: str
    vcodec: str
    acodec: str
    ext: str
    format_id: str
    protocol: str
    tbr: int
    status: str
    processor: str
    filename: str
    elapsed: float
    downloaded_bytes: int
    total_bytes: int
    speed: float
    _percent: float


@dataclass
class StatusBarMessage:
    important: str
    message: str


@dataclass
class HealthBar:
    bar: list[str]


@dataclass
class SlotColumns:
    slot: str
    previous: str
    status: str
    timer: str
    filesize: str
    resolution: str
    bitrate: str
    rank: str
    channel: str


@dataclass
class SlotStatus:
    slot: SlotColumns
    start: datetime | None
    is_downloading: bool
    sequence: int


class StatusIcons:
    downloading = ANSI.Red + ANSI.Blink + "●" + ANSI.ResetBlink + ANSI.DefaultColor
    inactive = ANSI.Green + "○" + ANSI.DefaultColor
    warning = ANSI.Yellow + "○" + ANSI.DefaultColor
    error = ANSI.Red + "○" + ANSI.DefaultColor
    processing = ANSI.Green + "●" + ANSI.DefaultColor
    gold_rank = ANSI.Gold + "❃" + ANSI.DefaultColor
    silver_rank = ANSI.Silver + "❃" + ANSI.DefaultColor
    bronze_rank = ANSI.Bronze + "❃" + ANSI.DefaultColor
    pink_rank = ANSI.Pink + "✦" + ANSI.DefaultColor
    blue_rank = ANSI.Blue + "✦" + ANSI.DefaultColor
    no_rank = " "


class DisplayController:
    loop_counter_init: int = 5
    slot_headers: SlotColumns = SlotColumns(
        "Slot",
        "Previous",
        " ",
        "Timer",
        "Size (MB)",
        "Resolution",
        "Bitrate",
        " ",
        "Channel",
    )
    slot_columns_len = [4, 10, 1, 8, 9, 10, 7, 1, 20]
    assert len(slot_columns_len) == len(fields(SlotColumns))
    POSTPROCESSES = {"Merger": "Merge", "MoveFiles": "Move", "FixupM3u8": "Normalize", "": "Unknown"}

    def __init__(self, queue) -> None:
        self.__response_queue = queue
        self.__slot_columns: dict[int, SlotStatus] = {}
        self.__listening = False
        self.__shutdown_requested = False
        self.__completion_requested = False
        self.__loop_counter = DisplayController.loop_counter_init
        self.__update_loop = Thread(
            target=self.__control_loop,
            daemon=True,
        )
        self.__update_loop.start()

    def shutdown_requested(self) -> bool:
        return self.__shutdown_requested

    def completion_requested(self) -> bool:
        return self.__completion_requested

    def halt(self):
        self.__listening = False
        self.__shutdown_requested = True
        if self.__update_loop and self.__update_loop.is_alive():
            self.__update_loop.join(timeout=60.0)

    def __control_loop(self):
        with Display(f"{__program_name__} {__version__} (YT-DLP {version('yt_dlp')})") as display:
            display.update_slots_header(DisplayController.slot_headers, DisplayController.slot_columns_len)
            self.__listening = True
            periodic_refresh = util.get_time()
            dirty = True
            while self.__listening:
                try:
                    if select.select([sys.stdin], [], [], 0)[0] and self.__check_keypress(display.status_bar_update):
                        break
                    slot_index, response = self.__response_queue.get(timeout=Config.POLL)
                    if slot_index == Config.MSG_DISP:
                        self.__process_display_response(display, response)
                    elif slot_index >= 0 and response is not None:
                        if slot_index not in self.__slot_columns:
                            self.__slot_columns[slot_index] = SlotStatus(
                                slot=self.__create_channel_slot(slot_index),
                                start=None,
                                is_downloading=False,
                                sequence=-1,
                            )
                            display.update_slot(slot_index, self.__slot_columns[slot_index].slot)
                        self.__process_response(slot_index, response)
                        dirty = True
                    else:
                        self.__response_queue.put((slot_index, response))
                    time.sleep(Config.POLL)
                except KeyboardInterrupt:
                    raise KeyboardInterrupt
                except queue.Empty:
                    pass
                except Exception:
                    Debug.writetb()
                if display.check_size_changed() or self.__update_timer() or dirty:
                    display.update()
                    dirty = False
                self.__loop_counter -= 1
                dirty = dirty or util.mins_ago(periodic_refresh)

    def __create_channel_slot(self, slot_index):
        return SlotColumns(
            slot=f"{slot_index:>3}",
            previous=" ",
            status=StatusIcons.inactive,
            timer=" ",
            filesize=" ",
            resolution=" ",
            bitrate=" ",
            rank=" ",
            channel=" ",
        )

    def __process_display_response(self, display, response):
        from .slot import FileInfo

        if isinstance(response, StatusBarMessage):
            Debug.write(f"{response.important}: {response.message}")
            display.status_bar_update(important=response.important, message=response.message)
        if isinstance(response, HealthBar):
            shutdown_msg = ""
            if self.__completion_requested or self.__shutdown_requested:
                shutdown_msg = f"| {ANSI.Yellow}Shutting down...{ANSI.DefaultColor}"
            display.health_bar_update(bar_text=response.bar + [shutdown_msg])
        if isinstance(response, FileInfo):
            if response.slot_index in self.__slot_columns:
                self.__slot_columns[response.slot_index].slot.filesize = f"{response.filesize >> 20:>8}"

    def __process_response(self, slot_index, response):
        from .slot import CurrentSlot

        if isinstance(response, CurrentChannel):
            if self.__update_channel_slot(slot_index, response):
                self.__slot_columns[slot_index].is_downloading = False
        if isinstance(response, CurrentSlot) and response.sequence >= self.__slot_columns[slot_index].sequence:
            self.__slot_columns[slot_index].sequence = response.sequence
            dl = self.__update_slot_slot(slot_index, response)
            if response.is_downloading:
                # This is only true when download is initiating
                self.__slot_columns[slot_index].start = None
                self.__slot_columns[slot_index].slot.timer = " "
            self.__slot_columns[slot_index].is_downloading = dl
        if response == Config.REC:
            self.__slot_columns[slot_index].start = util.get_time()
        if response == Config.UNR:
            self.__slot_columns[slot_index].is_downloading = False

    def __update_slot_slot(self, slot_index, current_slot):
        current = self.__slot_columns[slot_index].slot
        current.previous = (
            util.time_datestr(current_slot.previous_download) if current_slot.previous_download is not None else " "
        )
        current.channel = current_slot.channel_name
        current.rank = self.__get_rank(current_slot.channel_rank)
        current.slot = f"{slot_index + 1:>3}"
        current.status = StatusIcons.processing if current_slot.is_downloading else StatusIcons.inactive
        if current_slot.has_error:
            current.status = StatusIcons.error
            _, channel, message = current_slot.status_message
            if current_slot.channel_name != channel:
                current.channel = f"{current_slot.channel_name} {ANSI.Yellow}({channel} {message}){ANSI.DefaultColor}"
            else:
                current.channel = f"{current_slot.channel_name} {ANSI.Yellow}({message}){ANSI.DefaultColor}"
            current.bitrate = " "
            current.resolution = " "
            current.timer = " "
            current.filesize = " "
        return current_slot.is_downloading

    def __update_channel_slot(self, slot_index, current_channel: CurrentChannel):
        current = self.__slot_columns[slot_index].slot
        current.slot = f"{slot_index + 1:>3}"
        current.channel = f"{current_channel.id}"
        if current_channel.elapsed and current_channel.downloaded_bytes:
            bitrate = (current_channel.downloaded_bytes * 8 / current_channel.elapsed) / 1024
            current.bitrate = f"{bitrate:>6.0f}"
        else:
            current.bitrate = f"{current_channel.tbr:>6.0f}"
        current.resolution = f"{current_channel.width}✗{current_channel.height}"
        if current_channel.processor != "progress":
            status = f"{DisplayController.POSTPROCESSES[current_channel.processor]} {current_channel.status}"
            current.channel = f"{current_channel.id} {ANSI.Green}({status}){ANSI.DefaultColor}"
            current.status = StatusIcons.inactive if current_channel.status == "finished" else StatusIcons.processing
        return current_channel.status == "finished"

    def __get_rank(self, rank: int) -> str:
        if rank == 0:
            return StatusIcons.gold_rank
        if rank == 1:
            return StatusIcons.silver_rank
        if rank == 2:
            return StatusIcons.bronze_rank
        if rank == 3:
            return StatusIcons.pink_rank
        if rank == 4:
            return StatusIcons.blue_rank
        return StatusIcons.no_rank

    def __update_timer(self):
        if self.__loop_counter <= 0:
            self.__loop_counter = DisplayController.loop_counter_init
            for slot_index in self.__slot_columns:
                if self.__slot_columns[slot_index].is_downloading:
                    current = self.__slot_columns[slot_index].slot
                    current.status = (
                        StatusIcons.processing
                        if self.__slot_columns[slot_index].start is None
                        else StatusIcons.downloading
                    )
                    self.__slot_columns[slot_index].slot.timer = util.get_difference(
                        self.__slot_columns[slot_index].start
                    )
                    if dt := self.__slot_columns[slot_index].start:
                        if not util.same_date(dt):
                            self.__slot_columns[slot_index].slot.timer = (
                                ANSI.Dim + self.__slot_columns[slot_index].slot.timer + ANSI.ResetDim
                            )
                else:
                    self.__slot_columns[slot_index].slot.timer = (
                        ANSI.BrBlack + self.__slot_columns[slot_index].slot.timer + ANSI.DefaultColor
                    )
                    self.__slot_columns[slot_index].slot.status = StatusIcons.error
                if self.__completion_requested:
                    for slot_index in self.__slot_columns:
                        current = self.__slot_columns[slot_index].slot
                        if not self.__slot_columns[slot_index].is_downloading:
                            current.previous = " "
                            current.status = StatusIcons.warning
                            current.timer = " "
                            current.filesize = " "
                            current.resolution = " "
                            current.bitrate = " "
                            current.rank = " "
                            current.channel = " "
            return True
        return False

    def __check_keypress(self, message_handler):
        keypress = sys.stdin.read(1)
        if keypress.lower() == "q":
            message_handler("Notice", "Shutting down...")
            self.__shutdown_requested = True
            self.__listening = False
            return True
        if keypress.lower() == "c":
            message_handler("Notice", "Completing before shutting down...")
            self.__completion_requested = True
        if keypress == "\x03":  # Ctrl+C
            message_handler("Warning", "Shutting down (KeyboardInterrupt)...")
            self.__listening = False
            return True
        if keypress in map(str, range(10)):
            try:
                slot_index = int(keypress) - 1
                if slot_index in self.__slot_columns:
                    self.__response_queue.put((Config.MSG_SLOT, slot_index))
                    message_handler("Notice", f"Halting slot {keypress}...")
                else:
                    message_handler("Warning", f"Halting slot {keypress} not existing...")
            except ValueError:
                message_handler("Warning", f"Halting slot {keypress} not possible...")
        return False


class Display:
    dummy = SlotColumns(" ", " ", " ", " ", " ", " ", " ", " ", " ")

    def __init__(self, header_text) -> None:
        self.__w, self.__h = size()
        self.__header_text = header_text
        self.__header_health_text = None
        self.__slot_header: SlotColumns | None = None
        self.__slot_text: list[SlotColumns] = []
        self.__slot_ticks = []
        self.__status_bar_text = [f"{__program_name__} {__version__}", "Ready..."]
        self.__status_bar_time = util.time_str()
        self.__row = 1
        self.__fd = None
        self.__old = None
        self.__active = False

    def __enter__(self):
        self.__fd = sys.stdin.fileno()
        self.__old = termios.tcgetattr(self.__fd)
        tty.setcbreak(self.__fd)  # disables echo + canonical mode
        print(ANSI.SetupClearScreen, end="")
        self.__active = True
        return self

    def __exit__(self, exc_type, exc, tb):
        self.__active = False
        print(ANSI.ReturnScreen, end="")
        sys.stdout.flush()
        if self.__old and self.__fd:
            termios.tcsetattr(self.__fd, termios.TCSADRAIN, self.__old)
        sys.stdout.flush()

    def check_size_changed(self) -> bool:
        if (self.__w, self.__h) != size():
            self.__w, self.__h = size()
            print(ANSI.ClearScreen)
            return True
        return False

    def update(self):
        if self.__active:
            self.__row = 1
            self.__create_header()
            self.__create_slots()
            self.__create_status_line()
            self.__flush_to_screen()

    def status_bar_update(self, important=None, message=None):
        self.__status_bar_time = util.time_str()
        self.__status_bar_text = []
        if important:
            self.__status_bar_text.append(important)
        if message:
            self.__status_bar_text.append(message)
        self.__create_status_line()
        self.__flush_to_screen()

    def health_bar_update(self, bar_text: list[str]):
        self.__header_health_text = util.time_timestr() + " " + util.time_datestr() + " | " + " ".join(bar_text)

    def __flush_to_screen(self):
        print(ANSI.pos(y=1))

    def __print_row(self, row_string=""):
        if self.__active and self.__row < self.__h:
            row_string = ANSI.trim(row_string, self.__w, pad=True)
            print(ANSI.pos(y=self.__row), end="")
            print(row_string, end="")
            self.__row += 1

    def __create_header(self):
        self.__create_line(Draw.Rounded, top=True)
        self.__create_text_line(self.__header_text, Draw.Rounded, left_align=False)
        self.__create_line(Draw.Rounded)
        self.__create_text_line(self.__header_health_text, Draw.Rounded)
        self.__create_line(Draw.Rounded, top=False)

    def update_slots_header(self, header, ticks):
        self.__slot_header = header
        if sum(ticks) + (len(ticks) * 3) > (self.__w - 4):
            ticks = [1 for _ in ticks]
        ticks[-1] = ticks[-1] + (self.__w - 4) - (sum(ticks) + (len(ticks) * 3))
        self.__slot_ticks = ticks

    def update_slot(self, slot_index: int, columns: SlotColumns):
        while len(self.__slot_text) <= slot_index:
            self.__slot_text.append(Display.dummy)
        self.__slot_text[slot_index] = columns

    def __create_slots(self):
        self.__create_line(Draw.Rounded, top=True, ticks=self.__slot_ticks)
        if self.__slot_header is not None:
            self.__create_tick_text_line(self.__slot_header, self.__slot_ticks, Draw.Rounded)
        for cols in self.__slot_text:
            self.__create_line(Draw.Rounded, top=None, ticks=self.__slot_ticks)
            self.__create_tick_text_line(cols, self.__slot_ticks, Draw.Rounded)
        self.__create_line(Draw.Rounded, top=False, ticks=self.__slot_ticks)

    def __create_text_line(self, text, draw_class, end=True, left_align=True):
        if text is None:
            text = " " * (self.__w - 4)
        divider = draw_class.B
        if end:
            divider = draw_class.V
        text = ANSI.trim(text, (self.__w - 4), pad=True, align=ANSI.Left if left_align else ANSI.Centre)
        self.__print_row(divider + f" {text} " + divider)

    def __create_tick_text_line(self, cols: SlotColumns, ticks, draw_class, end=True):
        divider = draw_class.B
        if end:
            divider = draw_class.V
        result = ""
        if ticks is not None:
            if sum(ticks) + (len(ticks) * 3) > (self.__w - 4):
                short_ticks = [tick - 1 for tick in ticks if tick > 2]
                if sum(short_ticks) < sum(ticks):
                    self.__create_tick_text_line(cols, short_ticks, draw_class, end)
            else:
                for tick, col in zip(ticks, fields(cols)):
                    result += " " + ANSI.trim(getattr(cols, col.name), tick, pad=True) + " " + divider
                result = result[:-1]
                end_len = (self.__w - 2) - ANSI.len(result)
                if end_len > 0:
                    result += end_len * " "
        self.__print_row(divider + result + divider)

    def __create_line(self, draw_class, top=None, end=True, ticks=None):
        result = ""
        end_left = draw_class.LX
        end_right = draw_class.RX
        tick_mark = draw_class.MC
        draw_line = draw_class.H
        if end:
            end_left = draw_class.LM
            end_right = draw_class.RM
        if top is not None:
            if top:
                end_left = draw_class.LT
                end_right = draw_class.RT
                tick_mark = draw_class.MT
            else:
                end_left = draw_class.LB
                end_right = draw_class.RB
                tick_mark = draw_class.MB
        if ticks is not None:
            if sum(ticks) + (len(ticks) * 3) > (self.__w - 4):
                short_ticks = [tick - 1 for tick in ticks if tick > 2]
                if sum(short_ticks) < sum(ticks):
                    self.__create_line(draw_class, top, end, short_ticks)
            else:
                for tick in ticks:
                    result += (tick + 2) * draw_line + tick_mark
                result = result[:-1]
                end_len = (self.__w - 2) - len(result)
                if end_len > 0:
                    result += end_len * draw_line
        else:
            result = (self.__w - 2) * draw_line
        self.__print_row(end_left + result + end_right)

    def __create_status_line(self):
        important = ""
        main_message = ""
        if self.__status_bar_text:
            if len(self.__status_bar_text) == 2:
                important = self.__status_bar_text[0] + " "
                main_message = self.__status_bar_text[1]
            else:
                main_message = self.__status_bar_text[0]
            result = (
                ANSI.pos(y=self.__h)
                + ANSI.gray(pct=75, bg=True)
                + ANSI.gray(pct=0, bg=False)
                + " ► "
                + self.__status_bar_time
                + " "
                + ANSI.gray(pct=95, bg=True)
                + ANSI.Bold
                + " "
                + important
                + ANSI.ResetBold
                + ANSI.gray(pct=75, bg=True)
                + " "
                + main_message
            )
            result = ANSI.trim(result, self.__w, pad=True) + ANSI.BGDefaultColor + ANSI.DefaultColor

            if self.__active:
                print(result, end="")


class Draw:
    class Squared:
        LT = "┌"
        RT = "┐"
        LB = "└"
        RB = "┘"
        V = "│"
        H = "─"
        LM = "├"
        RM = "┤"
        MT = "┬"
        MB = "┴"
        MC = "┼"
        XT = "╵"
        XB = "╷"
        RX = "╴"
        LX = "╶"
        B = " "

    class Rounded(Squared):
        LT = "╭"
        RT = "╮"
        LB = "╰"
        RB = "╯"

    class Doubled(Squared):
        LT = "╒"
        RT = "╕"
        LB = "╘"
        RB = "╛"
        H = "═"
        LM = "╞"
        RM = "╡"
        MT = "╤"
        MB = "╧"
        MC = "╪"

    class Heavy(Squared):
        LT = "┍"
        RT = "┑"
        LB = "┕"
        RB = "┙"
        H = "━"
        LM = "┝"
        RM = "┥"
        MT = "┯"
        MB = "┷"
        MC = "┿"

    class Diagonal:
        L = "╱"
        R = "╲"
        MC = "╳"
