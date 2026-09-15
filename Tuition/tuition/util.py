"""Small shared helpers."""
import json
import datetime as dt

from .db import query

# Pink / rose / plum family, to match the app's pink theme (see --accent in
# tuition.css). All dark enough for white initials to read on them.
AVATAR_COLORS = ["#c2456f", "#a83f6b", "#c85f8e", "#b5487e",
                 "#9c5a86", "#cc5b6a", "#8f4a7a", "#a85585"]


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


def csv_list(value):
    """'English, Math ,' -> ['English', 'Math']."""
    return [x.strip() for x in (value or "").split(",") if x.strip()]


def json_list(value):
    """Parse a JSON-array TEXT column (teachers.subjects/levels/languages,
    prospects.match_subject, ...): None/blank -> []. A plain non-JSON string
    falls back to a single-item list instead of vanishing, so rows saved
    before a column switched from free text to a JSON array still display."""
    if not value:
        return []
    try:
        out = json.loads(value)
        return out if isinstance(out, list) else [str(out)]
    except (ValueError, TypeError):
        return [value] if isinstance(value, str) else []


def known_subjects():
    """Subjects seen anywhere in the app — settings' result subjects, every
    class's subject, every teacher's taught subjects, and every prospect's
    wanted subjects — for datalists on the class / teacher / prospect forms
    and the teacher-match page."""
    seen = []

    def add(name):
        n = (name or "").strip()
        if n and n.lower() not in {s.lower() for s in seen}:
            seen.append(n)

    row = query("SELECT result_subjects FROM settings WHERE id = 1", one=True)
    for s in json_list(row["result_subjects"] if row else "[]"):
        add(s)
    for r in query("SELECT DISTINCT subject FROM classes WHERE subject <> ''"):
        add(r["subject"])
    for r in query("SELECT subjects FROM teachers"):
        for s in json_list(r["subjects"]):
            add(s)
    for r in query("SELECT match_subject FROM prospects"):
        for s in json_list(r["match_subject"]):
            add(s)
    return seen


# Primary (Std 1-6) through secondary (Form 1-5) — the standard Malaysian
# run, always offered on the grade chip picker (user: "list完给我 一年纪 到
# 中学 我要我就按") so there's something to click even on a brand-new,
# still-empty database, not just whatever's already been typed.
GRADE_PRESET = [f"Std {n}" for n in range(1, 7)] + [f"Form {n}" for n in range(1, 6)]


def known_grades():
    """Grade / level names to offer on the grade chip picker — the standard
    Std 1-6 / Form 1-5 preset, plus anything else seen anywhere: students,
    prospects, classes' single `level`, and every teacher's taught `levels`."""
    seen = []

    def add(name):
        n = (name or "").strip()
        if n and n.lower() not in {s.lower() for s in seen}:
            seen.append(n)

    for g in GRADE_PRESET:
        add(g)
    for r in query("SELECT DISTINCT grade FROM students WHERE grade IS NOT NULL AND grade <> ''"):
        add(r["grade"])
    for r in query("SELECT DISTINCT grade FROM prospects WHERE grade IS NOT NULL AND grade <> ''"):
        add(r["grade"])
    for r in query("SELECT DISTINCT level FROM classes WHERE level IS NOT NULL AND level <> ''"):
        add(r["level"])
    for r in query("SELECT levels FROM teachers"):
        for lv in json_list(r["levels"]):
            add(lv)
    return seen


def known_experience():
    """Experience descriptions already used by other teachers (e.g. "1-to-1
    primary English/Math/Science tutoring") — for the experience chip picker
    on the teacher form. No preset here (unlike grades): these are free-form
    phrases, not a fixed vocabulary, so it starts empty and just grows."""
    seen = []

    def add(name):
        n = (name or "").strip()
        if n and n.lower() not in {s.lower() for s in seen}:
            seen.append(n)

    for r in query("SELECT experience_summary FROM teachers"):
        for e in json_list(r["experience_summary"]):
            add(e)
    return seen


def known_academic_results():
    """Academic-results entries already used by other teachers (e.g. "Dean's
    List", "CGPA 3.8") — for the results chip picker on the teacher form. No
    preset (same reasoning as known_experience): free-form, not a fixed
    vocabulary, so it starts empty and just grows."""
    seen = []

    def add(name):
        n = (name or "").strip()
        if n and n.lower() not in {s.lower() for s in seen}:
            seen.append(n)

    for r in query("SELECT academic_results FROM teachers"):
        for res in json_list(r["academic_results"]):
            add(res)
    return seen
