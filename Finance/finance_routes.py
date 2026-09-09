from flask import Blueprint, render_template, request, redirect, url_for, Response, jsonify
import base64
import calendar
import csv
import io
import json
import math
import mimetypes
import os
import re
import statistics
import uuid
import zipfile
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
try:
    # Package-relative import: used when this module is imported as
    # Finance.finance_routes (e.g. by a parent project's app.py).
    from .finance_helpers import (
        load_data, save_data, save_file, load_file, delete_file,
        BASE_DIR, DATA_DIR,
        KINDS, RESERVED_CATEGORY_NAMES, new_id, today_iso,
        clean_color, clean_icon,
        normalize_tags, advance_date,
        load_categories, save_categories, find_category,
        active_categories, categories_by_kind, default_categories,
        expense_category_names,
        load_shopping, save_shopping, find_shopping_item,
        SHOPPING_PRIORITIES, SHOPPING_STATUSES, SHOPPING_DECISIONS,
        load_recurring, save_recurring, find_recurring, RECURRING_FREQUENCIES,
        load_debts, save_debts, find_debt, debt_paid, debt_outstanding,
        DEBT_DIRECTIONS,
        load_networth, save_networth, find_snapshot,
        load_insights, save_insights,
        load_rates, save_rates, find_rate, clean_currency_code, BASE_CURRENCY,
    )
except ImportError:
    # Plain import: used when running Finance/app.py directly, where
    # Finance/ itself (not its parent) is on sys.path.
    from finance_helpers import (
        load_data, save_data, save_file, load_file, delete_file,
        BASE_DIR, DATA_DIR,
        KINDS, RESERVED_CATEGORY_NAMES, new_id, today_iso,
        clean_color, clean_icon,
        normalize_tags, advance_date,
        load_categories, save_categories, find_category,
        active_categories, categories_by_kind, default_categories,
        expense_category_names,
        load_shopping, save_shopping, find_shopping_item,
        SHOPPING_PRIORITIES, SHOPPING_STATUSES, SHOPPING_DECISIONS,
        load_recurring, save_recurring, find_recurring, RECURRING_FREQUENCIES,
        load_debts, save_debts, find_debt, debt_paid, debt_outstanding,
        DEBT_DIRECTIONS,
        load_networth, save_networth, find_snapshot,
        load_insights, save_insights,
        load_rates, save_rates, find_rate, clean_currency_code, BASE_CURRENCY,
    )

# Load Finance/.env (if present) so OPENAI_API_KEY / OPENAI_RECEIPT_MODEL can
# live in a file instead of the shell. Optional: a missing package or file is
# a no-op, and real environment variables always win over the file.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE_DIR, ".env"))
except ImportError:
    pass

finance_bp = Blueprint('finance', __name__, url_prefix='')

# ================= FILE PATHS =================
# ================= 文件路径 =================

f_expense = os.path.join(DATA_DIR, "expenses.json")
f_budget = os.path.join(DATA_DIR, "budget.json")
f_accounts = os.path.join(DATA_DIR, "accounts.json")
f_goals = os.path.join(DATA_DIR, "goals.json")


def _store_paths():
    """``{filename: absolute path}`` for every JSON store a backup / restore
    round-trips. Read live (not a module constant) so tests that repoint the
    data directory still see the right paths. ``finance_helpers`` owns
    ``categories.json`` + ``shopping.json``; the rest live in this module."""
    try:
        from . import finance_helpers as _fh
    except ImportError:
        import finance_helpers as _fh
    return {
        "expenses.json": f_expense,
        "budget.json": f_budget,
        "accounts.json": f_accounts,
        "goals.json": f_goals,
        "categories.json": _fh.f_categories,
        "shopping.json": _fh.f_shopping,
        "recurring.json": _fh.f_recurring,
        "debts.json": _fh.f_debts,
        "networth.json": _fh.f_networth,
        "insights.json": _fh.f_insights,
        "rates.json": _fh.f_rates,
    }


# ================= TRANSACTION RECORD STORE =================
# ================= 交易记录存储 =================
# expenses.json 是一个记录列表。以前用"在列表里的位置"来标识一条记录
# （编辑/删除靠下标），很脆弱：一旦插入/排序/删除，其他记录的下标就变了。
# 现在每条记录都有一个稳定的 uuid `id`，编辑/删除都按 id 定位。
# 删除是"软删除"：打上 deleted_at 时间戳、从各处统计里排除，可在
# Trash 页面恢复或彻底删除。
# expenses.json is a list of records. They used to be identified by list
# position (edit/delete by index), which is fragile — any insert / sort /
# delete shifts every later index. Now every record carries a stable uuid
# `id` and edit/delete look records up by it. Delete is a *soft* delete: a
# `deleted_at` timestamp is set, the record drops out of every total, and
# it can be restored or purged from the Trash page.

def _backfill_record_ids(records):
    changed = False
    for r in records:
        if not r.get("id"):
            r["id"] = new_id()
            changed = True
    return changed


def load_records(include_deleted=False):
    """Every transaction record, each guaranteed to have an ``id``.
    Soft-deleted records are excluded unless ``include_deleted=True``."""
    records = load_data(f_expense, [])
    if _backfill_record_ids(records):
        save_data(f_expense, records)
    if include_deleted:
        return records
    return [r for r in records if not r.get("deleted_at")]


def save_records(records):
    save_data(f_expense, records)


def _find_record(records, rid):
    return next((r for r in records if r.get("id") == rid), None)


RECEIPTS_DIR = os.path.join(BASE_DIR, "static", "receipts")
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp"}
# AI receipt extraction reads the OpenAI credentials from the environment:
#   OPENAI_API_KEY       - required; without it the "Extract with AI" button
#                          returns a 503 and the form still works manually.
#   OPENAI_RECEIPT_MODEL - optional; defaults to a small vision model.
RECEIPT_ANALYSIS_MAX_BYTES = 5 * 1024 * 1024
RECEIPT_ANALYSIS_TIMEOUT = 30  # seconds, so a hung request can't wedge a worker
RECEIPT_MIME_TYPES = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
}
def _receipt_response_schema(expense_categories):
    # The category enum is built per request from the user's *active* expense
    # categories (expense_category_names() already falls back to the seed list
    # so this is never empty).
    return {
        "type": "object",
        "properties": {
            "merchant": {"type": ["string", "null"]},
            "date": {"type": ["string", "null"]},
            "total": {"type": ["number", "null"]},
            "category": {"type": ["string", "null"], "enum": [*expense_categories, None]},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        },
        "required": ["merchant", "date", "total", "category", "confidence"],
        "additionalProperties": False,
    }


def _receipt_analysis_prompt(expense_categories):
    categories = ", ".join(expense_categories)
    return f"""Extract transaction details from this receipt image.

Use the final amount charged as total, not a subtotal, tax, discount, change,
or a line-item amount. Do not invent a date, total, merchant, or category when
the image is unclear. Dates must be normalized to ISO format; use null when the
format is ambiguous. When selecting a category, use only one of: {categories}."""


def _json_from_model_text(text):
    """Accept plain JSON, and tolerate a Markdown code fence from a model."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.endswith("```"):
            text = text[:-3].strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end < start:
        raise ValueError("No JSON object in the model response")
    data = json.loads(text[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("Receipt result was not an object")
    return data


def _openai_structured(prompt, schema, schema_name, max_output_tokens=400):
    """Shared strict-JSON OpenAI call for the text-only AI features (category
    suggestion, monthly review, afford check). Same key/model/plumbing as the
    receipt + purchase-advisor features. Raises RuntimeError when unconfigured."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("AI features are not configured")

    try:
        from openai import OpenAI, AuthenticationError
    except ImportError as exc:
        raise RuntimeError("The OpenAI package is not installed") from exc

    client = OpenAI(api_key=api_key, timeout=30)
    try:
        response = client.responses.create(
            model=os.environ.get("OPENAI_RECEIPT_MODEL", "gpt-4o-mini"),
            store=False,
            max_output_tokens=max_output_tokens,
            text={"format": {"type": "json_schema", "name": schema_name,
                             "strict": True, "schema": schema}},
            input=[{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
        )
    except AuthenticationError as exc:
        raise RuntimeError("AI features are not configured correctly") from exc
    return _json_from_model_text(response.output_text)


def _normalise_receipt_result(result, expense_categories):
    merchant = result.get("merchant")
    merchant = merchant.strip()[:160] if isinstance(merchant, str) and merchant.strip() else None

    date = result.get("date")
    try:
        date = datetime.strptime(date, "%Y-%m-%d").strftime("%Y-%m-%d") if isinstance(date, str) else None
    except ValueError:
        date = None

    total = result.get("total")
    try:
        total = float(total)
        if not math.isfinite(total) or total < 0:
            total = None
        elif total is not None:
            total = round(total, 2)
    except (TypeError, ValueError):
        total = None

    category = result.get("category")
    category_lookup = {name.casefold(): name for name in expense_categories}
    category = category_lookup.get(category.casefold()) if isinstance(category, str) else None

    confidence = result.get("confidence")
    confidence = confidence if confidence in {"high", "medium", "low"} else "low"

    return {
        "merchant": merchant,
        "date": date,
        "total": total,
        "category": category,
        "confidence": confidence,
    }


def _analyse_receipt(image_bytes, mime_type, expense_categories):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Receipt analysis is not configured")

    try:
        from openai import OpenAI, AuthenticationError
    except ImportError as exc:
        raise RuntimeError("The OpenAI package is not installed") from exc

    data_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
    client = OpenAI(api_key=api_key, timeout=RECEIPT_ANALYSIS_TIMEOUT)
    try:
        response = client.responses.create(
            model=os.environ.get("OPENAI_RECEIPT_MODEL", "gpt-4o-mini"),
            store=False,
            max_output_tokens=300,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "receipt_fields",
                    "strict": True,
                    "schema": _receipt_response_schema(expense_categories),
                },
            },
            input=[{
                "role": "user",
                "content": [
                    {"type": "input_text", "text": _receipt_analysis_prompt(expense_categories)},
                    {"type": "input_image", "image_url": data_url, "detail": "high"},
                ],
            }],
        )
    except AuthenticationError as exc:
        raise RuntimeError("Receipt analysis is not configured correctly") from exc
    return _normalise_receipt_result(_json_from_model_text(response.output_text), expense_categories)

def _allowed_file(filename):
    # 只要文件名里有"."，并且最后一段扩展名（小写化后）
    # 在允许的图片格式集合里，才算合法。
    # Only counts as valid if the filename contains a "." and the part
    # after the last dot (lowercased) is one of the allowed image formats.

    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def _save_receipt(file):
    ext = secure_filename(file.filename).rsplit(".", 1)[1].lower()
    filename = f"{uuid.uuid4().hex}.{ext}"
    save_file(os.path.join(RECEIPTS_DIR, filename), file.read())
    return filename

def _delete_receipt(filename):
    if filename:
        delete_file(os.path.join(RECEIPTS_DIR, filename))

@finance_bp.route("/receipts/<filename>")
def receipt_image(filename):
    data = load_file(os.path.join(RECEIPTS_DIR, secure_filename(filename)))
    if data is None:
        return "", 404
    mimetype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return Response(data, mimetype=mimetype)


@finance_bp.route("/analyze-receipt", methods=["POST"])
def analyze_receipt():
    """Read an image without saving it, then return AI-suggested form fields."""
    receipt_file = request.files.get("receipt")
    if not receipt_file or not receipt_file.filename:
        return jsonify(error="Choose a receipt image first."), 400
    if not _allowed_file(receipt_file.filename):
        return jsonify(error="Use a JPG, PNG, GIF, or WEBP receipt image."), 400

    image_bytes = receipt_file.read(RECEIPT_ANALYSIS_MAX_BYTES + 1)
    if not image_bytes:
        return jsonify(error="The receipt image is empty."), 400
    if len(image_bytes) > RECEIPT_ANALYSIS_MAX_BYTES:
        return jsonify(error="Use a receipt image smaller than 5 MB for AI extraction."), 413

    # _allowed_file() already guaranteed a "." and a known image extension;
    # read it off the raw name (secure_filename can drop a non-ASCII stem
    # entirely and leave nothing to split).
    extension = receipt_file.filename.rsplit(".", 1)[1].lower()
    mime_type = RECEIPT_MIME_TYPES.get(extension)
    if not mime_type:
        return jsonify(error="Use a JPG, PNG, GIF, or WEBP receipt image."), 400

    try:
        fields = _analyse_receipt(image_bytes, mime_type, expense_category_names())
    except RuntimeError as exc:
        return jsonify(error=str(exc)), 503
    except (ValueError, json.JSONDecodeError):
        return jsonify(error="The receipt could not be read. Please enter the details manually."), 422
    except Exception:
        return jsonify(error="Receipt analysis is temporarily unavailable. Please try again or enter the details manually."), 502

    return jsonify(fields=fields)

def _goal_time_data(target_date_str, remaining_amount):
    # 根据目标日期和还差多少钱，算出：还剩几天/几个月、
    # 平均每天/每周/每月要存多少钱才能如期达成目标。
    # 如果没设置目标日期，或者日期格式有问题，就返回一整套 None，
    # 让页面知道"这个目标没有设定期限，不用显示倒计时"。
    # Given a target date and how much money is still needed, works out:
    # days/months remaining, and how much needs to be saved per day/week/
    # month to hit the goal on time. If no target date was set, or the
    # date string is malformed, returns a set of Nones so the page knows
    # "this goal has no deadline, don't show a countdown".

    if not target_date_str:
        return {"days_remaining": None, "months_remaining": None, "required_daily": None, "required_weekly": None, "required_monthly": None, "overdue": False}
    try:
        target_date = datetime.strptime(target_date_str, "%Y-%m-%d")
        days = (target_date - datetime.now()).days

        if days <= 0:
            # 目标日期已经过了 —— 标记为逾期，"还需要存多少/每天"这些
            # 数字已经没有意义了，统一归零。
            # The target date has already passed — mark it overdue; the
            # "how much per day/week/month" figures no longer make sense,
            # so they're all zeroed out.
            return {"days_remaining": 0, "months_remaining": 0, "required_daily": 0, "required_weekly": 0, "required_monthly": 0, "overdue": True}

        weeks = days / 7
        months = days / 30.44  # 用平均每月天数 (365.25/12) 来估算月数
                                # uses the average days-per-month (365.25/12) to estimate months
        return {
            "days_remaining": days,
            "months_remaining": round(months, 1),
            "required_daily": round(remaining_amount / days, 2),
            "required_weekly": round(remaining_amount / weeks, 2),
            "required_monthly": round(remaining_amount / months, 2),
            "overdue": False,
        }
    except Exception:
        # 日期字符串格式不对（比如是空字符串或者乱打的），
        # 安全地返回"没有数据"，而不是让整个页面报错崩溃。
        # The date string is malformed (e.g. empty or garbled) — fail
        # safely by returning "no data" instead of crashing the whole page.
        return {"days_remaining": None, "months_remaining": None, "required_daily": None, "required_weekly": None, "required_monthly": None, "overdue": False}

def _get_period_records(records, period):
    # 根据传进来的 period（"weekly" / "yearly" / 其他默认当"monthly"），
    # 只挑出日期落在对应时间范围内的记录。
    # Depending on the given period ("weekly" / "yearly" / anything else
    # defaults to "monthly"), filters down to just the records whose date
    # falls within that time range.

    now = datetime.now()
    if period == "weekly":
        # now.weekday()：星期一=0 ... 星期日=6。用今天减去这个数字的天数，
        # 就能倒推回"这周星期一"的日期，作为这周的起点。
        # now.weekday(): Monday=0 ... Sunday=6. Subtracting that many days
        # from today rewinds the date back to "this week's Monday", used
        # as the start of the week.
        week_start = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
        week_end = now.strftime("%Y-%m-%d")
        return [r for r in records if week_start <= r.get("date", "") <= week_end]
    elif period == "yearly":
        return [r for r in records if r.get("date", "").startswith(now.strftime("%Y"))]
    else:
        return [r for r in records if r.get("date", "").startswith(now.strftime("%Y-%m"))]


