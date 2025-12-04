import re
import unicodedata


class ANSI:
    SetupClearScreen = "\033[?1049h\033[?25l\033[0m\033[2J"
    ReturnScreen = "\033[?25h\033[?1049l"

    DefaultColor = "\033[39m"
    BGDefaultColor = "\033[49m"

    Blink = "\033[5m"
    ResetBlink = "\033[25m"

    Bold = "\033[1m"
    Dim = "\033[2m"
    ResetBold = ResetDim = "\033[22m"

    Left = "LEFT"
    Right = "RIGHT"
    Centre = "CENTRE"

    @staticmethod
    def color(hex: str) -> str:
        hex = hex.removeprefix("#")
        code = ""
        if len(hex) == 6:
            try:
                r = int(hex[0:2], 16)
                g = int(hex[2:4], 16)
                b = int(hex[4:6], 16)
                code = f"\033[38;2;{r};{g};{b}m"
            except ValueError:
                pass
        return code

    @staticmethod
    def bg_color(hex: str) -> str:
        hex = hex.removeprefix("#")
        code = ""
        if len(hex) == 6:
            try:
                r = int(hex[0:2], 16)
                g = int(hex[2:4], 16)
                b = int(hex[4:6], 16)
                code = f"\033[48;2;{r};{g};{b}m"
            except ValueError:
                pass
        return code

    @staticmethod
    def gray(pct: int, bg: bool = False) -> str:
        val = 0
        if 0 <= pct <= 100:
            val = int(pct * 24 / 100)
        if bg:
            return f"\033[48;5;{232 + val}m"
        return f"\033[38;5;{232 + val}m"

    Red = "\033[31m"
    Yellow = "\033[33m"
    BrBlack = "\033[90m"
    Gold = color("D4AF37")
    Silver = color("8C8C96")
    Bronze = color("665D1E")
    Pink = color("F09CBB")
    Blue = color("89CFF0")
    Crimson = color("DC143C")
    Green = color("7CFC00")
    SignalYellow = color("FFFF00")

    @staticmethod
    def pos(x: int = 1, y: int = 1) -> str:
        return f"\033[{y};{x}H"

    @staticmethod
    def remove_ansi(string):
        code = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
        return code.sub("", string)

    @staticmethod
    def no_len(char: str) -> bool:
        if (
            unicodedata.category(char) in ("Mn", "Me", "Cf")
            and ord(char) not in (0x200D,)
            or ord(char) in (0x200B, 0x200C, 0x200D, 0xFEFF)
        ):
            return True
        return False

    @staticmethod
    def long(char: str) -> bool:
        if (
            0x1F300 <= ord(char) <= 0x1F9FF
            or 0x2600 <= ord(char) <= 0x26FF
            or 0x1F000 <= ord(char) <= 0x1F02F
            or 0x1F0A0 <= ord(char) <= 0x1F0FF
            or 0x1FA00 <= ord(char) <= 0x1FAFF
            or unicodedata.east_asian_width(char) in ("F", "W")
            or ord(char)
            in [
                0x2700,
                0x2705,
                0x270A,
                0x270B,
                0x2728,
                0x274C,
                0x274E,
                0x2753,
                0x2754,
                0x2755,
                0x2757,
                0x275F,
                0x2760,
                0x2795,
                0x2796,
                0x2797,
                0x27B0,
                0x27BF,
            ]
        ):
            return True
        return False

    @staticmethod
    def shrt(char: str) -> bool:
        return not (ANSI.no_len(char) or ANSI.long(char))

    @staticmethod
    def ulen(text: str):
        length = 0
        for c in text:
            if ANSI.long(c):
                length += 2
            elif ANSI.shrt(c):
                length += 1
        return length

    @staticmethod
    def len(text: str) -> int:
        if len(text) < 1:
            return 0
        return ANSI.ulen(ANSI.remove_ansi(text))

    @staticmethod
    def trim(text: str, length: int, pad=False, align=Left) -> str:
        if not text:
            return "" if not pad else " " * length
        code = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
        i = 0
        ansi_codes = []
        while i < len(text):
            match = code.match(text, i)
            if match:
                ansi_codes.append((i, match.group()))
                i = match.end()
            else:
                i += 1
        text = code.sub("", text)
        while ANSI.len(text) > length:
            text = text[:-1]
        if pad:
            text = ANSI.pad(text, length, align)
        for i, ansi_code in ansi_codes:
            if i < len(text):
                text = text[:i] + ansi_code + text[i:]
            else:
                text += ansi_code
        return text

    @staticmethod
    def pad(text: str, length: int, align, padder=" "):
        padder = padder[0] if len(padder) > 0 else " "
        if align == ANSI.Left:
            while ANSI.len(text) < length:
                text += padder * (length - ANSI.len(text))
        elif align == ANSI.Right:
            while ANSI.len(text) < length:
                text = padder * (length - ANSI.len(text)) + text
        elif align == ANSI.Centre:
            while ANSI.len(text) < length:
                if ANSI.len(text) % 2 == 0:
                    text = padder + text
                else:
                    text = text + padder
        return text
