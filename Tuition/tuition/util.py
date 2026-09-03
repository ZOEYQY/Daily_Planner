"""Small shared helpers."""
import datetime as dt

AVATAR_COLORS = ["#2f4b7c", "#665191", "#a05195", "#d45087",
                 "#f95d6a", "#ff7c43", "#3c8d5f", "#1f7a8c"]


def smartcase(value):
    """If the user typed a word/phrase in ALL CAPS, make it Title Case.
    Leaves normal or mixed-case input untouched (so 'SJKC Chong Hwa' stays)."""
    s = (value or "").strip()
    if s and s == s.upper() and s != s.lower():
        return s.title()
    return s


def titlecase_name(value):
    """For a person's name typed quickly: Title Case if it's all-lower or all-upper,
    leave deliberately mixed case alone."""
    s = (value or "").strip()
    if s and (s == s.lower() or s == s.upper()):
        return s.title()
    return s


def derive_full_name(name_en, name_zh):
    return (name_en or "").strip() or (name_zh or "").strip() or "Unnamed"


def pick_avatar_color(seed):
    return AVATAR_COLORS[sum(ord(c) for c in str(seed)) % len(AVATAR_COLORS)]


def initials(name):
    parts = [p for p in str(name).split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def age_from_dob(dob):
    if not dob:
        return None
    try:
        d = dt.date.fromisoformat(dob)
    except ValueError:
        return None
    today = dt.date.today()
    return today.year - d.year - ((today.month, today.day) < (d.month, d.day))


def parse_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