def _plus_days(iso_str, days):
    d = datetime.strptime(iso_str, "%Y-%m-%d").date() + timedelta(days=int(days))
    return d.isoformat()


def _period_windows(period, count, ref=None):
    """The last ``count`` date windows for a budget ``period``, oldest first,
    the current one last. Each item is ``(start_iso, end_iso)``."""
    ref = ref or datetime.now().date()
    windows = []
    if period == "weekly":
        this_start = ref - timedelta(days=ref.weekday())
        for i in range(count - 1, -1, -1):
            start = this_start - timedelta(weeks=i)
            windows.append((start.isoformat(), (start + timedelta(days=6)).isoformat()))
    elif period == "yearly":
        for i in range(count - 1, -1, -1):
            year = ref.year - i
            windows.append((f"{year}-01-01", f"{year}-12-31"))
    else:  # monthly
        for i in range(count - 1, -1, -1):
            month_index = ref.month - 1 - i
            year = ref.year + month_index // 12
            month = month_index % 12 + 1
            last = calendar.monthrange(year, month)[1]
            windows.append((f"{year}-{month:02d}-01", f"{year}-{month:02d}-{last:02d}"))
    return windows


def _spent_in_window(records, category, start, end):
    return round(sum(
        r.get("amount", 0) for r in records
        if r.get("type") == "expense" and r.get("category") == category
        and start <= r.get("date", "") <= end
    ), 2)


def _budget_view(b, records):
    """Enriched budget status, including rollover carry-over when enabled."""
    category = b.get("category")
    period = b.get("period", "monthly")
    limit = round(float(b.get("amount") or 0), 2)

    windows = _period_windows(period, 12)
    current_start, current_end = windows[-1]
    spent = _spent_in_window(records, category, current_start, current_end)

    carryover = 0.0
    if b.get("rollover"):
        # Sum (limit - spent) over each prior period. Positive = money saved
        # that rolls forward; negative = an overspend that eats into this one.
        for start, end in windows[:-1]:
            carryover += limit - _spent_in_window(records, category, start, end)
        carryover = round(carryover, 2)

    effective = round(limit + carryover, 2)
    remaining = round(effective - spent, 2)
    percent = (spent / effective * 100) if effective > 0 else (100 if spent else 0)
    status = ("safe" if percent < 80 else
              "warning" if percent < 100 else
              "full" if percent == 100 else "over")

    return {
        "category": category,
        "period": period,
        "amount": limit,
        "rollover": bool(b.get("rollover")),
        "carryover": carryover,
        "effective_limit": effective,
        "spent": spent,
        "remaining": remaining,
        "percent": percent,
        "display_percent": min(max(percent, 0), 100),
        "status": status,
        "overspent": round(max(0.0, spent - effective), 2),
    }


# ================= ADD =================
# ================= 新增记录 =================

@finance_bp.route("/add", methods=["GET", "POST"])
def add_financial():
    accounts = load_data(f_accounts, [])

    if request.method == "POST":
        form = request.form
        template_data = {
            "accounts": accounts,
            "categories": categories_by_kind(),
            "default_categories": default_categories(),
            "form_data": form,
        }

        account = form.get("account")
        new_account = form.get("new_account")
        purpose = form.get("purpose", "spending")

        if new_account:
            # 用户在下拉框选了"新建账户"并填了名字 —— 如果这个名字
            # 还没有用过，就顺手把新账户存进账户列表里，
            # 不需要用户额外跑到 Accounts 页面单独新建。
            # The user picked "create new account" and typed a name — if
            # this name isn't already used, add it to the accounts list on
            # the spot, so they don't have to separately visit the
            # Accounts page just to create it first.
            account = new_account
            if not any(a["name"] == account for a in accounts):
                accounts.append({
                    "name": account,
                    "purpose": purpose
                })
                save_data(f_accounts, accounts)

        if not account:
            return render_template("add.html", error="Account required", **template_data)

        date_str = form.get("date")
        record_type = form.get("type")
        tags = normalize_tags(form.get("tags"))

        # ----- TRANSFER -----
        if record_type == "transfer":
            amount, error = _parse_money(form.get("amount"), "Amount", allow_zero=False)
            if error:
                return render_template("add.html", error=error, **template_data)

            to_account = form.get("to_account")
            if not to_account:
                return render_template("add.html", error="Transfer account required", **template_data)

            # 转账要记两笔：一笔"转出"支出、一笔"转入"收入，这样两个账户
            # 各自的余额加总仍然正确（复式记账的简化版）。
            # A transfer records two entries — one "transferred out" expense
            # and one "transferred in" income — so each account's own
            # balance stays correct (a simplified double-entry).
            records = load_records()
            records.append({
                "id": new_id(), "date": date_str, "type": "expense",
                "category": "Transfer Out", "account": account,
                "item": f"Transfer to {to_account}", "amount": amount, "tags": tags,
            })
            records.append({
                "id": new_id(), "date": date_str, "type": "income",
                "category": "Transfer In", "account": to_account,
                "item": f"Transfer from {account}", "amount": amount, "tags": tags,
            })
            save_records(records)
            return redirect(url_for("finance.add_financial", added="transfer"))

        # 非转账记录：类型必须是已知的收入/支出/储蓄之一。
        # Non-transfer record: the type must be a known income/expense/saving kind.
        if record_type not in KINDS:
            return render_template("add.html", error="Choose a valid transaction type", **template_data)

        allowed_categories = template_data["categories"].get(record_type, [])

        receipt_file = request.files.get("receipt")
        receipt_filename = None
        if receipt_file and receipt_file.filename and _allowed_file(receipt_file.filename):
            receipt_filename = _save_receipt(receipt_file)

        # ----- SPLIT: one payment across several categories -----
        split_rows = _split_rows_from_form(form)
        if split_rows:
            clean_rows, error = _validate_split_rows(split_rows, record_type, allowed_categories)
            if error:
                return render_template("add.html", error=error, **template_data)

            split_id = new_id()
            label = " + ".join(dict.fromkeys(cat for cat, _, _ in clean_rows))
            parent_item = (form.get("item") or "").strip()
            records = load_records()
            for index, (cat, amt, note) in enumerate(clean_rows):
                records.append({
                    "id": new_id(),
                    "date": date_str,
                    "type": record_type,
                    "category": cat,
                    "account": account,
                    "item": " — ".join(p for p in (parent_item, note) if p) or label,
                    "amount": amt,
                    "receipt": receipt_filename if index == 0 else None,
                    "tags": tags,
                    "split_id": split_id,
                    "split_label": label,
                })
            save_records(records)
            return redirect(url_for("finance.add_financial", added="split"))

        # ----- SINGLE record -----
        category = form.get("category")
        if not category:
            return render_template("add.html", error="Category is required", **template_data)
        if allowed_categories and category not in allowed_categories:
            return render_template(
                "add.html",
                error=f'"{category}" is not one of your {record_type} categories',
                **template_data,
            )

        amount, error = _parse_money(form.get("amount"), "Amount", allow_zero=False)
        if error:
            return render_template("add.html", error=error, **template_data)

        records = load_records()
        records.append({
            "id": new_id(),
            "date": date_str,
            "type": record_type,
            "category": category,
            "account": account,
            "item": form.get("item"),
            "amount": amount,
            "receipt": receipt_filename,
            "tags": tags,
        })
        save_records(records)
        return redirect(url_for("finance.add_financial", added="1"))

    # After a successful save the POST above redirects back here (PRG), so the
    # user stays on the Add form instead of being sent to View. The "added"
    # query flag just tells this GET render to show a confirmation message.
    added = request.args.get("added")
    success = {
        "transfer": "Transfer recorded. Add another below.",
        "split": "Split transaction recorded. Add another below.",
        "1": "Record added. Add another below.",
    }.get(added)

    return render_template(
        "add.html",
        accounts=accounts,
        categories=categories_by_kind(),
        default_categories=default_categories(),
        success=success,
    )


# 一笔"拆分"交易 = 同一笔付款按多个分类记成多条记录，共享一个 split_id。
# 这样所有既有的分类/预算/汇总统计都不用改（它们看到的就是普通记录）。
# A "split" transaction = one payment recorded as several records under
# different categories, sharing a split_id. Every existing category / budget
# / summary aggregation keeps working unchanged — they just see normal rows.

def _split_rows_from_form(form):
    """Pull non-empty split lines from the Add form. Returns
    ``[(category, amount_raw, note), ...]`` (unvalidated)."""
    cats = form.getlist("split_category")
    amounts = form.getlist("split_amount")
    notes = form.getlist("split_item")
    rows = []
    for i in range(max(len(cats), len(amounts))):
        cat = (cats[i] if i < len(cats) else "").strip()
        amount_raw = amounts[i] if i < len(amounts) else ""
        note = (notes[i] if i < len(notes) else "").strip()
        if not cat and not str(amount_raw).strip():
            continue
        rows.append((cat, amount_raw, note))
    return rows


def _validate_split_rows(rows, kind, allowed_categories):
    """Returns ``(clean_rows, error)`` — clean_rows is ``[(cat, amount, note)]``
    with amounts parsed to floats."""
    if len(rows) < 2:
        return None, "A split needs at least two lines."
    clean = []
    for cat, amount_raw, note in rows:
        if not cat:
            return None, "Every split line needs a category."
        if allowed_categories and cat not in allowed_categories:
            return None, f'"{cat}" is not one of your {kind} categories.'
        amount, error = _parse_money(amount_raw, "Split amount", allow_zero=False)
        if error:
            return None, error
        clean.append((cat, amount, note))
    return clean, None


# ================= VIEW =================
# ================= 查看记录 =================

@finance_bp.route("/view")
def view_financial():
    records = load_records()
    accounts = load_data(f_accounts, [])

    args = request.args
    f = {
        "account": (args.get("account") or "").strip(),
        "type": (args.get("type") or "").strip(),
        "category": (args.get("category") or "").strip(),
        "tag": (args.get("tag") or "").strip(),
        "q": (args.get("q") or "").strip(),
        "start": (args.get("start") or "").strip(),
        "end": (args.get("end") or "").strip(),
        "min": (args.get("min") or "").strip(),
        "max": (args.get("max") or "").strip(),
    }

    # Records carry a stable `id`, so the display list can be filtered and
    # sorted freely without tracking positions in the file.
    if f["account"] and f["account"] != "All Accounts":
        records = [r for r in records if r.get("account") == f["account"]]
    if f["type"] in KINDS:
        records = [r for r in records if r.get("type") == f["type"]]
    if f["category"]:
        records = [r for r in records if r.get("category") == f["category"]]
    if f["tag"]:
        needle = f["tag"].lower()
        records = [r for r in records if any(needle == t.lower() for t in (r.get("tags") or []))]
    if f["start"]:
        records = [r for r in records if r.get("date", "") >= f["start"]]
    if f["end"]:
        records = [r for r in records if r.get("date", "") <= f["end"]]

    q = f["q"].lower()
    if q:
        def _matches(r):
            haystack = " ".join([
                str(r.get("item") or ""), str(r.get("category") or ""),
                str(r.get("account") or ""), " ".join(r.get("tags") or []),
            ]).lower()
            return q in haystack
        records = [r for r in records if _matches(r)]

    min_amount, _ = _parse_money(f["min"], "Min") if f["min"] else (None, None)
    max_amount, _ = _parse_money(f["max"], "Max") if f["max"] else (None, None)
    if min_amount is not None:
        records = [r for r in records if r.get("amount", 0) >= min_amount]
    if max_amount is not None:
        records = [r for r in records if r.get("amount", 0) <= max_amount]

    records = sorted(records, key=lambda r: r.get("date", ""), reverse=True)

    totals = {
        "count": len(records),
        "income": round(sum(r.get("amount", 0) for r in records
                            if r.get("type") == "income" and r.get("category") != "Transfer In"), 2),
        "expense": round(sum(r.get("amount", 0) for r in records
                             if r.get("type") == "expense" and r.get("category") != "Transfer Out"), 2),
    }
    totals["net"] = round(totals["income"] - totals["expense"], 2)

    filters_active = any(f.values())
    trash_count = sum(1 for r in load_records(include_deleted=True) if r.get("deleted_at"))

    return render_template(
        "view.html",
        records=records,
        accounts=accounts,
        selected_account=f["account"],
        filters=f,
        filters_active=filters_active,
        totals=totals,
        kinds=KINDS,
        all_categories=[c["name"] for c in active_categories()],
        trash_count=trash_count,
    )

# ================= DELETE =================
# ================= 删除记录 =================

@finance_bp.route("/delete/<rid>", methods=["POST"])
def delete_financial(rid):
    """Soft delete: flag the record, keep it (and its receipt) so it can be
    restored or purged from /trash."""
    records = load_records(include_deleted=True)
    record = _find_record(records, rid)
    if record and not record.get("deleted_at"):
        record["deleted_at"] = datetime.now().isoformat(timespec="seconds")
        save_records(records)

    if request.form.get("source") == "goal":
        return redirect(url_for("finance.plan", tab="goals"))
    return redirect(url_for("finance.view_financial"))

# ================= UPDATE =================
# ================= 修改记录 =================

def _categories_for_record(record):
    """``categories_by_kind()`` with this record's current category force-
    included in its own kind — so editing a record whose category was later
    archived or renamed still shows (and can keep) that value instead of
    silently dropping it."""
    cats = {kind: list(names) for kind, names in categories_by_kind().items()}
    kind = record.get("type")
    name = record.get("category")
    if kind in cats and name and name not in cats[kind]:
        cats[kind].append(name)
    return cats


@finance_bp.route("/update/<rid>", methods=["GET", "POST"])
def update_financial(rid):
    records = load_records()
    record = _find_record(records, rid)

    if record is None:
        return redirect(url_for("finance.view_financial"))

    accounts = load_data(f_accounts, [])

    # source 记录"这次编辑是从哪个页面点进来的"（比如从 Goals 页面
    # 点进来编辑一笔存款记录），保存成功后要跳回原本那个页面，
    # 而不是一律跳回 View 页面。
    # source records "which page this edit was opened from" (e.g. editing
    # a savings contribution from the Goals page) — after saving, it
    # redirects back to that same page instead of always going to View.

    source = request.args.get("source", "")

    if request.method == "POST":
        form = request.form
        source = form.get("source", "")
        # form.get(...) or record[...]：如果这个字段在表单里没填
        # （比如某些字段被禁用），就沿用原本记录里已经存在的值，
        # 而不是把它清空。
        # form.get(...) or record[...]: if a field wasn't submitted in the
        # form (e.g. some fields are disabled), fall back to the value
        # already stored on the record instead of blanking it out.
        date = form.get("date") or record["date"]
        type_ = form.get("type") or record["type"]
        category = form.get("category") or record.get("category", "-")
        item = form.get("item") or record.get("item", "-")
        # tags: only overwrite when the field is present in the form (the goal
        # edit form doesn't render it), otherwise keep what's stored.
        tags = normalize_tags(form.get("tags")) if "tags" in form else record.get("tags", [])

        amount, error = _parse_money(form.get("amount"), "Amount", allow_zero=False)
        if error:
            return render_template("update.html", record=record, accounts=accounts, source=source,
                                   categories=_categories_for_record(record), error=error)

        account = form.get("account")
        new_account = form.get("new_account")

        if new_account:
            account = new_account
            if not any(a["name"] == account for a in accounts):
                accounts.append({
                    "name": account,
                    "purpose": "spending"
                })
                save_data(f_accounts, accounts)

        if not account:
            account = record.get("account", "Default")

        receipt_file = request.files.get("receipt")
        if receipt_file and receipt_file.filename and _allowed_file(receipt_file.filename):
            # 换了新收据图片之前，先把旧的收据文件删掉，
            # 避免磁盘上堆积一堆再也用不到的旧图片。
            # Before saving a newly uploaded receipt image, delete the old
            # one first, so unused old receipt files don't keep piling up
            # on disk.
            _delete_receipt(record.get("receipt"))
            record["receipt"] = _save_receipt(receipt_file)

        record["date"] = date
        record["type"] = type_
        record["category"] = category
        record["item"] = item
        record["account"] = account
        record["amount"] = amount
        record["tags"] = tags

        save_records(records)
        if source == "goal":
            return redirect(url_for("finance.plan", tab="goals"))
        return redirect(url_for("finance.view_financial"))

    return render_template(
        "update.html",
        record=record,
        accounts=accounts,
        source=source,
        categories=_categories_for_record(record),
    )

