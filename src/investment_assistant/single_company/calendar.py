"""Exchange sessions, including holidays and early closing sessions."""

from datetime import datetime, timedelta, timezone
from functools import lru_cache


@lru_cache(maxsize=8)
def calendar(year, first_year=None):
    import exchange_calendars as xcals
    start = min(year - 12, first_year or year)
    return xcals.get_calendar("XNYS", start=f"{start}-01-01", end=f"{year + 2}-12-31")


def completed_session(now=None):
    import pandas as pd
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Use a timezone-aware cutoff.")
    cal = calendar(now.year)
    day = cal.date_to_session(pd.Timestamp(now.date()), direction="previous")
    # A short publication buffer also applies on half-days.
    if cal.session_close(day).to_pydatetime() + timedelta(minutes=30) > now:
        day = cal.previous_session(day)
    return day.date().isoformat()


def session_dates(start, end):
    import pandas as pd
    cal = calendar(int(end[:4]), int(start[:4]))
    return [s.date().isoformat() for s in cal.sessions_in_range(pd.Timestamp(start), pd.Timestamp(end))]


def next_session(day):
    import pandas as pd
    cal = calendar(int(day[:4]))
    label = cal.date_to_session(pd.Timestamp(day), direction="previous")
    return cal.next_session(label).date().isoformat()
