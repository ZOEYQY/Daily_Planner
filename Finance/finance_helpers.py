import calendar
import json
import os
import re
import shutil
import tempfile
import uuid
from datetime import date, datetime, timedelta

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


# ================= TAGS =================
# ================= 标签 =================
# 标签是记录上的轻量交叉标注（跟"分类"垂直）：一条记录一个分类，但可以
# 有多个标签，例如 #reimbursable #japan-trip。存成字符串列表。
# Tags are a lightweight cross-cutting label on a record (orthogonal to its
# single category): one category per record, but many tags, e.g.
# #reimbursable #japan-trip. Stored as a list of strings.

TAG_MAXLEN = 24
TAGS_MAX = 12


def normalize_tags(raw):
    """Accept a comma-separated string or a list; return a clean, de-duped,
    capped list of tag strings (leading '#' and surrounding space stripped)."""
    if isinstance(raw, str):
        parts = raw.split(",")
    elif isinstance(raw, (list, tuple)):
        parts = raw
    else:
        return []
    out = []
    seen = set()
    for part in parts:
        tag = str(part).strip().lstrip("#").strip()[:TAG_MAXLEN]
        if tag and tag.lower() not in seen:
            out.append(tag)
            seen.add(tag.lower())
        if len(out) >= TAGS_MAX:
            break
    return out


# ================= DATE MATH (recurring transactions) =================
# ================= 日期推进（定期交易） =================


def add_months(d, n):
    """``date`` ``n`` calendar months later, clamping the day to the last day
    of the target month (so 31 Jan + 1 month -> 28/29 Feb)."""
    month_index = d.month - 1 + int(n)
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return d.replace(year=year, month=month, day=day)


def advance_date(iso_str, frequency, interval=1):
    """Next occurrence of an ISO date given a frequency + interval."""
    d = datetime.strptime(iso_str, "%Y-%m-%d").date()
    interval = max(1, int(interval or 1))
    if frequency == "weekly":
        return (d + timedelta(weeks=interval)).isoformat()
    if frequency == "yearly":
        return add_months(d, 12 * interval).isoformat()
    return add_months(d, interval).isoformat()  # monthly (default)


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
    # Last AI purchase-advice result for this item, or None. Shape:
    # {recommendation, reasoning, suggested_wait_days, confidence, generated_at}
    "ai_suggestion": None,
    # Cooling-off period: wait_days set on the item, wait_until computed when a
    # decision (buy / wait) is made. Purchase is blocked until wait_until passes.
    "wait_days": 0,
    "wait_until": "",
    # Hindsight rating once bought: worth_it ∈ yes|no|meh, plus a note.
    "worth_it": "",
    "hindsight_note": "",
    "rated_at": "",
    # Price observations logged before buying:
    # [{id, date, price, source, note}]
    "price_checks": [],
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
                item[key] = list(default) if isinstance(default, list) else default
                changed = True
        for check in item.get("price_checks", []):
            if not check.get("id"):
                check["id"] = new_id()
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


# ================= RECURRING TRANSACTION STORE =================
# ================= 定期交易存储 =================
# 每条"规则"描述一笔按固定频率重复的交易（房租、订阅、每月工资...）。
# 规则本身不是交易记录 —— 到期时才由它"生成"一条真正的 expenses.json
# 记录（带 recurring_id / source=recurring 以便追溯），并把 next_due 往后推。
# Each *rule* describes a transaction that repeats on a fixed cadence (rent,
# subscriptions, monthly salary...). A rule is not itself a transaction: when
# it comes due it *generates* a real expenses.json record (tagged with
# recurring_id / source=recurring) and its next_due is advanced.

f_recurring = os.path.join(DATA_DIR, "recurring.json")

RECURRING_SCHEMA_VERSION = 1
RECURRING_FREQUENCIES = ("weekly", "monthly", "yearly")

_RECURRING_DEFAULTS = {
    "interval": 1,
    "tags": [],
    "end_date": "",
    "last_posted": "",
    "active": True,
    "auto_post": False,
    "item": "",
    "category": "",
    "account": "",
}


def load_recurring():
    """Recurring rules, each guaranteed to have an ``id`` and ``next_due``.

    Stored shape: ``{"schema_version": N, "rules": [ {...}, ... ]}``.
    """
    raw = load_data(f_recurring, None)
    if raw is None:
        return []

    rules = raw if isinstance(raw, list) else raw.get("rules", [])

    changed = False
    for rule in rules:
        if not rule.get("id"):
            rule["id"] = new_id()
            changed = True
        if "created_at" not in rule:
            rule["created_at"] = today_iso()
            changed = True
        if not rule.get("next_due"):
            rule["next_due"] = rule.get("start_date") or today_iso()
            changed = True
        for key, default in _RECURRING_DEFAULTS.items():
            if key not in rule:
                rule[key] = list(default) if isinstance(default, list) else default
                changed = True

    if changed:
        save_recurring(rules)
    return rules