# ================= PLAN (budget + goals combined) =================
# ================= 计划（预算 + 目标 合并页） =================
# Budget 和 Goals 合并成一个 /plan 页面：顶部是两者的合并概览，
# 下面依次是预算管理和目标管理。/budget 和 /goals 仍保留：GET 直接
# 重定向到 /plan，POST（新建/存款）照常处理后回到 /plan。
# Budget + Goals are one /plan page now: a combined overview on top, then
# the budget UI, then the goals UI. /budget and /goals still exist — GET
# redirects to /plan, POST (create / add-savings) is handled then returns.

def _budget_context():
    budgets = load_data(f_budget, [])
    records = load_records()
    display, warnings = [], []
    for b in budgets:
        view = _budget_view(b, records)
        if view["status"] == "over" and view["overspent"] > 0:
            warnings.append({"category": view["category"], "overspent": view["overspent"]})
        display.append(view)
    return {
        "budgets": display,
        "budget_categories": expense_category_names(),
        "budget_warnings": warnings,
    }


def _goals_context():
    goals_list = load_data(f_goals, [])
    accounts = load_data(f_accounts, [])
    records = load_records()
    short_goals, long_goals, active_goals, completed_goals = [], [], [], []
    goals_changed = False

    for g in goals_list:
        saved = sum(r.get("amount", 0) for r in records
                    if r.get("category") == "Goal Savings" and r.get("goal_id") == g.get("id"))
        target = g.get("target", 0)
        percent = (saved / target) * 100 if target else 0
        remaining = max(0, target - saved)
        stored_status = g.get("status", "In Progress")
        status = "Completed" if percent >= 100 else stored_status
        time_data = _goal_time_data(g.get("target_date"), remaining)

        contrib_sorted = sorted(
            (r for r in records
             if r.get("category") == "Goal Savings" and r.get("goal_id") == g.get("id")),
            key=lambda x: x.get("date", ""), reverse=True,
        )
        contributions = [
            {"id": r.get("id"), "date": r.get("date", ""),
             "account": r.get("account", ""), "amount": r.get("amount", 0)}
            for r in contrib_sorted
        ]

        goal_data = {
            "id": g.get("id"), "name": g.get("name"), "target": target, "saved": saved,
            "remaining": remaining, "percent": percent, "display_percent": min(percent, 100),
            "status": status, "priority": g.get("priority", "medium"),
            "notes": g.get("notes", ""), "target_date": g.get("target_date", ""),
            "goal_type": g.get("type"),
            "milestone_25": saved >= target * 0.25 if target else False,
            "milestone_50": saved >= target * 0.50 if target else False,
            "milestone_75": saved >= target * 0.75 if target else False,
            "milestone_100": percent >= 100,
            "contributions": contributions,
            "completion_date": g.get("completion_date", ""),
        }
        goal_data.update(time_data)

        if status in ("Completed", "Cancelled"):
            if status == "Completed" and not g.get("completion_date"):
                g["completion_date"] = datetime.now().strftime("%Y-%m-%d")
                goal_data["completion_date"] = g["completion_date"]
                goals_changed = True
            completed_goals.append(goal_data)
        else:
            active_goals.append(goal_data)
            (short_goals if g.get("type") == "short" else long_goals).append(goal_data)

    if goals_changed:
        save_data(f_goals, goals_list)

    return {
        "accounts": accounts,
        "short_goals": short_goals,
        "long_goals": long_goals,
        "active_goals": active_goals,
        "completed_goals": completed_goals,
    }


def _plan_summary(bctx, gctx):
    budgets = bctx["budgets"]
    active = gctx["active_goals"]
    total_limit = round(sum(b["effective_limit"] for b in budgets), 2)
    total_spent = round(sum(b["spent"] for b in budgets), 2)
    goal_target = round(sum(g["target"] for g in active), 2)
    goal_saved = round(sum(g["saved"] for g in active), 2)
    return {
        "budget_count": len(budgets),
        "total_limit": total_limit,
        "total_spent": total_spent,
        "budget_percent": round(total_spent / total_limit * 100) if total_limit else 0,
        "budget_headroom": round(total_limit - total_spent, 2),
        "budgets_over": sum(1 for b in budgets if b["status"] in ("over", "full")),
        "budgets_near": sum(1 for b in budgets if b["status"] == "warning"),
        "active_goal_count": len(active),
        "completed_goal_count": len(gctx["completed_goals"]),
        "goal_target": goal_target,
        "goal_saved": goal_saved,
        "goal_remaining": round(goal_target - goal_saved, 2),
        "goal_percent": round(goal_saved / goal_target * 100) if goal_target else 0,
        "goal_monthly_need": round(sum((g.get("required_monthly") or 0) for g in active), 2),
    }


PLAN_TABS = ("summary", "budgets", "goals")


def _render_plan(budget_error=None, goal_error=None, tab=None):
    # tab comes from ?tab=, or defaults per which error we're showing
    if tab is None:
        tab = request.args.get("tab", "summary")
    if budget_error:
        tab = "budgets"
    elif goal_error:
        tab = "goals"
    if tab not in PLAN_TABS:
        tab = "summary"

    bctx = _budget_context()
    gctx = _goals_context()
    return render_template(
        "plan.html",
        tab=tab,
        plan_summary=_plan_summary(bctx, gctx),
        budget_error=budget_error,
        goal_error=goal_error,
        **bctx, **gctx,
    )


@finance_bp.route("/plan")
def plan():
    return _render_plan()


@finance_bp.route("/budget", methods=["GET", "POST"])
def budget():
    if request.method != "POST":
        return redirect(url_for("finance.plan", tab="budgets"))

    category = request.form.get("category")
    amount_raw = request.form.get("amount")
    period = request.form.get("period", "monthly")
    rollover = request.form.get("rollover") == "on"

    if not category or not amount_raw:
        return _render_plan(budget_error="Category and amount required")
    try:
        amount = float(amount_raw)
    except Exception:
        return _render_plan(budget_error="Invalid amount")

    budgets = load_data(f_budget, [])
    for b in budgets:
        if b["category"] == category:
            b["amount"], b["period"], b["rollover"] = amount, period, rollover
            break
    else:
        budgets.append({"category": category, "amount": amount,
                        "period": period, "rollover": rollover})
    save_data(f_budget, budgets)
    return redirect(url_for("finance.plan", tab="budgets"))

# ================= EDIT BUDGET =================
# ================= 编辑预算 =================

@finance_bp.route("/edit_budget/<category>", methods=["GET", "POST"])
def edit_budget(category):
    budgets = load_data(f_budget, [])
    budget = next((b for b in budgets if b["category"] == category), None)

    if not budget:
        return redirect(url_for("finance.plan", tab="budgets"))

    # Keep this budget's own category in the picker even if it was archived,
    # so the edit form can still display and re-save it.
    category_options = expense_category_names()
    if budget["category"] not in category_options:
        category_options = category_options + [budget["category"]]

    if request.method == "POST":
        new_category = request.form.get("category", budget["category"])

        # Each category can only have one budget (same rule the /budget
        # "add" form enforces) — block the rename instead of silently
        # merging into / overwriting an unrelated category's existing
        # budget.
        if new_category != budget["category"]:
            conflict = any(
                b is not budget and b["category"] == new_category
                for b in budgets
            )
            if conflict:
                return render_template(
                    "edit_budget.html",
                    budget=budget,
                    categories=category_options,
                    error=f"You already have a budget for {new_category}.",
                )
            budget["category"] = new_category

        budget["amount"] = float(request.form.get("amount"))
        budget["period"] = request.form.get("period", budget.get("period", "monthly"))
        budget["rollover"] = request.form.get("rollover") == "on"
        save_data(f_budget, budgets)
        return redirect(url_for("finance.plan", tab="budgets"))

    return render_template(
        "edit_budget.html",
        budget=budget,
        categories=category_options,
    )

# ================= DELETE BUDGET =================
# ================= 删除预算 =================

@finance_bp.route("/delete_budget/<category>", methods=["POST"])
def delete_budget(category):
    budgets = load_data(f_budget, [])
    budgets = [b for b in budgets if b["category"] != category]
    save_data(f_budget, budgets)
    return redirect(url_for("finance.plan", tab="budgets"))

# ================= SUMMARY =================
# ================= 财务汇总 =================

@finance_bp.route("/summary")
def summary():
    records = load_records()
    now = datetime.now()

    selected_month = request.args.get("month", now.strftime("%m"))
    selected_year = request.args.get("year", now.strftime("%Y"))
    current_month = f"{selected_year}-{selected_month}"

    month_records = [r for r in records if r.get("date", "").startswith(current_month)]
    year_records = [r for r in records if r.get("date", "").startswith(selected_year)]

    # 计算收入/支出时都要排除 "Transfer In"/"Transfer Out"（转账）——
    # 转账只是把钱从自己的一个账户挪到另一个账户，不是真正赚到或花掉的钱，
    # 算进收支里会让数字失真。
    # Income/expense totals exclude "Transfer In"/"Transfer Out" — a
    # transfer just moves money between your own accounts, it isn't real
    # income or spending, so counting it here would skew the numbers.

    income = sum(r["amount"] for r in month_records if r["type"] == "income" and r.get("category") != "Transfer In")
    expense = sum(r["amount"] for r in month_records if r["type"] == "expense" and r.get("category") != "Transfer Out")
    balance = income - expense

    category_totals = {}
    for r in month_records:
        if r.get("type") == "expense" and r.get("category") != "Transfer Out":
            category = r.get("category", "Other")
            category_totals[category] = category_totals.get(category, 0) + r.get("amount", 0)

    total_expense = sum(category_totals.values())
    # sorted(..., key=lambda x: x[1], reverse=True)[:3]：把 (分类, 金额)
    # 这些键值对按金额从大到小排序，再取前 3 个，就是"花费最多的
    # 三个分类"。
    # sorted(..., key=lambda x: x[1], reverse=True)[:3]: sorts the
    # (category, amount) pairs from largest to smallest amount, then takes
    # the first 3 — giving the "top 3 highest-spending categories".
    top_categories = sorted(category_totals.items(), key=lambda x: x[1], reverse=True)[:3]
    # 给每个分类多算一个"占总支出的百分比"，如果这个月完全没有支出
    # （total_expense 是 0），就用 0% 代替，避免除以零报错。
    # Adds each category's share of total spending as a percentage; if
    # there was no spending at all this month (total_expense is 0), uses
    # 0% instead of dividing by zero.
    top_categories_with_percent = [(c, a, (a / total_expense * 100) if total_expense else 0) for c, a in top_categories]

    insight = "Your spending looks stable this month."
    if top_categories:
        top_cat, top_amt = top_categories[0]
        insight = f"Most spending comes from {top_cat} (RM {top_amt:.2f})."
    if expense == 0:
        insight = "No expenses recorded this month."
    if balance < 0:
        insight += " You are spending more than you earn."

    comparison = "No income recorded yet."
    if income > 0:
        expense_ratio = (expense / income) * 100
        comparison = f"Expenses are {expense_ratio:.0f}% of income this month."

    budgets = load_data(f_budget, [])
    budget_usage = []

    for b in budgets:
        view = _budget_view(b, records)
        budget_usage.append({
            "category": view["category"],
            "spent": view["spent"],
            "limit": view["effective_limit"],
            "base_limit": view["amount"],
            "carryover": view["carryover"],
            "rollover": view["rollover"],
            "remaining": view["remaining"],
            "percent": view["percent"],
            "display_percent": view["display_percent"],
            "status": view["status"],
        })

    yearly_income = sum(r.get("amount", 0) for r in year_records if r.get("type") == "income" and r.get("category") != "Transfer In")
    yearly_expense = sum(r.get("amount", 0) for r in year_records if r.get("type") == "expense" and r.get("category") != "Transfer Out")
    yearly_balance = yearly_income - yearly_expense

    # 算出这一年里每个月各自的收入/支出/结余，存成一个字典，
    # key 是 "年份-月份"（比如 "2026-01"），方便前端画每月趋势图。
    # month:02d 会把 1 补成 "01"、把 12 保持 "12"，确保月份永远是两位数。
    # Builds each month's income/expense/balance for the selected year into
    # a dict keyed by "year-month" (e.g. "2026-01"), so the frontend can
    # draw a month-by-month trend chart. month:02d pads 1 into "01" while
    # leaving 12 as "12", so the month is always 2 digits.

    monthly_data = {}
    for month in range(1, 13):
        key = f"{selected_year}-{month:02d}"
        monthly_income = sum(r.get("amount", 0) for r in year_records if r.get("type") == "income" and r.get("category") != "Transfer In" and r.get("date", "").startswith(key))
        monthly_expense = sum(r.get("amount", 0) for r in year_records if r.get("type") == "expense" and r.get("category") != "Transfer Out" and r.get("date", "").startswith(key))
        monthly_data[key] = {
            "income": monthly_income,
            "expense": monthly_expense,
            "balance": monthly_income - monthly_expense
        }

    goals_data = load_data(f_goals, [])
    accounts = load_data(f_accounts, [])

    saving = 0
    savings_accounts = [a["name"] for a in accounts if a.get("purpose") == "savings"]
    for acc in savings_accounts:
        balance_acc = 0
        for r in records:
            if r.get("account") != acc:
                continue
            if r.get("type") in ("income", "saving"):
                balance_acc += r.get("amount", 0)
            elif r.get("type") == "expense":
                balance_acc -= r.get("amount", 0)
        saving += balance_acc

    short_goals, long_goals = [], []
    for g in goals_data:
        saved = sum(r.get("amount", 0) for r in records if r.get("category") == "Goal Savings" and r.get("goal_id") == g["id"])
        target = g.get("target", 0)
        percent = (saved / target) * 100 if target else 0
        remaining_goal = target - saved
        status = "Completed" if percent >= 100 else "In Progress"
        goal_data = {
            "id": g.get("id"),
            "name": g.get("name"),
            "target": target,
            "saved": saved,
            "remaining": remaining_goal,
            "percent": percent,
            "display_percent": min(percent, 100),
            "status": status,
            "goal_type": g.get("type")
        }
        # 三元表达式：type 是 "short" 就放进 short_goals 列表，
        # 否则放进 long_goals 列表 —— 用一行代替一整个 if/else 块。
        # A one-line if/else: if the type is "short" it goes into
        # short_goals, otherwise into long_goals — condensing what would
        # otherwise be a full if/else block into a single line.
        (short_goals if g.get("type") == "short" else long_goals).append(goal_data)

    all_goals = short_goals + long_goals

    return render_template(
        "summary.html",
        income=income,
        expense=expense,
        saving=saving,
        balance=balance,
        daily_avg=round(expense / max(now.day, 1), 2),
        top_categories=top_categories_with_percent,
        insight=insight,
        comparison=comparison,
        budget_usage=budget_usage,
        year_income=yearly_income,
        year_expense=yearly_expense,
        year_balance=yearly_balance,
        monthly_data=monthly_data,
        selected_month=selected_month,
        selected_year=selected_year,
        short_goals=short_goals,
        long_goals=long_goals,
        all_goals=all_goals,
        ai_review=load_insights().get(f"{selected_year}-{selected_month}"),
        income_forecast=load_insights().get(f"forecast-{_next_month_key()}"),
        forecast_target=_next_month_key(),
    )

