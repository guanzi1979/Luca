import calendar
from datetime import date, timedelta
from constants import ACTIVE_SHIFTS


def next_month_year():
    today = date.today()
    if today.month == 12:
        return 1, today.year + 1
    return today.month + 1, today.year


def days_in_month(year, month):
    return calendar.monthrange(year, month)[1]


def weekday_letter(weekday_idx):
    mapping = {
        0: "L",
        1: "M",
        2: "M",
        3: "G",
        4: "V",
        5: "S",
        6: "D",
    }
    return mapping[weekday_idx]


def italian_weekday_name_to_index(name):
    mapping = {
        "Lun": 0,
        "Mar": 1,
        "Mer": 2,
        "Gio": 3,
        "Ven": 4,
        "Sab": 5,
        "Dom": 6,
        "MAI": None,
    }
    return mapping.get(name, None)


def day_header_label(dt):
    return f"{dt.day}-{weekday_letter(dt.weekday())}"


def easter_date(year):
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def italian_holidays(year):
    easter = easter_date(year)
    easter_monday = easter + timedelta(days=1)
    return {
        date(year, 1, 1),
        date(year, 1, 6),
        easter_monday,
        date(year, 4, 25),
        date(year, 5, 1),
        date(year, 6, 2),
        date(year, 8, 15),
        date(year, 11, 1),
        date(year, 12, 8),
        date(year, 12, 25),
        date(year, 12, 26),
    }


def day_info_list(year, month):
    holidays = italian_holidays(year)
    n = days_in_month(year, month)
    out = []
    for d in range(1, n + 1):
        dt = date(year, month, d)
        out.append({
            "date": dt,
            "day": d,
            "weekday": dt.weekday(),
            "weekday_letter": weekday_letter(dt.weekday()),
            "header_label": day_header_label(dt),
            "is_saturday": dt.weekday() == 5,
            "is_sunday": dt.weekday() == 6,
            "is_holiday": dt in holidays,
        })
    return out


def week_ranges_for_month(year, month):
    first_day = date(year, month, 1)
    last_day_num = days_in_month(year, month)
    last_day = date(year, month, last_day_num)

    first_monday = first_day - timedelta(days=first_day.weekday())
    ranges = []

    current_start = first_monday
    while current_start <= last_day:
        current_end = current_start + timedelta(days=6)

        start_idx = max(0, (max(current_start, first_day) - first_day).days)
        end_idx = min(last_day_num - 1, (min(current_end, last_day) - first_day).days)

        if start_idx <= end_idx:
            ranges.append((start_idx, end_idx))

        current_start = current_start + timedelta(days=7)

    return ranges


def parse_names(text, n):
    rows = [r.strip() for r in text.splitlines()]
    rows = [r for r in rows if r]
    if len(rows) < n:
        rows.extend([f"DIP{i+1}" for i in range(len(rows), n)])
    return rows[:n]


def normalize_names_list(names, n):
    names = [str(x).strip() for x in names]
    names = [x for x in names if x]
    if len(names) < n:
        names.extend([f"DIP{i+1}" for i in range(len(names), n)])
    return names[:n]


def count_weekend_work(schedule_row, day_infos):
    weekend = saturday = sunday = 0
    for s, info in zip(schedule_row, day_infos):
        if s in ACTIVE_SHIFTS:
            if info["is_saturday"] or info["is_sunday"]:
                weekend += 1
            if info["is_saturday"]:
                saturday += 1
            if info["is_sunday"]:
                sunday += 1
    return weekend, saturday, sunday


def max_consecutive(schedule_row, allowed_set):
    best = 0
    cur = 0
    for s in schedule_row:
        if s in allowed_set:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def max_consecutive_equals(schedule_row, target):
    best = 0
    cur = 0
    for s in schedule_row:
        if s == target:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def is_active_shift(s):
    return s in ACTIVE_SHIFTS