def save_recurring(rules):
    save_data(f_recurring, {
        "schema_version": RECURRING_SCHEMA_VERSION,
        "rules": rules,
    })


def find_recurring(rules, rule_id):
    return next((r for r in rules if r.get("id") == rule_id), None)


# ================= DEBT / LOAN STORE =================
# ================= 借贷存储 =================
# 一条 debt 记录你欠别人的钱（direction="owe"）或别人欠你的钱
# （direction="owed"）。payments 是还款/收款事件列表，未还金额 =
# principal - sum(payments)。这是一个独立台账，默认不会自动动
# expenses.json（可在还款时勾选"同时记一笔交易"）。
# A debt row is money you owe (direction="owe") or money owed to you
# (direction="owed"). `payments` is a list of repayment events; the
# outstanding balance is principal - sum(payments). It's a standalone
# ledger — it does not touch expenses.json unless the user opts in when
# recording a payment.

f_debts = os.path.join(DATA_DIR, "debts.json")

DEBTS_SCHEMA_VERSION = 1
DEBT_DIRECTIONS = ("owe", "owed")

_DEBT_DEFAULTS = {
    "description": "",
    "due_date": "",
    "account": "",
    "status": "open",
    "notes": "",
    "payments": [],
}


def load_debts():
    """Debt/loan rows. Shape: ``{"schema_version": N, "debts": [ {...} ]}``."""
    raw = load_data(f_debts, None)
    if raw is None:
        return []

    debts = raw if isinstance(raw, list) else raw.get("debts", [])

    changed = False
    for debt in debts:
        if not debt.get("id"):
            debt["id"] = new_id()
            changed = True
        if "created_at" not in debt:
            debt["created_at"] = today_iso()
            changed = True
        for key, default in _DEBT_DEFAULTS.items():
            if key not in debt:
                debt[key] = list(default) if isinstance(default, list) else default
                changed = True
        for payment in debt.get("payments", []):
            if not payment.get("id"):
                payment["id"] = new_id()
                changed = True

    if changed:
        save_debts(debts)
    return debts


def save_debts(debts):
    save_data(f_debts, {"schema_version": DEBTS_SCHEMA_VERSION, "debts": debts})


def find_debt(debts, debt_id):
    return next((d for d in debts if d.get("id") == debt_id), None)


def debt_paid(debt):
    return round(sum(float(p.get("amount") or 0) for p in debt.get("payments", [])), 2)


def debt_outstanding(debt):
    return round(float(debt.get("principal") or 0) - debt_paid(debt), 2)


# ================= NET-WORTH SNAPSHOT STORE =================
# ================= 净资产快照存储 =================
# 一张快照 = 某个时间点的 assets / liabilities / net。可以"立即快照"
# （由 App 数据自动算：账户余额 + 别人欠你的 - 你欠别人的），也可以
# 手动输入（用来包含 App 不追踪的房产、车等）。
# A snapshot = assets / liabilities / net at a point in time. Either
# auto-computed from the app's data (account balances + money owed to you -
# money you owe) or entered by hand (to include property, a car, etc.).

f_networth = os.path.join(DATA_DIR, "networth.json")

NETWORTH_SCHEMA_VERSION = 1


def load_networth():
    """Snapshots. Shape: ``{"schema_version": N, "snapshots": [ {...} ]}``."""
    raw = load_data(f_networth, None)
    if raw is None:
        return []

    snapshots = raw if isinstance(raw, list) else raw.get("snapshots", [])

    changed = False
    for snap in snapshots:
        if not snap.get("id"):
            snap["id"] = new_id()
            changed = True
        if "net" not in snap:
            snap["net"] = round(float(snap.get("assets") or 0) - float(snap.get("liabilities") or 0), 2)
            changed = True

    if changed:
        save_networth(snapshots)
    return snapshots


def save_networth(snapshots):
    save_data(f_networth, {"schema_version": NETWORTH_SCHEMA_VERSION, "snapshots": snapshots})


def find_snapshot(snapshots, snap_id):
    return next((s for s in snapshots if s.get("id") == snap_id), None)


# ================= AI INSIGHTS STORE =================
# ================= AI 洞察存储 =================
# 缓存 AI 生成的"月度回顾"，按 "YYYY-MM" 存，避免每次打开 Summary 都
# 重新调用（免费额度也有速率限制）。
# Caches the AI monthly-review text keyed by "YYYY-MM" so opening the
# Summary page doesn't re-call the AI every time (even the free tier is rate-limited).

f_insights = os.path.join(DATA_DIR, "insights.json")

INSIGHTS_SCHEMA_VERSION = 1