# ================= GOALS =================
# ================= 储蓄目标 =================

@finance_bp.route("/goals", methods=["GET", "POST"])
def goals():
    """GET redirects to /plan; POST (create goal / add savings) is handled
    then returns to /plan. The goal display logic lives in _goals_context()."""
    if request.method != "POST":
        return redirect(url_for("finance.plan", tab="goals"))

    action = request.form.get("action")

    if action == "create":
        name = request.form.get("name")
        target = request.form.get("target")
        goal_type = request.form.get("type")
        if not name or not target or not goal_type:
            return _render_plan(goal_error="All fields required")
        try:
            target = float(target)
        except Exception:
            return _render_plan(goal_error="Invalid target amount")

        goals_list = load_data(f_goals, [])
        next_goal_id = max([g.get("id", 0) for g in goals_list], default=0) + 1
        goals_list.append({
            "id": next_goal_id,
            "name": name,
            "type": goal_type,
            "target": target,
            "target_date": request.form.get("target_date") or None,
            "priority": request.form.get("priority", "medium"),
            "notes": request.form.get("notes", ""),
            "status": "In Progress",
        })
        save_data(f_goals, goals_list)
        return redirect(url_for("finance.plan", tab="goals"))

    if action == "save":
        try:
            goal_id = int(request.form.get("goal_id"))
            amount = float(request.form.get("amount"))
        except (TypeError, ValueError):
            return redirect(url_for("finance.plan", tab="goals"))

        # Adding savings to a goal is just a normal "expense" record tagged
        # with category "Goal Savings" + goal_id; the goal's saved total is
        # recalculated from those rows (no separate running total).
        records = load_records()
        records.append({
            "id": new_id(),
            "date": datetime.now().strftime("%Y-%m-%d"),
            "type": "expense",
            "category": "Goal Savings",
            "goal_id": goal_id,
            "account": request.form.get("account"),
            "item": f"Goal: {request.form.get('goal_name')}",
            "amount": amount,
        })
        save_records(records)
        return redirect(url_for("finance.plan", tab="goals"))

    return redirect(url_for("finance.plan", tab="goals"))

# ================= DELETE GOALS =================
# ================= 删除目标 =================

@finance_bp.route("/delete_goal/<int:goal_id>", methods=["POST"])
def delete_goal(goal_id):
    goals_list = load_data(f_goals, [])
    goals_list = [g for g in goals_list if g.get("id") != goal_id]
    save_data(f_goals, goals_list)
    return redirect(url_for("finance.plan", tab="goals"))

# ================= REOPEN GOAL =================
# ================= 重新开启目标 =================

@finance_bp.route("/reopen_goal/<int:goal_id>", methods=["POST"])
def reopen_goal(goal_id):
    goals_list = load_data(f_goals, [])
    for g in goals_list:
        if g.get("id") == goal_id:
            g["status"] = "In Progress"
            g.pop("completion_date", None)
            break
    save_data(f_goals, goals_list)
    return redirect(url_for("finance.plan", tab="goals"))

# ================= QUICK STATUS ACTIONS =================
# ================= 快速状态操作 =================

@finance_bp.route("/pause_goal/<int:goal_id>", methods=["POST"])
def pause_goal(goal_id):
    goals_list = load_data(f_goals, [])
    for g in goals_list:
        if g.get("id") == goal_id:
            g["status"] = "Paused"
            break
    save_data(f_goals, goals_list)
    return redirect(url_for("finance.plan", tab="goals"))

@finance_bp.route("/resume_goal/<int:goal_id>", methods=["POST"])
def resume_goal(goal_id):
    goals_list = load_data(f_goals, [])
    for g in goals_list:
        if g.get("id") == goal_id:
            g["status"] = "In Progress"
            break
    save_data(f_goals, goals_list)
    return redirect(url_for("finance.plan", tab="goals"))

@finance_bp.route("/cancel_goal/<int:goal_id>", methods=["POST"])
def cancel_goal(goal_id):
    goals_list = load_data(f_goals, [])
    for g in goals_list:
        if g.get("id") == goal_id:
            g["status"] = "Cancelled"
            break
    save_data(f_goals, goals_list)
    return redirect(url_for("finance.plan", tab="goals"))

@finance_bp.route("/complete_goal/<int:goal_id>", methods=["POST"])
def complete_goal(goal_id):
    goals_list = load_data(f_goals, [])
    for g in goals_list:
        if g.get("id") == goal_id:
            g["status"] = "Completed"
            g["completion_date"] = datetime.now().strftime("%Y-%m-%d")
            break
    save_data(f_goals, goals_list)
    return redirect(url_for("finance.plan", tab="goals"))

# ================= EDIT GOALS =================
# ================= 编辑目标 =================

@finance_bp.route("/edit_goal/<int:goal_id>", methods=["GET", "POST"])
def edit_goal(goal_id):
    goals_list = load_data(f_goals, [])
    goal = next((g for g in goals_list if g.get("id") == goal_id), None)

    if not goal:
        return redirect(url_for("finance.plan", tab="goals"))

    if request.method == "POST":
        name = request.form.get("name")
        target = request.form.get("target")

        if not name or not target:
            return render_template("edit_goal.html", goal=goal, error="All fields required")

        try:
            target = float(target)
        except Exception:
            return render_template("edit_goal.html", goal=goal, error="Invalid target amount")

        goal["name"] = name
        goal["target"] = target
        goal["target_date"] = request.form.get("target_date") or None
        goal["priority"] = request.form.get("priority", goal.get("priority", "medium"))
        goal["notes"] = request.form.get("notes", goal.get("notes", ""))
        goal["status"] = request.form.get("status", goal.get("status", "In Progress"))
        save_data(f_goals, goals_list)
        return redirect(url_for("finance.plan", tab="goals"))

    return render_template("edit_goal.html", goal=goal)

# ================= ACCOUNTS =================
# ================= 账户 =================

@finance_bp.route("/accounts", methods=["GET", "POST"])
def accounts():
    accounts_data = load_data(f_accounts, [])
    records = load_records()

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        purpose = request.form.get("purpose", "spending")
        if name and not any(a["name"] == name for a in accounts_data):
            accounts_data.append({"name": name, "purpose": purpose})
            save_data(f_accounts, accounts_data)
        return redirect(url_for("finance.accounts"))

    account_list = []
    for acc in accounts_data:
        acc_txns = [r for r in records if r.get("account") == acc["name"]]
        # 这个账户的余额 = 所有"收入/存款"加起来，减去所有"支出"，
        # 用一个生成器表达式一次性算完，不用先建一个临时列表。
        # This account's balance = every "income/saving" summed up, minus
        # every "expense" — computed in one pass with a generator
        # expression instead of building a temporary list first.
        balance  = sum(
            r.get("amount", 0) if r.get("type") in ("income", "saving") else -r.get("amount", 0)
            for r in acc_txns
        )
        # max(..., default=None)：找出这个账户最新一笔交易的日期；
        # 如果这个账户完全没有交易记录，就用 None 代替（避免 max()
        # 在空序列上直接报错）。
        # max(..., default=None): finds this account's most recent
        # transaction date; if the account has no transactions at all,
        # falls back to None (avoiding max() raising an error on an
        # empty sequence).
        last_txn = max((r.get("date", "") for r in acc_txns), default=None) if acc_txns else None
        account_list.append({
            "name":      acc["name"],
            "purpose":   acc.get("purpose", "spending"),
            "balance":   round(balance, 2),
            "txn_count": len(acc_txns),
            "last_txn":  last_txn,
        })

    return render_template("accounts.html", account_list=account_list)


@finance_bp.route("/edit_account/<name>", methods=["GET", "POST"])
def edit_account(name):
    accounts_data = load_data(f_accounts, [])
    records = load_records(include_deleted=True)  # trashed rows keep a valid account name too

    account = next((a for a in accounts_data if a.get("name") == name), None)
    if not account:
        return redirect(url_for("finance.accounts"))

    error = None
    if request.method == "POST":
        new_name    = request.form.get("name", "").strip()
        new_purpose = request.form.get("purpose", "spending")

        if not new_name:
            error = "Account name cannot be empty."
        elif new_name != name and any(a["name"] == new_name for a in accounts_data):
            error = f'An account named "{new_name}" already exists.'
        else:
            if new_name != name:
                # 账户改名了 —— 得把原本挂在旧名字上的每一笔交易记录，
                # 都同步改成新名字，不然那些记录会变成挂在一个"不存在"
                # 的账户名下，从此再也找不到。
                # The account was renamed — every transaction record
                # tagged with the old name must be updated to the new name
                # too, otherwise those records would end up pointing at an
                # account name that no longer exists, and become
                # unreachable from then on.
                for r in records:
                    if r.get("account") == name:
                        r["account"] = new_name
                save_records(records)
            account["name"]    = new_name
            account["purpose"] = new_purpose
            save_data(f_accounts, accounts_data)
            return redirect(url_for("finance.accounts"))

    return render_template("edit_account.html", account=account, error=error)


@finance_bp.route("/delete_account/<name>", methods=["POST"])
def delete_account(name):
    accounts_data = load_data(f_accounts, [])
    accounts_data = [a for a in accounts_data if a.get("name") != name]
    save_data(f_accounts, accounts_data)
    return redirect(url_for("finance.accounts"))


# ================= SHARED MONEY PARSING =================
# ================= 金额解析（多处复用） =================

def _parse_money(raw, field="Amount", allow_zero=True):
    """Return ``(value, error)``. ``value`` is a non-negative float rounded to
    2dp, or ``None`` when ``error`` is set."""
    if raw is None or str(raw).strip() == "":
        return (0.0, None) if allow_zero else (None, f"{field} is required.")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None, f"{field} must be a number."
    if not math.isfinite(value) or value < 0:
        return None, f"{field} cannot be negative."
    if value == 0 and not allow_zero:
        return None, f"{field} must be greater than 0."
    return round(value, 2), None


# ================= CATEGORIES =================
# ================= 分类管理 =================
# 用户自定义分类的增删改查。一个 POST 端点，用 action 字段区分操作
# （沿用 goals / budget 的写法）。分类被历史记录引用时不能真正删除，
# 只能归档（archived=True），这样旧交易仍然有效。
# CRUD for the user's own categories. One POST endpoint switched on an
# `action` field (same style as goals / budget). A category referenced by
# existing records can't be hard-deleted — only archived — so historical
# transactions stay valid.

def _rename_category_everywhere(old_name, new_name):
    """Point every stored reference (records, budgets, shopping items) at the
    category's new name, the same way edit_account rewrites account names."""
    if old_name == new_name:
        return

    # include trashed records — a restored record must keep a valid category
    records = load_records(include_deleted=True)
    if any(r.get("category") == old_name for r in records):
        for r in records:
            if r.get("category") == old_name:
                r["category"] = new_name
        save_records(records)

    budgets = load_data(f_budget, [])
    if any(b.get("category") == old_name for b in budgets):
        for b in budgets:
            if b.get("category") == old_name:
                b["category"] = new_name
        save_data(f_budget, budgets)

    items = load_shopping()
    if any(it.get("category") == old_name for it in items):
        for it in items:
            if it.get("category") == old_name:
                it["category"] = new_name
        save_shopping(items)


def _category_in_use(name):
    # trashed records count too — deleting the category would orphan them on restore
    if any(r.get("category") == name for r in load_records(include_deleted=True)):
        return "existing records"
    if any(b.get("category") == name for b in load_data(f_budget, [])):
        return "a budget"
    return None


def _handle_category_action(action, cats):
    form = request.form

    if action == "create":
        name = (form.get("name") or "").strip()
        kind = form.get("kind")
        if not name:
            return "Category name is required."
        if kind not in KINDS:
            return "Choose a valid type (income, expense or saving)."
        if name in RESERVED_CATEGORY_NAMES:
            return f'"{name}" is reserved by the app.'
        if any(c["name"].casefold() == name.casefold() and c["kind"] == kind for c in cats):
            return f'A {kind} category named "{name}" already exists.'
        order = max([c.get("order", 0) for c in cats if c["kind"] == kind], default=-1) + 1
        cats.append({
            "id": new_id(),
            "name": name,
            "kind": kind,
            "icon": clean_icon(form.get("icon")),
            "color": clean_color(form.get("color")),
            "order": order,
            "archived": False,
            "is_default": False,
            "created_at": today_iso(),
        })
        save_categories(cats)
        return None

    cat = find_category(cats, form.get("id"))
    if not cat:
        return "That category no longer exists."

    if action == "rename":
        new_name = (form.get("name") or "").strip()
        if not new_name:
            return "Category name is required."
        if new_name in RESERVED_CATEGORY_NAMES:
            return f'"{new_name}" is reserved by the app.'
        if new_name.casefold() != cat["name"].casefold() and any(
            c is not cat and c["name"].casefold() == new_name.casefold()
            and c["kind"] == cat["kind"] for c in cats
        ):
            return f'A {cat["kind"]} category named "{new_name}" already exists.'
        _rename_category_everywhere(cat["name"], new_name)
        cat["name"] = new_name
        save_categories(cats)
        return None

    if action == "style":
        cat["icon"] = clean_icon(form.get("icon"))
        cat["color"] = clean_color(form.get("color"))
        save_categories(cats)
        return None

    if action == "archive":
        cat["archived"] = True
        cat["is_default"] = False
        save_categories(cats)
        return None

    if action == "unarchive":
        cat["archived"] = False
        save_categories(cats)
        return None

    if action == "delete":
        used = _category_in_use(cat["name"])
        if used:
            return (f'"{cat["name"]}" is used by {used} — archive it instead so '
                    f"the history stays valid.")
        cats.remove(cat)
        save_categories(cats)
        return None

    if action == "set_default":
        for c in cats:
            if c["kind"] == cat["kind"]:
                c["is_default"] = (c is cat)
        cat["archived"] = False
        save_categories(cats)
        return None

    if action == "clear_default":
        cat["is_default"] = False
        save_categories(cats)
        return None

    if action == "move":
        if cat.get("archived"):
            return None
        siblings = sorted(
            [c for c in cats if c["kind"] == cat["kind"] and not c.get("archived")],
            key=lambda c: c.get("order", 0),
        )
        pos = siblings.index(cat)
        swap = pos - 1 if form.get("direction") == "up" else pos + 1
        if 0 <= swap < len(siblings):
            cat["order"], siblings[swap]["order"] = (
                siblings[swap].get("order", 0), cat.get("order", 0),
            )
            save_categories(cats)
        return None

    return "Unknown action."


def _render_categories(cats, error=None):
    used_names = {r.get("category") for r in load_records(include_deleted=True)}
    used_names |= {b.get("category") for b in load_data(f_budget, [])}

    grouped = {}
    for kind in KINDS:
        rows = sorted(
            (c for c in cats if c.get("kind") == kind),
            key=lambda c: (c.get("archived", False), c.get("order", 0), c.get("name", "").lower()),
        )
        grouped[kind] = [
            {**c, "in_use": c.get("name") in used_names} for c in rows
        ]

    return render_template(
        "categories.html",
        grouped=grouped,
        kinds=KINDS,
        error=error,
    )


@finance_bp.route("/categories", methods=["GET", "POST"])
def categories():
    cats = load_categories()

    if request.method == "POST":
        error = _handle_category_action(request.form.get("action", "create"), cats)
        if error:
            return _render_categories(cats, error=error)
        return redirect(url_for("finance.categories"))

    return _render_categories(cats)


# ================= SHOPPING LIST =================
# ================= 购物清单 / 消费决策 =================
# 购物清单不只是待办列表：每一项都记录"为什么该买"和"为什么不该买"，
# 帮用户克制冲动消费。决定 Buy 之后可以一键生成一笔支出，不用重新输入。
# The shopping list is a purchase-decision tool, not a checklist: each item
# records the case *for* and *against* buying it. Once the decision is
# "buy", recording the purchase creates a Finance expense in one step
# without re-typing the details.

