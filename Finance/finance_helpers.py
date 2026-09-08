import json
import os
import re
import tempfile
import uuid
from datetime import date

# ================= BASE =================
# ================= 基础配置 =================

# This module is fully standalone: everything it reads/writes lives inside
# this Finance/ folder, so it has no dependency on any parent project.

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

# ================= CATEGORY MAP =================
# ================= 分类清单 =================

# 这份清单现在只是"首次使用时的默认分类" —— 真正生效的分类存在
# data/categories.json 里，用户可以自行增删改（见下方 CATEGORY STORE）。
# This map is now only the *seed* used the first time categories.json is
# created. The live, user-editable categories live in data/categories.json
# (see the CATEGORY STORE section further down). Keep this in sync loosely;
# it is the safety fallback whenever the store is empty.

CATEGORY_MAP = {
    "income": [
        "Salary",
        "Freelance",
        "Business",
        "Gift",
        "Bonus"
    ],
    "expense": [
        "Food",
        "Transport",
        "Travel",
        "Entertainment",
        "Rent",
        "Bills",
        "Education",
        "Other"
    ],
    "saving": [
        "Savings",
        "Investment",
        "Emergency Fund"
    ]
}

# ================= JSON DATA STORE =================
# ================= JSON 数据存储 =================


def load_data(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return default


def save_data(path, data):
    # 先把内容写到同一个文件夹里的临时文件，再用 os.replace() 原子性地
    # 换成正式文件名 —— 这样即使程序中途崩溃或被两个请求同时写入，
    # 也不会留下一个写了一半、损坏掉的 JSON 文件。
    # Writes to a temp file in the same folder first, then atomically
    # swaps it into place with os.replace() — this way a crash mid-write,
    # or two requests writing at the same time, can never leave behind a
    # half-written, corrupted JSON file.
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=folder, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


# ================= RAW FILE STORE (receipts) =================
# ================= 原始文件存储（收据图片） =================


def save_file(path, content_bytes):
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=folder, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content_bytes)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def load_file(path):
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return f.read()


def delete_file(path):
    if path and os.path.exists(path):
        os.remove(path)


# ================= IDS / DATES =================
# ================= 通用工具 =================


def new_id():
    """A short, stable, collision-safe id for store records.

    Used for categories and shopping-list items so that cloud sync / a
    future database migration has something to key on that isn't a list
    position (unlike expenses.json, which is still positional).
    """
    return uuid.uuid4().hex


def today_iso():
    return date.today().isoformat()


# ================= TRANSACTION KINDS =================
# ================= 交易类型 =================

# 交易"类型"（收入/支出/储蓄）保持写死，因为报表计算依赖它们：
# income 加、expense 减、saving/transfer 是账户之间的内部转移。
# 用户能完全自定义的是"分类"（Food / Gaming / Girlfriend...），
# 每个分类归属于其中一种类型。
# Transaction *kinds* stay fixed because the reporting math depends on the
# exact strings (income adds, expense subtracts, saving/transfer are
# internal moves). What users fully customise is *categories*
# (Food / Gaming / Girlfriend / ...), each of which belongs to one kind.
KINDS = ("income", "expense", "saving")

# 应用内部记账用的分类名，用户不能新建或与之冲突。
# Category names the app uses for its own internal bookkeeping rows
# (transfers, goal contributions). Users may not create or collide with these.
RESERVED_CATEGORY_NAMES = {"Transfer In", "Transfer Out", "Goal Savings"}

_HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def clean_color(value):
    """Return a safe CSS hex colour (``#rgb`` / ``#rrggbb``) or ``''``."""
    value = (value or "").strip()
    return value if _HEX_COLOR_RE.match(value) else ""


def clean_icon(value):
    """Keep an icon field to a couple of characters (an emoji or short tag)."""
    return (value or "").strip()[:8]


# ================= CATEGORY STORE =================
# ================= 分类存储 =================

f_categories = os.path.join(DATA_DIR, "categories.json")

CATEGORIES_SCHEMA_VERSION = 1

# Every category record written to categories.json has these keys. Older /
# hand-edited files are upgraded in memory by load_categories().
_CATEGORY_DEFAULTS = {
    "icon": "",
    "color": "",
    "archived": False,
    "is_default": False,
}


def _seed_categories():
    cats = []
    for kind, names in CATEGORY_MAP.items():
        for order, name in enumerate(names):
            cats.append({
                "id": new_id(),
                "name": name,
                "kind": kind,
                "icon": "",
                "color": "",
                "order": order,
                "archived": False,
                "is_default": False,
                "created_at": today_iso(),
            })
    return cats