def load_insights():
    """``{"YYYY-MM": {narrative, suggestions, generated_at}}``."""
    raw = load_data(f_insights, None)
    if not isinstance(raw, dict):
        return {}
    if "reviews" in raw:
        return raw["reviews"]
    return raw


def save_insights(reviews):
    save_data(f_insights, {"schema_version": INSIGHTS_SCHEMA_VERSION, "reviews": reviews})


# ================= CURRENCY CONVERTER STORE =================
# ================= 货币换算存储 =================
# 一个独立的小工具，跟交易记账完全无关。RM (MYR) 是本位币，其他货币各
# 存一个 rate_to_myr（1 单位该货币 = 多少 RM），由用户自己维护/更新。
# A standalone tool, unrelated to transaction bookkeeping. RM (MYR) is the
# base; every other currency stores a rate_to_myr (1 unit of it = how many
# RM), maintained/updated by the user.

f_rates = os.path.join(DATA_DIR, "rates.json")

RATES_SCHEMA_VERSION = 1
BASE_CURRENCY = "MYR"

# Seed rates are rough placeholders — the user is expected to update them.
_SEED_RATES = [
    ("USD", "US Dollar", 4.70),
    ("SGD", "Singapore Dollar", 3.50),
    ("EUR", "Euro", 5.10),
    ("GBP", "British Pound", 5.95),
    ("JPY", "Japanese Yen", 0.032),
    ("CNY", "Chinese Yuan", 0.66),
    ("AUD", "Australian Dollar", 3.10),
    ("THB", "Thai Baht", 0.14),
    ("IDR", "Indonesian Rupiah", 0.00030),
]


def clean_currency_code(value):
    return re.sub(r"[^A-Za-z]", "", value or "").upper()[:5]


def _seed_rates():
    return [
        {"code": code, "name": name, "rate_to_myr": rate, "updated_at": today_iso()}
        for code, name, rate in _SEED_RATES
    ]


def load_rates():
    """Currency rows. Shape: ``{"schema_version": N, "base": "MYR",
    "rates": [{code, name, rate_to_myr, updated_at}]}``. Seeded on first use."""
    raw = load_data(f_rates, None)
    if raw is None:
        rates = _seed_rates()
        save_rates(rates)
        return rates

    rates = raw if isinstance(raw, list) else raw.get("rates", [])
    changed = False
    for r in rates:
        if "updated_at" not in r:
            r["updated_at"] = today_iso()
            changed = True
        if "name" not in r:
            r["name"] = r.get("code", "")
            changed = True
    if changed:
        save_rates(rates)
    return rates


def save_rates(rates):
    save_data(f_rates, {
        "schema_version": RATES_SCHEMA_VERSION,
        "base": BASE_CURRENCY,
        "rates": rates,
    })


def find_rate(rates, code):
    return next((r for r in rates if r.get("code") == code), None)


# ================= PROFILES (simple multi-user, no password yet) =================
# ================= 用户档案（简单多用户，暂无密码） =================
# 一个 profile 只是一个名字 + id —— 不是账号系统，没有密码，"安全性"
# 以后再加。每个 profile 在 data/profiles/<id>/ 下有自己完整的一套
# Finance 数据（记录、预算、目标、分类、购物清单……），互不可见。
# A profile is just a name + id — not a real account system, no password,
# "security" is meant to be layered on later. Each profile gets its own full
# set of Finance data (records, budgets, goals, categories, shopping list...)
# under data/profiles/<id>/, invisible to every other profile.

PROFILES_ROOT = os.path.join(DATA_DIR, "profiles")
PROFILES_INDEX_FILE = os.path.join(DATA_DIR, "profiles.json")
RECEIPTS_ROOT = os.path.join(BASE_DIR, "static", "receipts")
PROFILES_SCHEMA_VERSION = 1

# Every per-profile JSON store filename. Keep this in sync with the store
# constants below (and with finance_routes._store_paths(), which backup/
# restore uses) — it's what first-run migration looks for at the old flat
# Finance/data/<name>.json locations.
PROFILE_STORE_FILENAMES = (
    "expenses.json", "budget.json", "accounts.json", "goals.json",
    "categories.json", "shopping.json", "recurring.json", "debts.json",
    "networth.json", "insights.json", "rates.json",
)


def load_profiles():
    raw = load_data(PROFILES_INDEX_FILE, None)
    if not isinstance(raw, dict) or not isinstance(raw.get("profiles"), list):
        return []
    return raw["profiles"]


def save_profiles(profiles):
    save_data(PROFILES_INDEX_FILE, {
        "schema_version": PROFILES_SCHEMA_VERSION,
        "profiles": profiles,
    })


def find_profile(profiles, profile_id):
    return next((p for p in profiles if p.get("id") == profile_id), None)