def _shopping_fields_from_form(form, expense_categories):
    """Pull + validate the editable fields shared by create/update.
    Returns ``(data, error)``."""
    name = (form.get("name") or "").strip()
    if not name:
        return None, "Item name is required."

    price, price_error = _parse_money(form.get("estimated_price"), "Estimated price")
    if price_error:
        return None, price_error

    priority = form.get("priority", "medium")
    if priority not in SHOPPING_PRIORITIES:
        priority = "medium"

    category = (form.get("category") or "").strip()
    if category and expense_categories and category not in expense_categories:
        return None, f'"{category}" is not one of your expense categories.'

    try:
        wait_days = max(0, int(form.get("wait_days") or 0))
    except (TypeError, ValueError):
        return None, "Cooling-off days must be a whole number."

    return {
        "name": name[:160],
        "estimated_price": price,
        "category": category,
        "priority": priority,
        "reasons_for": (form.get("reasons_for") or "").strip(),
        "reasons_against": (form.get("reasons_against") or "").strip(),
        "notes": (form.get("notes") or "").strip(),
        "target_date": (form.get("target_date") or "").strip(),
        "wait_days": wait_days,
    }, None


def _shopping_budget_check(item, records, budgets):
    """How this purchase would land against the item category's budget."""
    category = item.get("category")
    if not category:
        return None
    budget = next((b for b in budgets if b.get("category") == category), None)
    if not budget:
        return None
    view = _budget_view(budget, records)
    price = round(float(item.get("estimated_price") or 0), 2)
    after = round(view["spent"] + price, 2)
    limit = view["effective_limit"]
    return {
        "category": category,
        "period": view["period"],
        "limit": limit,
        "spent": view["spent"],
        "after": after,
        "over_by": round(max(0.0, after - limit), 2),
        "percent_after": round(after / limit * 100) if limit > 0 else 0,
    }


def _handle_shopping_action(form, items):
    action = form.get("action", "create")
    expense_categories = [c["name"] for c in active_categories("expense")]

    if action == "create":
        data, error = _shopping_fields_from_form(form, expense_categories)
        if error:
            return error
        item = {
            "id": new_id(),
            "date_added": today_iso(),
            "status": "open",
            "decision": "undecided",
            "decision_date": "",
            "expense_created": False,
            "purchased_at": "",
            "actual_price": None,
            **data,
        }
        items.append(item)
        save_shopping(items)
        return None

    item = find_shopping_item(items, form.get("id"))
    if not item:
        return "That shopping item no longer exists."

    if action == "update":
        data, error = _shopping_fields_from_form(form, expense_categories)
        if error:
            return error
        item.update(data)
        save_shopping(items)
        return None

    if action == "decide":
        decision = form.get("decision")
        if decision not in SHOPPING_DECISIONS:
            return "Choose a valid decision."
        item["decision"] = decision
        item["decision_date"] = today_iso() if decision != "undecided" else ""
        # A "buy" or "wait" decision starts the cooling-off clock (if the item
        # has wait_days set); "wait" with no days uses a 14-day default.
        wait_days = int(item.get("wait_days") or 0)
        if decision in ("buy", "wait"):
            if decision == "wait" and wait_days == 0:
                wait_days = 14
                item["wait_days"] = 14
            item["wait_until"] = _plus_days(today_iso(), wait_days) if wait_days > 0 else ""
        else:
            item["wait_until"] = ""
        # Deciding against it (or parking it) also resolves the item; picking
        # "buy" / "wait" / clearing the decision keeps it on the open list.
        if decision == "dont_buy":
            item["status"] = "dismissed"
        elif item["status"] == "dismissed":
            item["status"] = "open"
        save_shopping(items)
        return None

    if action == "skip_wait":
        item["wait_until"] = ""
        save_shopping(items)
        return None

    if action == "add_price_check":
        price, error = _parse_money(form.get("price"), "Price", allow_zero=False)
        if error:
            return error
        item.setdefault("price_checks", []).append({
            "id": new_id(),
            "date": (form.get("date") or "").strip() or today_iso(),
            "price": price,
            "source": (form.get("source") or "").strip()[:80],
            "note": (form.get("note") or "").strip()[:160],
        })
        save_shopping(items)
        return None

    if action == "delete_price_check":
        cid = form.get("check_id")
        item["price_checks"] = [c for c in item.get("price_checks", []) if c.get("id") != cid]
        save_shopping(items)
        return None

    if action == "rate":
        if item.get("status") != "bought":
            return "Only bought items can be rated."
        worth = form.get("worth_it")
        if worth not in ("yes", "no", "meh"):
            return "Pick worth-it / not / meh."
        item["worth_it"] = worth
        item["hindsight_note"] = (form.get("hindsight_note") or "").strip()[:300]
        item["rated_at"] = today_iso()
        save_shopping(items)
        return None

    if action == "reopen":
        item["status"] = "open"
        save_shopping(items)
        return None

    if action == "dismiss":
        item["status"] = "dismissed"
        save_shopping(items)
        return None

    if action == "delete":
        items.remove(item)
        save_shopping(items)
        return None

    if action == "purchase":
        # Turn the item into a real expense without re-entering anything.
        accounts = load_data(f_accounts, [])
        account = form.get("account")
        if not account or not any(a["name"] == account for a in accounts):
            return "Choose an account to record the purchase against."

        # Cooling-off period still running? Block unless explicitly overridden.
        wait_until = item.get("wait_until") or ""
        if wait_until and today_iso() < wait_until and form.get("force") != "1":
            return (f"Still in the cooling-off period until {wait_until}. "
                    f'Use "Buy now anyway" to override.')

        amount, amount_error = _parse_money(
            form.get("amount") if form.get("amount") not in (None, "")
            else item.get("estimated_price"),
            "Purchase amount", allow_zero=False,
        )
        if amount_error:
            return amount_error

        purchase_date = (form.get("date") or "").strip() or today_iso()

        category = item.get("category")
        if not category or (expense_categories and category not in expense_categories):
            category = expense_categories[0] if expense_categories else "Other"

        records = load_records()
        records.append({
            "id": new_id(),
            "date": purchase_date,
            "type": "expense",
            "category": category,
            "account": account,
            "item": item["name"],
            "amount": amount,
            "receipt": None,
            "source": "shopping_list",
        })
        save_records(records)

        item["status"] = "bought"
        item["decision"] = "buy"
        item["decision_date"] = item.get("decision_date") or today_iso()
        item["wait_until"] = ""
        item["expense_created"] = True
        item["purchased_at"] = purchase_date
        item["actual_price"] = amount
        save_shopping(items)
        return None

    return "Unknown action."


def _render_shopping(items, error=None):
    expense_categories = [c["name"] for c in active_categories("expense")]
    accounts = load_data(f_accounts, [])
    records = load_records()
    budgets = load_data(f_budget, [])
    today = today_iso()

    priority_rank = {"high": 0, "medium": 1, "low": 2}
    status_rank = {"open": 0, "bought": 1, "dismissed": 2}

    ordered = sorted(
        items,
        key=lambda it: (
            status_rank.get(it.get("status"), 9),
            priority_rank.get(it.get("priority"), 9),
            it.get("date_added", ""),
        ),
    )

    view_items = []
    for it in ordered:
        wait_until = it.get("wait_until") or ""
        ready = not wait_until or wait_until <= today
        days_left = 0
        if wait_until and not ready:
            days_left = (datetime.strptime(wait_until, "%Y-%m-%d").date()
                         - datetime.strptime(today, "%Y-%m-%d").date()).days
        checks = it.get("price_checks", [])
        prices = [float(c.get("price") or 0) for c in checks if c.get("price")]
        view_items.append({
            **it,
            "ready": ready,
            "wait_days_left": days_left,
            "budget_check": _shopping_budget_check(it, records, budgets),
            "lowest_price": round(min(prices), 2) if prices else None,
            "latest_price": round(prices[-1], 2) if prices else None,
        })

    open_items = [it for it in items if it.get("status") == "open"]
    bought = [it for it in items if it.get("status") == "bought"]
    rated = [it for it in bought if it.get("worth_it")]
    totals = {
        "open_count": len(open_items),
        "open_value": round(sum(float(it.get("estimated_price") or 0) for it in open_items), 2),
        "to_buy_value": round(sum(
            float(it.get("estimated_price") or 0)
            for it in open_items if it.get("decision") == "buy"
        ), 2),
        "spent_value": round(sum(float(it.get("actual_price") or 0) for it in bought), 2),
    }
    history = {
        "count": len(bought),
        "rated": len(rated),
        "worth_it": sum(1 for it in rated if it.get("worth_it") == "yes"),
        "not_worth": sum(1 for it in rated if it.get("worth_it") == "no"),
        "meh": sum(1 for it in rated if it.get("worth_it") == "meh"),
        "vs_estimate": round(sum(
            float(it.get("actual_price") or 0) - float(it.get("estimated_price") or 0)
            for it in bought
        ), 2),
    }

    return render_template(
        "shopping.html",
        items=view_items,
        categories=expense_categories,
        accounts=accounts,
        priorities=SHOPPING_PRIORITIES,
        totals=totals,
        history=history,
        today=today,
        error=error,
    )


@finance_bp.route("/shopping", methods=["GET", "POST"])
def shopping():
    items = load_shopping()

    if request.method == "POST":
        error = _handle_shopping_action(request.form, items)
        if error:
            return _render_shopping(items, error=error)
        return redirect(url_for("finance.shopping"))

    return _render_shopping(items)


# ================= SHOPPING: AI PURCHASE ADVISOR =================
# ================= 购物：AI 购买建议 =================
# 同一套 OpenAI 管道（key 从 Finance/.env 读，严格 JSON schema，不存图，
# 没配 key 就返回 503、页面照常用）。给一个购物清单条目 + 用户真实的
# 财务情况，让模型给出 buy / wait / dont_buy 的建议。建议只是参考，
# 最终决定权仍然在用户手里。结果会存回该条目的 ai_suggestion 字段。
# Same OpenAI plumbing as the receipt feature (key from Finance/.env,
# strict JSON schema, image never stored, missing key -> 503 and the page
# still works). Given one shopping item plus the user's real finance
# context, the model recommends buy / wait / dont_buy. It's advice only —
# the user still decides. The result is stored back on the item's
# ai_suggestion field.

PURCHASE_ADVICE_TIMEOUT = 30  # seconds
PURCHASE_ADVICE_SCHEMA = {
    "type": "object",
    "properties": {
        "recommendation": {"type": "string", "enum": ["buy", "wait", "dont_buy"]},
        "reasoning": {"type": "string"},
        "suggested_wait_days": {"type": ["integer", "null"]},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": ["recommendation", "reasoning", "suggested_wait_days", "confidence"],
    "additionalProperties": False,
}


def _purchase_context(item):
    """The real finance figures the advisor sees alongside the item itself:
    this month's income/expense/balance, category spend + budget headroom,
    and how many other purchases are already lined up."""
    records = load_records()
    month = datetime.now().strftime("%Y-%m")
    month_records = [r for r in records if r.get("date", "").startswith(month)]

    income = round(sum(
        r.get("amount", 0) for r in month_records
        if r.get("type") == "income" and r.get("category") != "Transfer In"
    ), 2)
    expense = round(sum(
        r.get("amount", 0) for r in month_records
        if r.get("type") == "expense" and r.get("category") != "Transfer Out"
    ), 2)

    category = item.get("category") or ""
    category_spent_month = round(sum(
        r.get("amount", 0) for r in month_records
        if r.get("type") == "expense" and r.get("category") == category
    ), 2) if category else 0.0

    category_budget = None
    if category:
        for b in load_data(f_budget, []):
            if b.get("category") == category:
                period = b.get("period", "monthly")
                period_records = _get_period_records(records, period)
                spent = round(sum(
                    r.get("amount", 0) for r in period_records
                    if r.get("type") == "expense" and r.get("category") == category
                ), 2)
                limit = b.get("amount", 0)
                category_budget = {
                    "period": period,
                    "limit": limit,
                    "spent": spent,
                    "remaining": round(limit - spent, 2),
                }
                break

    others = [
        it for it in load_shopping()
        if it.get("id") != item.get("id")
        and it.get("status") == "open" and it.get("decision") == "buy"
    ]

    return {
        "month": month,
        "income_this_month": income,
        "expense_this_month": expense,
        "balance_this_month": round(income - expense, 2),
        "category_spent_this_month": category_spent_month,
        "category_budget": category_budget,
        "other_items_planned_to_buy": len(others),
        "other_planned_value": round(
            sum(float(o.get("estimated_price") or 0) for o in others), 2
        ),
    }


def _normalise_purchase_advice(result):
    recommendation = result.get("recommendation")
    if recommendation not in {"buy", "wait", "dont_buy"}:
        recommendation = "wait"

    reasoning = result.get("reasoning")
    reasoning = (reasoning.strip()[:600]
                 if isinstance(reasoning, str) and reasoning.strip()
                 else "No reasoning was returned.")

    wait_days = result.get("suggested_wait_days")
    try:
        wait_days = int(wait_days)
        if wait_days <= 0 or wait_days > 365:
            wait_days = None
    except (TypeError, ValueError):
        wait_days = None

    confidence = result.get("confidence")
    if confidence not in {"high", "medium", "low"}:
        confidence = "low"

    return {
        "recommendation": recommendation,
        "reasoning": reasoning,
        "suggested_wait_days": wait_days,
        "confidence": confidence,
        "generated_at": today_iso(),
    }


def _advise_purchase(item, context):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("AI purchase advice is not configured")

    try:
        from openai import OpenAI, AuthenticationError
    except ImportError as exc:
        raise RuntimeError("The OpenAI package is not installed") from exc

    payload = {
        "currency": "RM",
        "item": {
            "name": item.get("name"),
            "estimated_price": item.get("estimated_price"),
            "category": item.get("category") or None,
            "priority": item.get("priority"),
            "reasons_to_buy": item.get("reasons_for") or None,
            "reasons_not_to_buy": item.get("reasons_against") or None,
            "notes": item.get("notes") or None,
            "target_date": item.get("target_date") or None,
            "added_on": item.get("date_added"),
        },
        "finance_context": context,
    }

    prompt = (
        "You are a level-headed personal finance assistant helping the user "
        "avoid impulse spending. Using the shopping item and the user's real "
        "finance context below, recommend exactly one of: buy, wait, dont_buy.\n"
        "- Put needs above wants: a broken essential or a genuine study/work "
        "need is a strong reason to buy; \"I just want it\" is weak.\n"
        "- Weigh their budget headroom, this month's balance, and how many "
        "other purchases they have already lined up.\n"
        "- If you choose \"wait\", set suggested_wait_days (e.g. 7, 14, 30); "
        "otherwise set it to null.\n"
        "- reasoning: 2-3 short sentences spoken directly to the user, no "
        "preamble.\n\n"
        f"DATA:\n{json.dumps(payload, ensure_ascii=False)}"
    )

    client = OpenAI(api_key=api_key, timeout=PURCHASE_ADVICE_TIMEOUT)
    try:
        response = client.responses.create(
            model=os.environ.get("OPENAI_RECEIPT_MODEL", "gpt-4o-mini"),
            store=False,
            max_output_tokens=400,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "purchase_advice",
                    "strict": True,
                    "schema": PURCHASE_ADVICE_SCHEMA,
                },
            },
            input=[{
                "role": "user",
                "content": [{"type": "input_text", "text": prompt}],
            }],
        )
    except AuthenticationError as exc:
        raise RuntimeError("AI purchase advice is not configured correctly") from exc
    return _normalise_purchase_advice(_json_from_model_text(response.output_text))