def load_categories():
    """Return the list of category records, seeding + migrating on the fly.

    Stored shape: ``{"schema_version": N, "categories": [ {...}, ... ]}``.
    A bare list (older shape) is tolerated. Missing per-record keys are
    back-filled and persisted so the rest of the code can assume they exist.
    """
    raw = load_data(f_categories, None)

    if raw is None:
        cats = _seed_categories()
        save_categories(cats)
        return cats

    cats = raw if isinstance(raw, list) else raw.get("categories", [])

    changed = False
    for index, cat in enumerate(cats):
        if not cat.get("id"):
            cat["id"] = new_id()
            changed = True
        if not cat.get("kind"):
            cat["kind"] = "expense"
            changed = True
        if "order" not in cat:
            cat["order"] = index
            changed = True
        if "created_at" not in cat:
            cat["created_at"] = today_iso()
            changed = True
        for key, default in _CATEGORY_DEFAULTS.items():
            if key not in cat:
                cat[key] = default
                changed = True

    if changed:
        save_categories(cats)
    return cats


def save_categories(cats):
    save_data(f_categories, {
        "schema_version": CATEGORIES_SCHEMA_VERSION,
        "categories": cats,
    })


def find_category(cats, cat_id):
    return next((c for c in cats if c.get("id") == cat_id), None)


def active_categories(kind=None):
    """Non-archived categories, ordered, optionally filtered to one kind."""
    cats = [c for c in load_categories() if not c.get("archived")]
    if kind is not None:
        cats = [c for c in cats if c.get("kind") == kind]
    return sorted(cats, key=lambda c: (c.get("order", 0), c.get("name", "").lower()))


def categories_by_kind():
    """``{kind: [name, ...]}`` of active categories — the shape the add/edit
    record forms expect for their category dropdowns."""
    out = {kind: [] for kind in KINDS}
    for cat in active_categories():
        out.setdefault(cat.get("kind"), []).append(cat.get("name"))
    return out


def default_categories():
    """``{kind: name}`` for kinds that have a default category set."""
    out = {}
    for cat in load_categories():
        if cat.get("is_default") and not cat.get("archived"):
            out[cat.get("kind")] = cat.get("name")
    return out


def expense_category_names():
    """Active expense category names, falling back to the seed list so
    callers that must never get an empty list (e.g. the receipt AI schema)
    stay safe."""
    names = [c["name"] for c in active_categories("expense")]
    return names or list(CATEGORY_MAP["expense"])


# ================= SHOPPING LIST STORE =================
# ================= 购物清单存储 =================

f_shopping = os.path.join(DATA_DIR, "shopping.json")

SHOPPING_SCHEMA_VERSION = 1

SHOPPING_PRIORITIES = ("high", "medium", "low")
# open      —— 还在考虑 / still deciding
# bought    —— 已购买（通常已生成一笔支出）/ purchased (usually created an expense)
# dismissed —— 决定不买 / decided against it
SHOPPING_STATUSES = ("open", "bought", "dismissed")
# 购买决定，与 status 分开：用户可以先决定 "buy" 但还没真正下单。
# The purchase decision, kept separate from status: a user can decide
# "buy" well before actually recording the purchase.
SHOPPING_DECISIONS = ("undecided", "buy", "dont_buy", "wait")

_SHOPPING_DEFAULTS = {
    "estimated_price": 0.0,
    "category": "",
    "priority": "medium",
    "reasons_for": "",
    "reasons_against": "",
    "notes": "",
    "target_date": "",
    "status": "open",
    "decision": "undecided",
    "decision_date": "",
    "expense_created": False,
    "purchased_at": "",
    "actual_price": None,
}


def load_shopping():
    """Return the list of shopping-list items, migrating shape on the fly.

    Stored shape: ``{"schema_version": N, "items": [ {...}, ... ]}``.
    """
    raw = load_data(f_shopping, None)
    if raw is None:
        return []

    items = raw if isinstance(raw, list) else raw.get("items", [])

    changed = False
    for index, item in enumerate(items):
        if not item.get("id"):
            item["id"] = new_id()
            changed = True
        if "date_added" not in item:
            item["date_added"] = today_iso()
            changed = True
        for key, default in _SHOPPING_DEFAULTS.items():
            if key not in item:
                item[key] = default
                changed = True

    if changed:
        save_shopping(items)
    return items


def save_shopping(items):
    save_data(f_shopping, {
        "schema_version": SHOPPING_SCHEMA_VERSION,
        "items": items,
    })


def find_shopping_item(items, item_id):
    return next((it for it in items if it.get("id") == item_id), None)
