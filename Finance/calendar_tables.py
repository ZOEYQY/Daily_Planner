"""Relational tables for each profile's Calendar state.

The Calendar client still sends and receives its whole state as one object
(``/calendar/api/calendar-state``). Here that object's record lists become
rows, one per item, using the same row conversion as finance_tables.py:

    categories       -> calendar_categories (+ calendar_category_colors)
    tasks            -> calendar_tasks
    events           -> calendar_events
    specialDays      -> calendar_special_days
    customFieldDefs  -> calendar_field_defs

Column names are snake_case (``dueDate`` -> ``due_date``). Nested per-item
data (repeat rules, exceptions, to-do lists, custom field values) stays in
JSON columns. Everything else in the state (view, tray toggles, sort modes,
...) is kept in ``calendar_states.payload``. ``calendar_state_lists`` marks a
profile whose state is in these tables and which lists it had; without it,
``calendar_states.payload`` is a whole legacy state. A state that wouldn't
read back identically from the tables is kept whole in the payload instead.

Example query::

    SELECT title, date, start_time FROM calendar_events
    WHERE profile_id = '...' AND date BETWEEN '2026-10-01' AND '2026-10-07'
    ORDER BY date, start_time;
"""
from sqlalchemy import Column, String, Table, delete, insert, select

try:
    from . import finance_tables as ft
except ImportError:
    import finance_tables as ft

STR, FLOAT, INT, BOOL, DATE, ANY = ft.STR, ft.FLOAT, ft.INT, ft.BOOL, ft.DATE, ft.ANY

_EXTRA_FIELDS = [
    ("link", STR), ("place", STR), ("people", STR), ("thingsToBring", STR), ("topic", STR),
    ("todoList", ANY), ("targets", ANY), ("recordNotes", STR), ("customFields", ANY),
    ("extraFieldsOverride", ANY),
]

LISTS = {
    "categories": ft.StoreSpec(None, "calendar_categories", [
        ("id", STR), ("name", STR), ("enabledFields", ANY),
    ], children={
        "colors": ("calendar_category_colors", [
            ("id", STR), ("name", STR), ("value", STR), ("enabledFields", ANY),
        ]),
    }),
    "tasks": ft.StoreSpec(None, "calendar_tasks", [
        ("id", STR), ("title", STR), ("done", BOOL), ("categoryId", STR), ("colorId", STR),
        ("dueDate", DATE), ("startTime", STR), ("endTime", STR), ("scheduled", BOOL), ("notes", STR),
        ("rescheduleCount", INT), ("rescheduleHistory", ANY), ("overdueReschedule", BOOL),
        ("repeat", ANY), ("exceptions", ANY), ("doneDates", ANY), *_EXTRA_FIELDS,
    ], indexes=[("ix_calendar_tasks_profile_due", ("profile_id", "due_date"))]),
    "events": ft.StoreSpec(None, "calendar_events", [
        ("id", STR), ("title", STR), ("categoryId", STR), ("colorId", STR), ("date", DATE),
        ("startTime", STR), ("endTime", STR), ("notes", STR), ("repeat", ANY), ("exceptions", ANY),
        *_EXTRA_FIELDS,
    ], indexes=[("ix_calendar_events_profile_date", ("profile_id", "date"))]),
    "specialDays": ft.StoreSpec(None, "calendar_special_days", [
        ("id", STR), ("title", STR), ("date", DATE), ("categoryId", STR), ("colorId", STR),
        ("notes", STR), ("repeat", ANY), ("exceptions", ANY), ("todoList", ANY),
        ("customFields", ANY), ("extraFieldsOverride", ANY),
    ]),
    "customFieldDefs": ft.StoreSpec(None, "calendar_field_defs", [
        ("id", STR), ("key", STR), ("label", STR),
    ]),
}

lists_table = Table(
    "calendar_state_lists", ft.metadata,
    Column("profile_id", String(ft.PROFILE_ID_MAX), primary_key=True),
    Column("lists", ft.JSON_TYPE, nullable=False),
)


def _prepare(profile_id, state):
    """(settings, {list name: (rows, child rows)}) if ``state`` reads back
    identically from the tables, else ``None``."""
    if not isinstance(state, dict) or len(profile_id) > ft.PROFILE_ID_MAX:
        return None
    settings = {key: value for key, value in state.items() if key not in LISTS}
    built = {}
    rebuilt = dict(settings)
    for name, spec in LISTS.items():
        if name not in state:
            continue
        items = state[name]
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            return None
        rows, child_rows = ft.build_rows(spec, profile_id, [(None, item) for item in items])
        built[name] = (rows, child_rows)
        rebuilt[name] = [record for _, record in ft.rows_to_items(spec, rows, child_rows)]
    if rebuilt != state:
        return None
    return settings, built


def load_state(session, row):
    """The full Calendar state for a ``CalendarState`` row (or ``None``)."""
    if row is None:
        return None
    lists = session.scalar(select(lists_table.c.lists).where(lists_table.c.profile_id == row.profile_id))
    if lists is None:
        return row.payload
    state = dict(row.payload)
    for name in lists:
        state[name] = [record for _, record in ft.select_items(session, LISTS[name], row.profile_id)]
    return state


def store_state(session, row, state):
    """Save ``state`` for ``row``'s profile: lists into the tables and the rest
    into ``row.payload``, or all of it into the payload if it doesn't fit."""
    for spec in LISTS.values():
        ft.delete_items(session, spec, row.profile_id)
    session.execute(delete(lists_table).where(lists_table.c.profile_id == row.profile_id))
    prepared = _prepare(row.profile_id, state)
    if prepared is None:
        row.payload = state
        return
    settings, built = prepared
    row.payload = settings
    for name, (rows, child_rows) in built.items():
        ft.insert_rows(session, LISTS[name], rows, child_rows)
    session.execute(insert(lists_table).values(profile_id=row.profile_id, lists=list(built)))