@finance_bp.route("/shopping/advise", methods=["POST"])
def shopping_advise():
    """Return an AI buy / wait / don't-buy recommendation for one item, and
    store it on that item. JSON in, JSON out (no page reload)."""
    items = load_shopping()
    item = find_shopping_item(items, request.form.get("id"))
    if not item:
        return jsonify(error="That shopping item no longer exists."), 404
    if item.get("status") == "bought":
        return jsonify(error="This item has already been bought."), 400

    try:
        advice = _advise_purchase(item, _purchase_context(item))
    except RuntimeError as exc:
        return jsonify(error=str(exc)), 503
    except (ValueError, json.JSONDecodeError):
        return jsonify(error="The AI response could not be read. Please try again."), 422
    except Exception:
        return jsonify(error="AI advice is temporarily unavailable. Please try again shortly."), 502

    item["ai_suggestion"] = advice
    save_shopping(items)
    return jsonify(advice=advice)


# ================= RECURRING TRANSACTIONS =================
# ================= 定期交易 =================
# 一条规则到期时生成一条真正的 expenses.json 记录（带 recurring_id /
# source=recurring）。可以手动"记账"，也可以打开 auto_post 让它在打开
# Dashboard 时自动补记（会一次补齐落下的多个周期）。
# A rule generates a real expenses.json record when due (tagged with
# recurring_id / source=recurring). Post it by hand, or turn on auto_post
# and it materialises when you open the Dashboard (catching up every missed
# period in one go).

RECURRING_CATCHUP_CAP = 120  # stop runaway catch-up on a very stale rule


def _recurring_instance(rule, date_str):
    return {
        "id": new_id(),
        "date": date_str,
        "type": rule.get("type"),
        "category": rule.get("category"),
        "account": rule.get("account"),
        "item": rule.get("item") or rule.get("category") or "Recurring",
        "amount": round(float(rule.get("amount") or 0), 2),
        "receipt": None,
        "tags": list(rule.get("tags") or []),
        "recurring_id": rule.get("id"),
        "source": "recurring",
    }


def _post_due_recurring(only_rule_id=None, only_auto=False):
    """Materialise every due occurrence of the matching rule(s). Returns the
    number of records created."""
    rules = load_recurring()
    today = today_iso()
    records = None
    posted = 0
    rules_changed = False

    for rule in rules:
        if only_rule_id and rule.get("id") != only_rule_id:
            continue
        if not rule.get("active"):
            continue
        if only_auto and not rule.get("auto_post"):
            continue

        end = rule.get("end_date") or ""
        guard = 0
        while rule.get("next_due", "") and rule["next_due"] <= today and guard < RECURRING_CATCHUP_CAP:
            due = rule["next_due"]
            if end and due > end:
                break
            if records is None:
                records = load_records()
            records.append(_recurring_instance(rule, due))
            rule["last_posted"] = due
            rule["next_due"] = advance_date(due, rule.get("frequency", "monthly"), rule.get("interval", 1))
            posted += 1
            guard += 1
            rules_changed = True

        if end and rule.get("next_due", "") > end and rule.get("active"):
            rule["active"] = False
            rules_changed = True

    if records is not None:
        save_records(records)
    if rules_changed:
        save_recurring(rules)
    return posted


def _run_auto_recurring():
    """Called on Dashboard load — posts due occurrences of auto_post rules."""
    try:
        return _post_due_recurring(only_auto=True)
    except Exception:
        return 0


def _recurring_due_count():
    today = today_iso()
    return sum(
        1 for r in load_recurring()
        if r.get("active") and r.get("next_due", "") and r["next_due"] <= today
    )


def _recurring_fields_from_form(form):
    """Validate the shared create/update fields. Returns ``(data, error)``."""
    kind = form.get("type")
    if kind not in KINDS:
        return None, "Choose a valid transaction type (income, expense or saving)."

    category = (form.get("category") or "").strip()
    allowed = categories_by_kind().get(kind, [])
    if not category:
        return None, "Category is required."
    if allowed and category not in allowed:
        return None, f'"{category}" is not one of your {kind} categories.'

    account = (form.get("account") or "").strip()
    if not account or not any(a["name"] == account for a in load_data(f_accounts, [])):
        return None, "Choose an existing account (add one on the Accounts page first)."

    amount, error = _parse_money(form.get("amount"), "Amount", allow_zero=False)
    if error:
        return None, error

    frequency = form.get("frequency")
    if frequency not in RECURRING_FREQUENCIES:
        return None, "Choose a frequency (weekly, monthly or yearly)."

    try:
        interval = max(1, int(form.get("interval") or 1))
    except (TypeError, ValueError):
        return None, "Every-N must be a whole number."

    start_date = (form.get("start_date") or "").strip()
    try:
        datetime.strptime(start_date, "%Y-%m-%d")
    except ValueError:
        return None, "Start date is required (YYYY-MM-DD)."

    end_date = (form.get("end_date") or "").strip()
    if end_date:
        try:
            datetime.strptime(end_date, "%Y-%m-%d")
        except ValueError:
            return None, "End date must be YYYY-MM-DD."
        if end_date < start_date:
            return None, "End date can't be before the start date."

    return {
        "type": kind,
        "category": category,
        "account": account,
        "item": (form.get("item") or "").strip()[:160],
        "amount": amount,
        "tags": normalize_tags(form.get("tags")),
        "frequency": frequency,
        "interval": interval,
        "start_date": start_date,
        "end_date": end_date,
        "auto_post": form.get("auto_post") == "on",
    }, None


def _handle_recurring_action(form, rules):
    action = form.get("action", "create")

    if action == "create":
        data, error = _recurring_fields_from_form(form)
        if error:
            return error
        rules.append({
            "id": new_id(),
            "created_at": today_iso(),
            "active": True,
            "last_posted": "",
            "next_due": data["start_date"],
            **data,
        })
        save_recurring(rules)
        return None

    rule = find_recurring(rules, form.get("id"))
    if not rule:
        return "That recurring rule no longer exists."

    if action == "update":
        data, error = _recurring_fields_from_form(form)
        if error:
            return error
        # keep next_due unless the start date moved and nothing's been posted yet
        if not rule.get("last_posted") and data["start_date"] != rule.get("start_date"):
            rule["next_due"] = data["start_date"]
        rule.update(data)
        save_recurring(rules)
        return None

    if action == "pause":
        rule["active"] = False
        save_recurring(rules)
        return None

    if action == "resume":
        rule["active"] = True
        save_recurring(rules)
        return None

    if action == "skip":
        if rule.get("next_due"):
            rule["next_due"] = advance_date(
                rule["next_due"], rule.get("frequency", "monthly"), rule.get("interval", 1)
            )
            save_recurring(rules)
        return None

    if action == "delete":
        rules.remove(rule)
        save_recurring(rules)
        return None

    if action == "post":
        _post_due_recurring(only_rule_id=rule["id"])
        return None

    return "Unknown action."


def _render_recurring(rules, error=None):
    today = today_iso()
    rows = []
    for r in sorted(rules, key=lambda r: (not r.get("active"), r.get("next_due", ""))):
        rows.append({**r, "due_now": bool(r.get("active") and r.get("next_due", "") and r["next_due"] <= today)})
    return render_template(
        "recurring.html",
        rules=rows,
        due_count=sum(1 for r in rows if r["due_now"]),
        accounts=load_data(f_accounts, []),
        categories=categories_by_kind(),
        frequencies=RECURRING_FREQUENCIES,
        kinds=KINDS,
        today=today,
        error=error,
    )


@finance_bp.route("/recurring", methods=["GET", "POST"])
def recurring():
    rules = load_recurring()

    if request.method == "POST":
        if request.form.get("action") == "post_all":
            _post_due_recurring()
            return redirect(url_for("finance.recurring"))
        error = _handle_recurring_action(request.form, rules)
        if error:
            return _render_recurring(rules, error=error)
        return redirect(url_for("finance.recurring"))

    return _render_recurring(rules)


# ================= DEBTS / LOANS =================
# ================= 借贷 =================
# 独立台账。每条记录你欠别人 (owe) 或别人欠你 (owed)，加上一串还款/收款
# 事件。可选：记一笔还款时同时在 expenses.json 里生成对应交易。
# A standalone ledger — money you owe (owe) or are owed (owed), plus a list
# of payment events. Optionally, recording a payment also creates the
# matching transaction in expenses.json.

def _debt_view(debt):
    outstanding = debt_outstanding(debt)
    paid = debt_paid(debt)
    principal = round(float(debt.get("principal") or 0), 2)
    return {
        **debt,
        "paid": paid,
        "outstanding": max(0.0, outstanding),
        "overpaid": round(max(0.0, -outstanding), 2),
        "principal": principal,
        "percent_paid": min(100, round(paid / principal * 100)) if principal else 0,
        "settled": debt.get("status") == "settled" or outstanding <= 0,
    }


def _debt_fields_from_form(form):
    direction = form.get("direction")
    if direction not in DEBT_DIRECTIONS:
        return None, "Choose whether you owe this or are owed it."

    counterparty = (form.get("counterparty") or "").strip()
    if not counterparty:
        return None, "Who is it with? (name required)"

    principal, error = _parse_money(form.get("principal"), "Amount", allow_zero=False)
    if error:
        return None, error

    date_str = (form.get("date") or "").strip() or today_iso()
    due_date = (form.get("due_date") or "").strip()

    return {
        "direction": direction,
        "counterparty": counterparty[:120],
        "description": (form.get("description") or "").strip()[:200],
        "principal": principal,
        "date": date_str,
        "due_date": due_date,
        "account": (form.get("account") or "").strip(),
        "notes": (form.get("notes") or "").strip(),
    }, None


def _handle_debt_action(form, debts):
    action = form.get("action", "create")

    if action == "create":
        data, error = _debt_fields_from_form(form)
        if error:
            return error
        debts.append({
            "id": new_id(), "created_at": today_iso(),
            "status": "open", "payments": [], **data,
        })
        save_debts(debts)
        return None

    debt = find_debt(debts, form.get("id"))
    if not debt:
        return "That debt no longer exists."

    if action == "update":
        data, error = _debt_fields_from_form(form)
        if error:
            return error
        debt.update(data)
        save_debts(debts)
        return None

    if action == "add_payment":
        amount, error = _parse_money(form.get("amount"), "Payment", allow_zero=False)
        if error:
            return error
        pay_date = (form.get("date") or "").strip() or today_iso()
        payment = {
            "id": new_id(),
            "date": pay_date,
            "amount": amount,
            "note": (form.get("note") or "").strip()[:200],
            "record_id": "",
        }

        # Optionally mirror the payment into the transaction ledger.
        if form.get("as_transaction") == "on":
            account = (form.get("txn_account") or debt.get("account") or "").strip()
            category = (form.get("txn_category") or "").strip()
            kind = "expense" if debt["direction"] == "owe" else "income"
            allowed = categories_by_kind().get(kind, [])
            if not account or not any(a["name"] == account for a in load_data(f_accounts, [])):
                return "Choose an account for the mirrored transaction."
            if allowed and category and category not in allowed:
                return f'"{category}" is not one of your {kind} categories.'
            if not category:
                category = allowed[0] if allowed else "Other"
            records = load_records()
            txn = {
                "id": new_id(), "date": pay_date, "type": kind,
                "category": category, "account": account,
                "item": f"{'Repayment to' if kind == 'expense' else 'Repayment from'} {debt['counterparty']}",
                "amount": amount, "receipt": None, "tags": [],
                "source": "debt", "debt_id": debt["id"],
            }
            records.append(txn)
            save_records(records)
            payment["record_id"] = txn["id"]

        debt.setdefault("payments", []).append(payment)
        if debt_outstanding(debt) <= 0:
            debt["status"] = "settled"
        save_debts(debts)
        return None

    if action == "delete_payment":
        pid = form.get("payment_id")
        debt["payments"] = [p for p in debt.get("payments", []) if p.get("id") != pid]
        if debt.get("status") == "settled" and debt_outstanding(debt) > 0:
            debt["status"] = "open"
        save_debts(debts)
        return None

    if action == "settle":
        debt["status"] = "settled"
        save_debts(debts)
        return None

    if action == "reopen":
        debt["status"] = "open"
        save_debts(debts)
        return None

    if action == "delete":
        debts.remove(debt)
        save_debts(debts)
        return None

    return "Unknown action."


def _render_debts(debts, error=None):
    rows = [_debt_view(d) for d in debts]
    rows.sort(key=lambda d: (d["settled"], d.get("due_date") or "9999", d.get("date", "")))

    owe = [d for d in rows if d["direction"] == "owe" and not d["settled"]]
    owed = [d for d in rows if d["direction"] == "owed" and not d["settled"]]
    totals = {
        "you_owe": round(sum(d["outstanding"] for d in owe), 2),
        "owed_to_you": round(sum(d["outstanding"] for d in owed), 2),
    }
    return render_template(
        "debts.html",
        debts=rows,
        totals=totals,
        accounts=load_data(f_accounts, []),
        categories=categories_by_kind(),
        directions=DEBT_DIRECTIONS,
        today=today_iso(),
        error=error,
    )


@finance_bp.route("/debts", methods=["GET", "POST"])
def debts():
    debt_list = load_debts()
    if request.method == "POST":
        error = _handle_debt_action(request.form, debt_list)
        if error:
            return _render_debts(debt_list, error=error)
        return redirect(url_for("finance.debts"))
    return _render_debts(debt_list)


# ================= NET WORTH =================
# ================= 净资产 =================
# 快照 = 某个时间点的 assets/liabilities/net。"立即快照"由 App 数据自动
# 算：所有账户余额 + 别人欠你的 − 你欠别人的。也可手动加一条。
# A snapshot captures assets/liabilities/net at a moment. "Snapshot now"
# auto-computes it from app data (all account balances + money owed to you
# − money you owe). Manual entries are also allowed.

def _account_balances(records, accounts):
    out = {}
    for acc in accounts:
        name = acc["name"]
        bal = 0.0
        for r in records:
            if r.get("account") != name:
                continue
            if r.get("type") in ("income", "saving"):
                bal += r.get("amount", 0)
            elif r.get("type") == "expense":
                bal -= r.get("amount", 0)
        out[name] = round(bal, 2)
    return out


def _computed_net_worth():
    records = load_records()
    accounts = load_data(f_accounts, [])
    balances = _account_balances(records, accounts)

    liquid = round(sum(balances.values()), 2)
    debts = [_debt_view(d) for d in load_debts()]
    receivable = round(sum(d["outstanding"] for d in debts
                           if d["direction"] == "owed" and not d["settled"]), 2)
    owed = round(sum(d["outstanding"] for d in debts
                     if d["direction"] == "owe" and not d["settled"]), 2)

    assets = round(liquid + receivable, 2)
    liabilities = owed
    return {
        "assets": assets,
        "liabilities": liabilities,
        "net": round(assets - liabilities, 2),
        "breakdown": {
            "account_balances": balances,
            "liquid": liquid,
            "receivable": receivable,
            "owed": owed,
        },
    }


@finance_bp.route("/networth", methods=["GET", "POST"])
def networth():
    snapshots = load_networth()
    computed = _computed_net_worth()

    if request.method == "POST":
        action = request.form.get("action", "snapshot")

        if action == "snapshot":
            snapshots.append({
                "id": new_id(),
                "date": today_iso(),
                "assets": computed["assets"],
                "liabilities": computed["liabilities"],
                "net": computed["net"],
                "note": (request.form.get("note") or "").strip()[:200],
                "auto": True,
                "breakdown": computed["breakdown"],
            })
            save_networth(snapshots)
            return redirect(url_for("finance.networth"))

        if action == "add":
            assets, e1 = _parse_money(request.form.get("assets"), "Assets")
            liabilities, e2 = _parse_money(request.form.get("liabilities"), "Liabilities")
            error = e1 or e2
            date_str = (request.form.get("date") or "").strip() or today_iso()
            if error:
                return render_template("networth.html", snapshots=_networth_rows(snapshots),
                                       computed=computed, today=today_iso(), error=error)
            snapshots.append({
                "id": new_id(), "date": date_str,
                "assets": assets, "liabilities": liabilities,
                "net": round(assets - liabilities, 2),
                "note": (request.form.get("note") or "").strip()[:200],
                "auto": False, "breakdown": {},
            })
            save_networth(snapshots)
            return redirect(url_for("finance.networth"))

        if action == "delete":
            snap = find_snapshot(snapshots, request.form.get("id"))
            if snap:
                snapshots.remove(snap)
                save_networth(snapshots)
            return redirect(url_for("finance.networth"))

    return render_template("networth.html", snapshots=_networth_rows(snapshots),
                           computed=computed, today=today_iso())