def profile_data_dir(profile_id):
    d = os.path.join(PROFILES_ROOT, profile_id)
    os.makedirs(d, exist_ok=True)
    return d


def profile_receipts_dir(profile_id):
    d = os.path.join(RECEIPTS_ROOT, profile_id)
    os.makedirs(d, exist_ok=True)
    return d


def profile_store_paths(profile_id):
    """``{module-global-name: absolute path}`` for one profile's stores,
    covering both this module's and finance_routes'. See apply_profile_paths."""
    d = profile_data_dir(profile_id)
    return {
        "f_expense": os.path.join(d, "expenses.json"),
        "f_budget": os.path.join(d, "budget.json"),
        "f_accounts": os.path.join(d, "accounts.json"),
        "f_goals": os.path.join(d, "goals.json"),
        "f_categories": os.path.join(d, "categories.json"),
        "f_shopping": os.path.join(d, "shopping.json"),
        "f_recurring": os.path.join(d, "recurring.json"),
        "f_debts": os.path.join(d, "debts.json"),
        "f_networth": os.path.join(d, "networth.json"),
        "f_insights": os.path.join(d, "insights.json"),
        "f_rates": os.path.join(d, "rates.json"),
        "RECEIPTS_DIR": profile_receipts_dir(profile_id),
    }


def apply_profile_paths(profile_id):
    """Point every per-profile store at ``profile_id``'s data by reassigning
    this module's own f_* globals (finance_routes reassigns its own f_expense/
    f_budget/f_accounts/f_goals/RECEIPTS_DIR the same way in its
    before_request hook). Mutating module globals instead of threading a
    profile id through every one of the ~40 routes keeps this a small,
    localized change — safe here because the app has no concurrent-request
    handling (single dev-server process), same as every other global this
    codebase already relies on. Returns the full path dict for the caller."""
    paths = profile_store_paths(profile_id)
    globals().update({
        "f_categories": paths["f_categories"],
        "f_shopping": paths["f_shopping"],
        "f_recurring": paths["f_recurring"],
        "f_debts": paths["f_debts"],
        "f_networth": paths["f_networth"],
        "f_insights": paths["f_insights"],
        "f_rates": paths["f_rates"],
    })
    return paths


def _migrate_legacy_flat_data(target_profile_id):
    """One-time upgrade: if Finance/data/<name>.json files exist flat (the
    pre-profiles layout), move them into the new profile's own folder instead
    of leaving them behind or silently losing them. Also moves any flat
    receipt images. Returns True if anything was moved."""
    moved = False
    target_dir = profile_data_dir(target_profile_id)
    for name in PROFILE_STORE_FILENAMES:
        legacy_path = os.path.join(DATA_DIR, name)
        target_path = os.path.join(target_dir, name)
        if os.path.exists(legacy_path) and not os.path.exists(target_path):
            os.replace(legacy_path, target_path)
            moved = True

    if os.path.isdir(RECEIPTS_ROOT):
        target_receipts = profile_receipts_dir(target_profile_id)
        for fname in os.listdir(RECEIPTS_ROOT):
            src = os.path.join(RECEIPTS_ROOT, fname)
            if os.path.isfile(src):
                dst = os.path.join(target_receipts, fname)
                if not os.path.exists(dst):
                    os.replace(src, dst)
                    moved = True
    return moved


def ensure_default_profile():
    """Called on first use: if no profile exists yet, create one — migrating
    any pre-profiles flat data into it so nothing already recorded is lost."""
    profiles = load_profiles()
    if profiles:
        return profiles
    profile_id = new_id()
    moved = _migrate_legacy_flat_data(profile_id)
    profiles = [{
        "id": profile_id,
        "name": "My Finance" if moved else "Profile 1",
        "created_at": today_iso(),
    }]
    save_profiles(profiles)
    return profiles


def create_profile(name):
    profiles = load_profiles()
    profile_id = new_id()
    profiles.append({
        "id": profile_id,
        "name": (name or "").strip()[:60] or "Unnamed",
        "created_at": today_iso(),
    })
    save_profiles(profiles)
    profile_data_dir(profile_id)  # create its folder eagerly
    return profile_id


def rename_profile(profile_id, name):
    profiles = load_profiles()
    profile = find_profile(profiles, profile_id)
    if profile and (name or "").strip():
        profile["name"] = name.strip()[:60]
        save_profiles(profiles)
    return profile


def delete_profile(profile_id):
    """Removes the profile entry and permanently deletes all of its data."""
    profiles = load_profiles()
    profiles = [p for p in profiles if p.get("id") != profile_id]
    save_profiles(profiles)
    shutil.rmtree(profile_data_dir(profile_id), ignore_errors=True)
    shutil.rmtree(profile_receipts_dir(profile_id), ignore_errors=True)
