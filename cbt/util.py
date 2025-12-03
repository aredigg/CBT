from datetime import datetime, timedelta, timezone

from .ansi import ANSI


def str_time(dt) -> datetime:
    return datetime.strptime(dt, "%Y-%m-%d %H:%M:%SZ") if isinstance(dt, str) else dt


def time_str(dt: datetime | None = None) -> str:
    if dt is None:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    return dt.strftime("%Y-%m-%d %H:%M:%SZ")


def time_datestr(dt: datetime | None = None) -> str:
    if dt is None:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return dt.strftime("%Y-%m-%d")


def get_time(posix=0) -> datetime:
    if posix == 0:
        return datetime.now(timezone.utc)
    return datetime.fromtimestamp(posix, timezone.utc)


def get_difference(dt_a: datetime | None, dt_b: datetime | None = None) -> str:
    if dt_a is None:
        return ANSI.Dim + "00:00:00" + ANSI.ResetDim
    if dt_b is None:
        dt_b = get_time()
    delta = abs(dt_b - dt_a)
    seconds = int(delta.total_seconds())
    return f"{seconds // 3600:02}:{(seconds % 3600) // 60:02}:{seconds % 60:02}"


def same_date(dt_a: datetime, dt_b: datetime | None = None) -> bool:
    dt_b = get_time() if dt_b is None else dt_b
    return dt_a.date() == dt_b.date()


def hours_ago(dt: datetime, hrs) -> bool:
    return dt < get_time() - timedelta(hours=hrs)
