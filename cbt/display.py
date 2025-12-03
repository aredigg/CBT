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

from . import __program_name__, __version__, util
from .ansi import ANSI
from .processor import CurrentChannel, StatusBarMessage
from .slot import CurrentSlot


@dataclass
class SlotColumns:
    slot: str
    previous: str
    status: str
    timer: str
    resolution: str
    bitrate: str
    rank: str
    channel: str


@dataclass
class SlotStatus:
    slot: SlotColumns
    start: datetime | None
    is_downloading: bool


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
        "Resolution",
        "Bitrate",
        " ",
        "Channel",
    )
    slot_columns_len = [4, 10, 1, 8, 10, 7, 1, 20]
    assert len(slot_columns_len) == len(fields(SlotColumns))
    POSTPROCESSES = {"Merger": "merging", "MoveFiles": "moving", "FixupM3u8": "adjusting", "": "Unknown"}

    def __init__(self, queue, debug_queue=None) -> None:
        self.__response_queue = queue
        self.__debug_queue = debug_queue
        self.__slot_columns: dict[int, SlotStatus] = {}
        self.__listening = False
        self.__shutdown_requested = False
        self.__loop_counter = DisplayController.loop_counter_init
        self.__update_loop = Thread(
            target=self.__control_loop,
            daemon=True,
        )
        self.__update_loop.start()

    def shutdown_requested(self) -> bool:
        return self.__shutdown_requested

    def halt(self):
        self.__listening = False
        self.__shutdown_requested = True

    def __control_loop(self):
        with Display(f"{__program_name__} {__version__} (YT-DLP {version('yt_dlp')})") as display:
            display.update_slots_header(DisplayController.slot_headers, DisplayController.slot_columns_len)
            self.__listening = True
            dirty = True
            while self.__listening:
                try:
                    if select.select([sys.stdin], [], [], 0)[0] and self.__check_keypress(display.status_bar_update):
                        break
                    slot_index, response = self.__response_queue.get(timeout=0.1)
                    if response is None:
                        self.__response_queue.put((slot_index, response))
                    elif slot_index < 0:
                        if isinstance(response, StatusBarMessage):
                            display.status_bar_update(important=response.important, message=response.message)
                    else:
                        if slot_index not in self.__slot_columns:
                            self.__slot_columns[slot_index] = SlotStatus(
                                slot=self.__create_channel_slot(slot_index), start=None, is_downloading=False
                            )
                            display.update_slot(slot_index, self.__slot_columns[slot_index].slot)
                        self.__process_response(slot_index, response)
                        dirty = True
                    time.sleep(0.05)
                except KeyboardInterrupt:
                    raise KeyboardInterrupt
                except queue.Empty:
                    time.sleep(0.05)
                if display.check_size_changed() or self.__update_timer() or dirty:
                    display.update()
                    dirty = False
                self.__loop_counter -= 1

    def __create_channel_slot(self, slot_index):
        return SlotColumns(
            slot=f"{slot_index:>3}",
            previous=" ",
            status=StatusIcons.inactive,
            timer=" ",
            resolution=" ",
            bitrate=" ",
            rank=" ",
            channel=" ",
        )

    def __process_response(self, slot_index, response):
        if isinstance(response, CurrentChannel):
            if self.__update_channel_slot(slot_index, response):
                self.__slot_columns[slot_index].is_downloading = False
        if isinstance(response, CurrentSlot):
            dl = self.__update_slot_slot(slot_index, response)
            if response.is_downloading:
                # This is only true when download is initiating
                self.__slot_columns[slot_index].start = util.get_time()
                self.__slot_columns[slot_index].slot.timer = " "
            self.__slot_columns[slot_index].is_downloading = dl

    def __update_slot_slot(self, slot_index, current_slot: CurrentSlot):
        current = self.__slot_columns[slot_index].slot
        current.previous = (
            util.time_datestr(current_slot.previous_download) if current_slot.previous_download is not None else " "
        )
        current.channel = current_slot.channel_name
        current.rank = self.__get_rank(current_slot.channel_rank)
        current.slot = f"{slot_index + 1:>3}"
        current.status = StatusIcons.downloading if current_slot.is_downloading else StatusIcons.inactive
        if current_slot.has_error:
            current.status = StatusIcons.error
            extractor, channel, message = current_slot.status_message
            current.channel = f"{current_slot.channel_name} {ANSI.Yellow}({message}){ANSI.DefaultColor}"
            current.bitrate = " "
            current.resolution = " "
            current.timer = " "
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
                    if self.__slot_columns[slot_index].start is None:
                        self.__slot_columns[slot_index].start = util.get_time()
                    self.__slot_columns[slot_index].slot.timer = util.get_difference(
                        self.__slot_columns[slot_index].start
                    )
                    if dt := self.__slot_columns[slot_index].start:
                        if not util.same_date(dt):
                            self.__slot_columns[slot_index].slot.timer = (
                                ANSI.Dim + self.__slot_columns[slot_index].slot.timer + ANSI.ResetDim
                            )
            return True
        return False

    def __check_keypress(self, message_handler):
        keypress = sys.stdin.read(1)
        if keypress.lower() == "q":
            message_handler("Shutting down...")
            self.__shutdown_requested = True
            self.__listening = False
            return True
        if keypress == "\x03":  # Ctrl+C
            message_handler("Shutting down (KeyboardInterrupt)...")
            self.__listening = False
            return True
        return False


class Display:
    dummy = SlotColumns(" ", " ", " ", " ", " ", " ", " ", " ")

    def __init__(self, header_text) -> None:
        self.__w, self.__h = size()
        self.__header_text = header_text
        self.__header_status = None
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
        tty.setraw(self.__fd)  # disables echo + canonical mode
        print(ANSI.SetupClearScreen, end="")
        self.__active = True
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.__old and self.__fd:
            termios.tcsetattr(self.__fd, termios.TCSADRAIN, self.__old)
        self.__flush_to_screen()
        self.__active = False
        print(ANSI.ReturnScreen, end="")

    def check_size_changed(self) -> bool:
        if (self.__w, self.__h) != size():
            self.__w, self.__h = size()
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
        self.__create_text_line(self.__header_text, Draw.Rounded)
        self.__create_line(Draw.Rounded)
        self.__create_text_line(self.__header_status, Draw.Rounded)
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
        text = ANSI.trim(text, (self.__w - 4), pad=True)
        self.__print_row(divider + f" {text} " + divider)

    def __create_tick_text_line(self, cols: SlotColumns, ticks, draw_class, end=True):
        divider = draw_class.B
        if end:
            divider = draw_class.V
        result = ""
        if ticks is not None:
            if sum(ticks) + (len(ticks) * 3) > (self.__w - 4):
                pass
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
                pass
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
            result = ANSI.pos(y=self.__h) + " ► " + self.__status_bar_time + " " + important + main_message
            result = ANSI.trim(result, self.__w, pad=True)
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