def _networth_rows(snapshots):
    rows = sorted(snapshots, key=lambda s: s.get("date", ""))
    peak = max((s.get("net", 0) for s in rows), default=0)
    low = min((s.get("net", 0) for s in rows), default=0)
    span = (peak - low) or 1
    out = []
    prev = None
    for s in rows:
        net = s.get("net", 0)
        out.append({
            **s,
            "change": None if prev is None else round(net - prev, 2),
            "bar_pct": round((net - low) / span * 100),
        })
        prev = net
    out.reverse()  # newest first for the table
    return out


# ================= TRASH (soft-deleted records) =================
# ================= 回收站（软删除的记录） =================

@finance_bp.route("/trash")
def trash():
    deleted = [r for r in load_records(include_deleted=True) if r.get("deleted_at")]
    deleted.sort(key=lambda r: r.get("deleted_at", ""), reverse=True)
    return render_template("trash.html", records=deleted)


@finance_bp.route("/trash/<rid>/restore", methods=["POST"])
def trash_restore(rid):
    records = load_records(include_deleted=True)
    record = _find_record(records, rid)
    if record and record.get("deleted_at"):
        record.pop("deleted_at", None)
        save_records(records)
    return redirect(url_for("finance.trash"))


@finance_bp.route("/trash/<rid>/purge", methods=["POST"])
def trash_purge(rid):
    records = load_records(include_deleted=True)
    record = _find_record(records, rid)
    if record and record.get("deleted_at"):
        _delete_receipt(record.get("receipt"))
        records.remove(record)
        save_records(records)
    return redirect(url_for("finance.trash"))


@finance_bp.route("/trash/empty", methods=["POST"])
def trash_empty():
    records = load_records(include_deleted=True)
    kept = []
    for r in records:
        if r.get("deleted_at"):
            _delete_receipt(r.get("receipt"))
        else:
            kept.append(r)
    save_records(kept)
    return redirect(url_for("finance.trash"))


# ================= DATA: EXPORT / BACKUP / RESTORE =================
# ================= 数据：导出 / 备份 / 恢复 =================

CSV_COLUMNS = ["id", "date", "type", "category", "account", "item", "amount",
               "tags", "receipt", "goal_id", "source", "recurring_id",
               "split_id", "split_label", "deleted_at"]


def _data_counts():
    records_all = load_records(include_deleted=True)
    return {
        "records": sum(1 for r in records_all if not r.get("deleted_at")),
        "trashed": sum(1 for r in records_all if r.get("deleted_at")),
        "accounts": len(load_data(f_accounts, [])),
        "budgets": len(load_data(f_budget, [])),
        "goals": len(load_data(f_goals, [])),
        "categories": len(load_categories()),
        "shopping": len(load_shopping()),
        "recurring": len(load_recurring()),
        "debts": len(load_debts()),
        "currencies": len(load_rates()),
    }


@finance_bp.route("/data")
def data_tools():
    return render_template("data_tools.html", counts=_data_counts(),
                           import_result=None)


@finance_bp.route("/data/import.csv", methods=["POST"])
def data_import_csv():
    """Append transaction records from an uploaded CSV.

    Required columns: date, type, amount. Optional: category, account, item,
    tags. Each row is validated; bad rows are skipped and reported.
    """
    upload = request.files.get("csvfile")
    if not upload or not upload.filename:
        return render_template("data_tools.html", counts=_data_counts(),
                               import_result={"error": "Choose a CSV file first."}), 400

    try:
        text = upload.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        return render_template("data_tools.html", counts=_data_counts(),
                               import_result={"error": "The file isn't UTF-8 text."}), 400

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or "date" not in reader.fieldnames:
        return render_template("data_tools.html", counts=_data_counts(),
                               import_result={"error": "CSV needs a header row with at least: date, type, amount."}), 400

    records = load_records()
    imported, skipped = 0, []
    for line_no, row in enumerate(reader, start=2):
        date_str = (row.get("date") or "").strip()
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            skipped.append(f"row {line_no}: bad date {date_str!r}")
            continue

        kind = (row.get("type") or "").strip().lower()
        if kind not in KINDS:
            skipped.append(f"row {line_no}: type must be income/expense/saving, got {kind!r}")
            continue

        amount, error = _parse_money(row.get("amount"), "amount", allow_zero=False)
        if error:
            skipped.append(f"row {line_no}: {error}")
            continue

        records.append({
            "id": new_id(),
            "date": date_str,
            "type": kind,
            "category": (row.get("category") or "").strip() or "Other",
            "account": (row.get("account") or "").strip() or "Imported",
            "item": (row.get("item") or "").strip(),
            "amount": amount,
            "receipt": None,
            "tags": normalize_tags(row.get("tags")),
            "source": "import",
        })
        imported += 1

    if imported:
        save_records(records)

    return render_template("data_tools.html", counts=_data_counts(),
                           import_result={"imported": imported, "skipped": skipped})


@finance_bp.route("/data/export.csv")
def data_export_csv():
    """All transaction records (including trashed, flagged in deleted_at) as CSV."""
    include_trashed = request.args.get("trashed") == "1"
    records = load_records(include_deleted=include_trashed)

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for r in sorted(records, key=lambda r: r.get("date", "")):
        row = dict(r)
        if isinstance(row.get("tags"), list):
            row["tags"] = ";".join(row["tags"])
        writer.writerow(row)

    stamp = datetime.now().strftime("%Y%m%d")
    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="finance-records-{stamp}.csv"'},
    )


@finance_bp.route("/data/backup.zip")
def data_backup():
    """Zip of every JSON store — the whole dataset, restorable via /data/restore."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, path in _store_paths().items():
            if os.path.exists(path):
                zf.write(path, arcname=name)
    buffer.seek(0)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Response(
        buffer.getvalue(),
        mimetype="application/zip",
        headers={"Content-Disposition": f'attachment; filename="finance-backup-{stamp}.zip"'},
    )


@finance_bp.route("/data/restore", methods=["POST"])
def data_restore():
    """Replace the JSON stores from an uploaded backup zip. Every file in the
    zip is validated as JSON before anything is written."""
    upload = request.files.get("backup")
    if not upload or not upload.filename:
        return render_template("data_tools.html", counts=_data_counts(),
                               error="Choose a backup .zip file first."), 400

    known = _store_paths()
    try:
        with zipfile.ZipFile(upload.stream) as zf:
            staged = {}
            for name in zf.namelist():
                base = os.path.basename(name)
                if base not in known:
                    continue
                try:
                    staged[base] = json.loads(zf.read(name).decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    return render_template("data_tools.html", counts=_data_counts(),
                                           error=f"{base} in the backup is not valid JSON."), 400
    except zipfile.BadZipFile:
        return render_template("data_tools.html", counts=_data_counts(),
                               error="That file is not a valid .zip backup."), 400

    if not staged:
        return render_template("data_tools.html", counts=_data_counts(),
                               error="The zip contained no recognised Finance data files."), 400

    for base, parsed in staged.items():
        save_data(known[base], parsed)

    return redirect(url_for("finance.data_tools", restored=len(staged)))


# ================= AI: CATEGORISE / REVIEW / AFFORD / ANOMALIES =================
# ================= AI：自动分类 / 月度回顾 / 负担得起吗 / 异常检测 =================
# 前三个复用 OpenAI（key 来自 Finance/.env，没配就返回 503，页面照常）；
# 异常检测是纯统计（免费、常开）。
# The first three reuse OpenAI (key from Finance/.env, 503 when unconfigured,
# page still works). Anomaly detection is pure statistics — free, always on.

_WORD_RE = re.compile(r"[a-z0-9]+")


def _local_category_guess(item_text, kind):
    """Guess a category from the user's own past records that share words with
    this item description — free, no API call."""
    words = set(_WORD_RE.findall((item_text or "").lower()))
    if not words:
        return None
    allowed = set(categories_by_kind().get(kind, []))
    if not allowed:
        return None
    scores = {}
    for r in load_records(include_deleted=True):
        if r.get("type") != kind or r.get("category") not in allowed:
            continue
        overlap = words & set(_WORD_RE.findall((r.get("item") or "").lower()))
        if overlap:
            scores[r["category"]] = scores.get(r["category"], 0) + len(overlap)
    return max(scores, key=scores.get) if scores else None


@finance_bp.route("/suggest-category", methods=["POST"])
def suggest_category():
    item = (request.form.get("item") or "").strip()
    kind = request.form.get("type")
    if kind not in KINDS:
        return jsonify(error="Choose a transaction type first."), 400
    if not item:
        return jsonify(error="Type an item description first."), 400

    allowed = categories_by_kind().get(kind, [])
    if not allowed:
        return jsonify(error=f"You have no {kind} categories yet."), 400

    local = _local_category_guess(item, kind)
    if local:
        return jsonify(category=local, source="history", confidence="medium")

    schema = {
        "type": "object",
        "properties": {
            "category": {"type": ["string", "null"], "enum": [*allowed, None]},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        },
        "required": ["category", "confidence"],
        "additionalProperties": False,
    }
    prompt = (f"Choose the single best category for this {kind} transaction, "
              f"only from this list: {', '.join(allowed)}.\n"
              f"Item description: {item!r}\n"
              f"If none fit, return null.")
    try:
        result = _openai_structured(prompt, schema, "category_pick", 80)
    except RuntimeError as exc:
        return jsonify(error=str(exc)), 503
    except (ValueError, json.JSONDecodeError):
        return jsonify(error="The AI response could not be read."), 422
    except Exception:
        return jsonify(error="AI is temporarily unavailable."), 502

    category = result.get("category")
    if category not in allowed:
        return jsonify(category=None, source="none", confidence="low")
    return jsonify(category=category, source="ai",
                   confidence=result.get("confidence", "low"))


def _month_digest(records, year, month):
    key = f"{year}-{month}"
    prev = datetime.strptime(key + "-01", "%Y-%m-%d").date()
    prev = (prev.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")

    def _sum(period_key, kind, exclude):
        return round(sum(r.get("amount", 0) for r in records
                         if r.get("date", "").startswith(period_key)
                         and r.get("type") == kind and r.get("category") != exclude), 2)

    month_records = [r for r in records if r.get("date", "").startswith(key)]
    by_cat = {}
    for r in month_records:
        if r.get("type") == "expense" and r.get("category") != "Transfer Out":
            by_cat[r.get("category")] = round(by_cat.get(r.get("category"), 0) + r.get("amount", 0), 2)

    income = _sum(key, "income", "Transfer In")
    expense = _sum(key, "expense", "Transfer Out")
    budgets = load_data(f_budget, [])
    budget_lines = []
    for b in budgets:
        v = _budget_view(b, records)
        budget_lines.append({"category": v["category"], "spent": v["spent"],
                             "limit": v["effective_limit"], "status": v["status"]})

    return {
        "month": key,
        "income": income,
        "expense": expense,
        "balance": round(income - expense, 2),
        "prev_month_expense": _sum(prev, "expense", "Transfer Out"),
        "by_category": dict(sorted(by_cat.items(), key=lambda kv: kv[1], reverse=True)),
        "budgets": budget_lines,
        "transaction_count": len(month_records),
    }


@finance_bp.route("/summary/review", methods=["POST"])
def summary_review():
    now = datetime.now()
    month = request.form.get("month") or now.strftime("%m")
    year = request.form.get("year") or now.strftime("%Y")
    key = f"{year}-{month}"

    digest = _month_digest(load_records(), year, month)
    if digest["transaction_count"] == 0:
        return jsonify(error="No records for that month yet."), 400

    schema = {
        "type": "object",
        "properties": {
            "narrative": {"type": "string"},
            "suggestions": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
        },
        "required": ["narrative", "suggestions"],
        "additionalProperties": False,
    }
    prompt = (
        "You are a concise personal finance coach. Write a 2-4 sentence review "
        "of this month spoken directly to the user (no preamble), then 1-3 short, "
        "concrete suggestions. Currency is RM.\n\n"
        f"DATA:\n{json.dumps(digest, ensure_ascii=False)}"
    )
    try:
        result = _openai_structured(prompt, schema, "monthly_review", 400)
    except RuntimeError as exc:
        return jsonify(error=str(exc)), 503
    except (ValueError, json.JSONDecodeError):
        return jsonify(error="The AI response could not be read."), 422
    except Exception:
        return jsonify(error="AI is temporarily unavailable."), 502

    review = {
        "narrative": (result.get("narrative") or "").strip()[:1200],
        "suggestions": [s.strip()[:200] for s in (result.get("suggestions") or []) if s.strip()][:3],
        "generated_at": today_iso(),
    }
    reviews = load_insights()
    reviews[key] = review
    save_insights(reviews)
    return jsonify(review=review, month=key)


def _afford_context(amount, category):
    records = load_records()
    accounts = load_data(f_accounts, [])
    now = datetime.now()
    key = now.strftime("%Y-%m")
    month_records = [r for r in records if r.get("date", "").startswith(key)]

    income = round(sum(r.get("amount", 0) for r in month_records
                       if r.get("type") == "income" and r.get("category") != "Transfer In"), 2)
    expense = round(sum(r.get("amount", 0) for r in month_records
                        if r.get("type") == "expense" and r.get("category") != "Transfer Out"), 2)

    last_day = calendar.monthrange(now.year, now.month)[1]
    days_left = last_day - now.day

    recurring_before_eom = 0.0
    eom = f"{now.year}-{now.month:02d}-{last_day:02d}"
    for rule in load_recurring():
        if rule.get("active") and rule.get("type") == "expense":
            due = rule.get("next_due", "")
            if due and due <= eom:
                recurring_before_eom += float(rule.get("amount") or 0)

    savings = 0.0
    savings_accounts = [a["name"] for a in accounts if a.get("purpose") == "savings"]
    for r in records:
        if r.get("account") in savings_accounts:
            if r.get("type") in ("income", "saving"):
                savings += r.get("amount", 0)
            elif r.get("type") == "expense":
                savings -= r.get("amount", 0)

    debts_owed = round(sum(debt_outstanding(d) for d in load_debts()
                           if d.get("direction") == "owe" and d.get("status") != "settled"), 2)

    ctx = {
        "purchase_amount": amount,
        "category": category or None,
        "currency": "RM",
        "this_month_income": income,
        "this_month_expense": expense,
        "this_month_balance": round(income - expense, 2),
        "days_left_in_month": days_left,
        "recurring_expenses_still_due_this_month": round(recurring_before_eom, 2),
        "savings_balance": round(savings, 2),
        "debts_you_owe": debts_owed,
    }
    if category:
        b = next((x for x in load_data(f_budget, []) if x.get("category") == category), None)
        if b:
            v = _budget_view(b, records)
            ctx["category_budget_remaining"] = v["remaining"]
    return ctx


@finance_bp.route("/afford", methods=["POST"])
def afford():
    amount, error = _parse_money(request.form.get("amount"), "Amount", allow_zero=False)
    if error:
        return jsonify(error=error), 400
    category = (request.form.get("category") or "").strip()
    note = (request.form.get("note") or "").strip()[:160]

    context = _afford_context(amount, category)
    schema = {
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": ["yes", "tight", "no"]},
            "reasoning": {"type": "string"},
        },
        "required": ["verdict", "reasoning"],
        "additionalProperties": False,
    }
    prompt = (
        "Can the user afford this purchase without straining their month? "
        "Answer yes / tight / no with 2-3 sentences spoken to the user (no "
        "preamble). Weigh their remaining balance, recurring expenses still due, "
        "and savings. Treat dipping into savings as 'tight' at best.\n"
        f"{('Purpose: ' + note) if note else ''}\n\n"
        f"DATA:\n{json.dumps(context, ensure_ascii=False)}"
    )
    try:
        result = _openai_structured(prompt, schema, "afford_check", 250)
    except RuntimeError as exc:
        return jsonify(error=str(exc)), 503
    except (ValueError, json.JSONDecodeError):
        return jsonify(error="The AI response could not be read."), 422
    except Exception:
        return jsonify(error="AI is temporarily unavailable."), 502

    verdict = result.get("verdict")
    if verdict not in ("yes", "tight", "no"):
        verdict = "tight"
    return jsonify(verdict=verdict,
                   reasoning=(result.get("reasoning") or "").strip()[:800],
                   context=context)


def _spending_anomalies(records):
    """Flag recent expenses that are far above the usual amount for their
    category. Pure statistics — no API call."""
    cutoff = (datetime.now().date() - timedelta(days=45)).isoformat()
    by_cat = {}
    for r in records:
        if r.get("type") != "expense" or r.get("category") in ("Transfer Out", "Goal Savings"):
            continue
        by_cat.setdefault(r.get("category"), []).append(r)

    flags = []
    for category, rows in by_cat.items():
        amounts = [r.get("amount", 0) for r in rows if r.get("amount", 0) > 0]
        if len(amounts) < 4:
            continue
        typical = statistics.median(amounts)
        if typical <= 0:
            continue
        for r in rows:
            amount = r.get("amount", 0)
            if r.get("date", "") < cutoff or amount <= 0:
                continue
            ratio = amount / typical
            if ratio >= 3 and (amount - typical) >= 50:
                flags.append({
                    "id": r.get("id"),
                    "date": r.get("date"),
                    "category": category,
                    "item": r.get("item") or "—",
                    "amount": round(amount, 2),
                    "typical": round(typical, 2),
                    "ratio": round(ratio, 1),
                })
    flags.sort(key=lambda f: f["ratio"], reverse=True)
    return flags[:8]


# ================= NEXT-MONTH INCOME FORECAST =================
# ================= 下月收入预估 =================
# 兼职/散工收入每月不固定 —— 用过去几个月的收入历史预估下个月能拿多少。
# 先算一个纯统计的基线（近月加权平均 + 中位数），有 OpenAI key 就让模型
# 结合趋势/波动/定期收入再细化成一个区间。结果缓存在 insights.json。
# Part-time / gig income varies month to month — this estimates next month
# from the last few months of income. A pure-statistics baseline first
# (recent weighted average + median), then, if a key is set, the model
# refines it into a range using the trend / variability / recurring income.
# Cached in insights.json under "forecast-<YYYY-MM>".

def _next_month_key(ref=None):
    ref = ref or datetime.now()
    if ref.month == 12:
        return f"{ref.year + 1}-01"
    return f"{ref.year}-{ref.month + 1:02d}"


def _month_keys_back(n):
    now = datetime.now()
    year, month, keys = now.year, now.month, []
    for _ in range(n):
        keys.append(f"{year}-{month:02d}")
        month -= 1
        if month == 0:
            month, year = 12, year - 1
    return list(reversed(keys))


def _income_history(records, months=9):
    keys = _month_keys_back(months)
    current = keys[-1]
    buckets = {k: {"month": k, "total": 0.0, "count": 0, "by_category": {}} for k in keys}

    for r in records:
        if r.get("type") != "income" or r.get("category") == "Transfer In":
            continue
        if r.get("source") == "debt":  # a loan repaid to you isn't salary
            continue
        mk = (r.get("date") or "")[:7]
        if mk not in buckets:
            continue
        amount = r.get("amount", 0) or 0
        b = buckets[mk]
        b["total"] = round(b["total"] + amount, 2)
        b["count"] += 1
        cat = r.get("category") or "Other"
        b["by_category"][cat] = round(b["by_category"].get(cat, 0) + amount, 2)

    rows = []
    for k in keys:
        row = buckets[k]
        row["complete"] = k != current
        rows.append(row)

    recurring = [
        {
            "item": x.get("item") or x.get("category"),
            "amount": round(float(x.get("amount") or 0), 2),
            "frequency": x.get("frequency"),
            "next_due": x.get("next_due"),
        }
        for x in load_recurring()
        if x.get("active") and x.get("type") == "income"
    ]

    return {
        "currency": "RM",
        "target_month": _next_month_key(),
        "current_month": current,
        "months": rows,
        "recurring_income": recurring,
    }


def _baseline_income_forecast(history):
    """Statistical estimate from completed months. Returns None until there
    are at least 2 completed months *since the first month with income* (so
    empty pre-tracking months don't drag the estimate to zero, but a genuine
    zero-earning month in the middle still counts)."""
    months = history["months"]
    first = next((i for i, m in enumerate(months) if m["count"] > 0), None)
    if first is None:
        return None
    usable = [m["total"] for m in months[first:] if m["complete"]]
    if len(usable) < 2:
        return None
    recent = usable[-6:]
    weights = list(range(1, len(recent) + 1))  # newer months weigh more
    weighted = sum(v * w for v, w in zip(recent, weights)) / sum(weights)
    median = statistics.median(recent)
    return {
        "estimate": round((weighted + median) / 2, 2),
        "low": round(min(recent), 2),
        "high": round(max(recent), 2),
        "months_used": len(recent),
        "recent_totals": [round(v, 2) for v in recent],
    }


@finance_bp.route("/forecast-income", methods=["POST"])
def forecast_income():
    history = _income_history(load_records())
    baseline = _baseline_income_forecast(history)
    if baseline is None:
        return jsonify(error="Not enough income history yet — record income across "
                             "at least two completed months first."), 400

    target = history["target_month"]
    totals_str = ", ".join(f"RM {v:.2f}" for v in baseline["recent_totals"])
    forecast = {
        "target": target,
        "estimate": baseline["estimate"],
        "low": baseline["low"],
        "high": baseline["high"],
        "confidence": "low",
        "reasoning": f"Statistical estimate from your last {baseline['months_used']} "
                     f"months of income ({totals_str}).",
        "source": "baseline",
        "generated_at": today_iso(),
    }

    if os.environ.get("OPENAI_API_KEY"):
        schema = {
            "type": "object",
            "properties": {
                "estimate": {"type": "number"},
                "low": {"type": "number"},
                "high": {"type": "number"},
                "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                "reasoning": {"type": "string"},
            },
            "required": ["estimate", "low", "high", "confidence", "reasoning"],
            "additionalProperties": False,
        }
        prompt = (
            f"Estimate the user's total income for {target}. They may work part-time, "
            "so income varies month to month. Give a point estimate, a low-high range, "
            "and 1-2 sentences of reasoning spoken to the user (no preamble). Weigh the "
            "recent trend, the month-to-month variability, and any recurring income. "
            "Currency is RM. Do not assume a raise or extra shifts without evidence.\n"
            f"Statistical baseline: {json.dumps(baseline)}\n\n"
            f"DATA:\n{json.dumps(history, ensure_ascii=False)}"
        )
        try:
            result = _openai_structured(prompt, schema, "income_forecast", 300)
            est = max(0.0, round(float(result["estimate"]), 2))
            lo = max(0.0, round(float(result["low"]), 2))
            hi = max(0.0, round(float(result["high"]), 2))
            if lo > hi:
                lo, hi = hi, lo
            forecast.update({
                "estimate": est, "low": lo, "high": hi,
                "confidence": result.get("confidence") if result.get("confidence") in ("high", "medium", "low") else "low",
                "reasoning": (result.get("reasoning") or "").strip()[:600] or forecast["reasoning"],
                "source": "ai",
            })
        except Exception:
            forecast["reasoning"] += " (AI refinement unavailable — showing the statistical estimate.)"

    insights = load_insights()
    insights[f"forecast-{target}"] = forecast
    save_insights(insights)
    return jsonify(forecast=forecast)


# ================= CURRENCY CONVERTER =================
# ================= 货币换算器 =================
# 独立小工具。RM 为本位币，每种外币存 rate_to_myr（1 单位 = 多少 RM），
# 用户自行维护。换算：金额 * rate[from] / rate[to]（MYR 的 rate = 1）。
# Standalone tool. RM is the base; each foreign currency stores rate_to_myr
# (1 unit = how many RM), user-maintained. Convert: amount * rate[from] /
# rate[to] (MYR's rate is 1).

def _rate_of(code, rates):
    if code in (BASE_CURRENCY, "MYR", "RM"):
        return 1.0
    row = find_rate(rates, code)
    try:
        value = float(row["rate_to_myr"]) if row else None
    except (TypeError, ValueError):
        return None
    return value if value and value > 0 else None


def _parse_rate(raw):
    """Exchange rate — a positive float, kept to full precision (not rounded
    to 2dp like money, so tiny rates like JPY/IDR survive)."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None, "Rate must be a number."
    if not math.isfinite(value) or value <= 0:
        return None, "Rate must be greater than 0."
    return round(value, 8), None


def _handle_rate_action(form, rates):
    action = form.get("action")

    if action == "add_rate":
        code = clean_currency_code(form.get("code"))
        if not code or len(code) < 2:
            return "Enter a currency code (2-5 letters, e.g. USD)."
        if code in (BASE_CURRENCY, "MYR"):
            return "MYR is the base currency and is always 1."
        if find_rate(rates, code):
            return f"{code} is already in the list — edit it instead."
        rate, error = _parse_rate(form.get("rate_to_myr"))
        if error:
            return error
        rates.append({
            "code": code,
            "name": (form.get("name") or code).strip()[:60],
            "rate_to_myr": rate,
            "updated_at": today_iso(),
        })
        save_rates(rates)
        return None

    row = find_rate(rates, clean_currency_code(form.get("code")))
    if not row:
        return "That currency is not in the list."

    if action == "update_rate":
        rate, error = _parse_rate(form.get("rate_to_myr"))
        if error:
            return error
        row["rate_to_myr"] = rate
        if form.get("name"):
            row["name"] = form.get("name").strip()[:60]
        row["updated_at"] = today_iso()
        save_rates(rates)
        return None

    if action == "delete_rate":
        rates.remove(row)
        save_rates(rates)
        return None

    return "Unknown action."


def _render_currency(rates, error=None):
    rates = sorted(rates, key=lambda r: r.get("code", ""))
    codes = [BASE_CURRENCY] + [r["code"] for r in rates]

    args = request.args
    result = None
    src = clean_currency_code(args.get("from")) or "USD"
    dst = clean_currency_code(args.get("to")) or BASE_CURRENCY
    amount_raw = args.get("amount")

    if amount_raw:
        amount, amount_error = _parse_money(amount_raw, "Amount")
        rate_from = _rate_of(src, rates)
        rate_to = _rate_of(dst, rates)
        if amount_error:
            result = {"error": amount_error}
        elif rate_from is None or rate_to is None:
            result = {"error": "One of those currencies has no rate set."}
        else:
            in_myr = amount * rate_from
            result = {
                "amount": amount,
                "from": src,
                "to": dst,
                "converted": round(in_myr / rate_to, 4),
                "in_myr": round(in_myr, 2),
                "unit_rate": round(rate_from / rate_to, 6),
            }

    return render_template(
        "currency.html",
        rates=rates,
        codes=codes,
        base=BASE_CURRENCY,
        sel_from=src,
        sel_to=dst,
        amount=amount_raw or "",
        result=result,
        error=error,
    )


@finance_bp.route("/currency", methods=["GET", "POST"])
def currency():
    rates = load_rates()
    if request.method == "POST":
        error = _handle_rate_action(request.form, rates)
        if error:
            return _render_currency(rates, error=error)
        return redirect(url_for("finance.currency"))
    return _render_currency(rates)


# ================= FINANCE HOME =================
# ================= 财务首页 =================

@finance_bp.route("/finance")
def finance_home():
    _run_auto_recurring()  # materialise any due auto-post recurring transactions

    records = load_records()
    budgets = load_data(f_budget, [])
    goals_list = load_data(f_goals, [])
    accounts = load_data(f_accounts, [])
    recurring_due = _recurring_due_count()

    _debts = [_debt_view(d) for d in load_debts()]
    debt_summary = {
        "you_owe": round(sum(d["outstanding"] for d in _debts
                             if d["direction"] == "owe" and not d["settled"]), 2),
        "owed_to_you": round(sum(d["outstanding"] for d in _debts
                                 if d["direction"] == "owed" and not d["settled"]), 2),
    }
    net_worth = _computed_net_worth()["net"]
    anomalies = _spending_anomalies(records)

    now = datetime.now()
    current_month = now.strftime("%Y-%m")

    month_records = [r for r in records if r.get("date", "").startswith(current_month)]

    income = sum(r.get("amount", 0) for r in month_records if r.get("type") == "income" and r.get("category") != "Transfer In")
    expense = sum(r.get("amount", 0) for r in month_records if r.get("type") == "expense" and r.get("category") != "Transfer Out")

    savings_accounts = [a["name"] for a in accounts if a.get("purpose") == "savings"]

    saving = 0
    for acc in savings_accounts:
        balance_acc = 0
        for r in records:
            if r.get("account") != acc:
                continue
            balance_acc += r.get("amount", 0) if r.get("type") in ("income", "saving") else -r.get("amount", 0)
        saving += balance_acc

    spending_accounts = [a["name"] for a in accounts if a.get("purpose") == "spending"]
    balance = 0
    for r in records:
        if r.get("account") not in spending_accounts:
            continue
        balance += r.get("amount", 0) if r.get("type") == "income" else -r.get("amount", 0)

    recent_records = sorted(records, key=lambda x: x.get("date", ""), reverse=True)[:5]

    category_totals = {}
    for r in month_records:
        if r.get("type") == "expense" and r.get("category") != "Transfer Out":
            cat = r.get("category", "Other")
            category_totals[cat] = category_totals.get(cat, 0) + r.get("amount", 0)

    # max(dict, key=dict.get)：在字典的 key（分类名）里找出对应 value
    # （花费金额）最大的那一个 key —— 也就是"这个月花最多钱的分类"。
    # 如果字典是空的（这个月完全没花钱），就用 None 代替，避免报错。
    # max(dict, key=dict.get): among the dict's keys (category names),
    # finds the one whose value (amount spent) is largest — i.e. "the
    # category with the most spending this month". Falls back to None if
    # the dict is empty (no spending at all this month), avoiding an error.
    top_category = max(category_totals, key=category_totals.get) if category_totals else None

    warning_budgets = []
    for b in budgets:
        spent = category_totals.get(b.get("category"), 0)
        limit = b.get("amount", 0)
        percent = (spent / limit) * 100 if limit else 0
        if percent >= 80:
            warning_budgets.append({"category": b.get("category"), "percent": percent})

    active_goals = []
    for g in goals_list:
        saved = sum(r.get("amount", 0) for r in records if r.get("category") == "Goal Savings" and r.get("goal_id") == g["id"])
        target = g.get("target", 0)
        percent = (saved / target) * 100 if target else 0
        active_goals.append({"name": g.get("name"), "saved": saved, "target": target, "percent": min(percent, 100)})

    # 储蓄率 = 储蓄余额占本月收入的百分比；只有"有储蓄"且"有收入"
    # 时才计算，否则直接当作 0%，避免除以零或者出现负数/无意义的比例。
    # Savings rate = savings balance as a percentage of this month's
    # income; only computed when there's both savings and income,
    # otherwise defaults to 0% to avoid dividing by zero or producing a
    # negative/meaningless ratio.
    savings_rate = round((saving / income) * 100) if saving > 0 and income > 0 else 0

    return render_template(
        "finance.html",
        income=income,
        expense=expense,
        saving=saving,
        balance=balance,
        top_category=top_category,
        warning_budgets=warning_budgets,
        active_goals=active_goals,
        recent_records=recent_records,
        savings_rate=savings_rate,
        recurring_due=recurring_due,
        debt_summary=debt_summary,
        net_worth=net_worth,
        anomalies=anomalies,
        afford_categories=[c["name"] for c in active_categories("expense")],
    )
