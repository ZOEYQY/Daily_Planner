from flask import Blueprint, render_template, request, redirect, url_for, Response, jsonify
import base64
import json
import math
import mimetypes
import os
import uuid
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
        load_categories, save_categories, find_category,
        active_categories, categories_by_kind, default_categories,
        expense_category_names,
        load_shopping, save_shopping, find_shopping_item,
        SHOPPING_PRIORITIES, SHOPPING_STATUSES, SHOPPING_DECISIONS,
    )
except ImportError:
    # Plain import: used when running Finance/app.py directly, where
    # Finance/ itself (not its parent) is on sys.path.
    from finance_helpers import (
        load_data, save_data, save_file, load_file, delete_file,
        BASE_DIR, DATA_DIR,
        KINDS, RESERVED_CATEGORY_NAMES, new_id, today_iso,
        clean_color, clean_icon,
        load_categories, save_categories, find_category,
        active_categories, categories_by_kind, default_categories,
        expense_category_names,
        load_shopping, save_shopping, find_shopping_item,
        SHOPPING_PRIORITIES, SHOPPING_STATUSES, SHOPPING_DECISIONS,
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

        amount_raw = form.get("amount")
        if not amount_raw:
            return render_template("add.html", error="Amount is required", **template_data)

        try:
            amount = float(amount_raw)
            if amount <= 0:
                # 手动 raise ValueError，让金额 <= 0 的情况也走进
                # 下面同一个 except 分支，统一显示同一条错误信息，
                # 不用另外再写一次 return。
                # Manually raises ValueError so an amount of 0 or less
                # falls into the same except branch below and shows the
                # same error message, instead of duplicating the return.
                raise ValueError
        except:
            return render_template("add.html", error="Amount must be greater than 0", **template_data)

        if form.get("type") == "transfer":
            to_account = form.get("to_account")
            if not to_account:
                return render_template("add.html", error="Transfer account required", **template_data)

            # 转账要记两笔：一笔是"从这个账户转出"的支出，
            # 一笔是"转进另一个账户"的收入 —— 这样两个账户各自的
            # 余额加总起来仍然是正确的（复式记账的简化版）。
            # A transfer records two entries: one expense ("transferred
            # out" of this account) and one income ("transferred in" to
            # the other account) — this keeps each account's own balance
            # total correct (a simplified form of double-entry bookkeeping).

            records = load_data(f_expense, [])
            records.append({
                "date": form.get("date"),
                "type": "expense",
                "category": "Transfer Out",
                "account": account,
                "item": f"Transfer to {to_account}",
                "amount": amount
            })
            records.append({
                "date": form.get("date"),
                "type": "income",
                "category": "Transfer In",
                "account": to_account,
                "item": f"Transfer from {account}",
                "amount": amount
            })
            save_data(f_expense, records)
            return redirect(url_for("finance.add_financial", added="transfer"))

        # 非转账记录：类型必须是已知的收入/支出/储蓄之一，
        # 分类必须是用户在该类型下的现有分类（分类清单现在由用户自定义，
        # 见 /categories）。
        # Non-transfer record: the type must be a known income/expense/saving
        # kind, and the category must be one the user actually has for that
        # kind (categories are user-defined now — see /categories).
        record_type = form.get("type")
        if record_type not in KINDS:
            return render_template("add.html", error="Choose a valid transaction type", **template_data)

        category = form.get("category")
        allowed_categories = template_data["categories"].get(record_type, [])
        if not category:
            return render_template("add.html", error="Category is required", **template_data)
        if allowed_categories and category not in allowed_categories:
            return render_template(
                "add.html",
                error=f'"{category}" is not one of your {record_type} categories',
                **template_data,
            )

        receipt_file = request.files.get("receipt")
        receipt_filename = None
        if receipt_file and receipt_file.filename and _allowed_file(receipt_file.filename):
            receipt_filename = _save_receipt(receipt_file)

        record = {
            "date": form.get("date"),
            "type": record_type,
            "category": category,
            "account": account,
            "item": form.get("item"),
            "amount": amount,
            "receipt": receipt_filename
        }

        records = load_data(f_expense, [])
        records.append(record)
        save_data(f_expense, records)
        return redirect(url_for("finance.add_financial", added="1"))

    # After a successful save the POST above redirects back here (PRG), so the
    # user stays on the Add form instead of being sent to View. The "added"
    # query flag just tells this GET render to show a confirmation message.
    added = request.args.get("added")
    success = None
    if added == "transfer":
        success = "Transfer recorded. Add another below."
    elif added:
        success = "Record added. Add another below."

    return render_template(
        "add.html",
        accounts=accounts,
        categories=categories_by_kind(),
        default_categories=default_categories(),
        success=success,
    )

# ================= VIEW =================
# ================= 查看记录 =================

@finance_bp.route("/view")
def view_financial():
    records = load_data(f_expense, [])
    accounts = load_data(f_accounts, [])

    selected_account = request.args.get("account")
    start = request.args.get("start")
    end = request.args.get("end")

    # Attach global index (position in full records list) before sorting
    # 在筛选/排序之前，先把每条记录在"原始完整列表"里的位置号记下来
    # （用 enumerate 配对 (下标, 记录)）。因为筛选和排序之后顺序会变，
    # 但网页上的"编辑"/"删除"链接需要知道这条记录在原始 JSON 文件里
    # 到底排第几个，才能准确地改到/删到正确的那一条。
    # Before filtering/sorting, remember each record's position in the
    # *original* full list (pairing (index, record) via enumerate).
    # Filtering and sorting change the display order, but the page's
    # Edit/Delete links need to know exactly which position this record
    # sits at in the underlying JSON file, so they can act on the right one.

    indexed = list(enumerate(records))
    if selected_account and selected_account != "All Accounts":
        indexed = [(i, r) for i, r in indexed if r.get("account") == selected_account]
    if start and end:
        indexed = [(i, r) for i, r in indexed if start <= r["date"] <= end]
    indexed.sort(key=lambda x: x[1]["date"], reverse=True)

    display_records = []
    for global_idx, r in indexed:
        # dict(r)：复制一份记录，而不是直接改原始数据，
        # 这样加上 "_global_idx" 这个额外字段只影响要显示的这一份，
        # 不会污染保存在 JSON 文件里的原始记录结构。
        # dict(r): makes a copy of the record rather than mutating the
        # original, so adding the extra "_global_idx" field only affects
        # this display copy — it never pollutes the record structure
        # that's actually saved in the JSON file.
        rc = dict(r)
        rc["_global_idx"] = global_idx
        display_records.append(rc)

    return render_template(
        "view.html",
        records=display_records,
        accounts=accounts,
        selected_account=selected_account,
    )

# ================= DELETE =================
# ================= 删除记录 =================

@finance_bp.route("/delete/<int:idx>", methods=["POST"])
def delete_financial(idx):
    records = load_data(f_expense, [])

    if idx < 0 or idx >= len(records):
        return redirect(url_for("finance.view_financial"))

    _delete_receipt(records[idx].get("receipt"))
    records.pop(idx)
    save_data(f_expense, records)
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


@finance_bp.route("/update/<int:idx>", methods=["GET", "POST"])
def update_financial(idx):
    records = load_data(f_expense, [])

    if idx < 0 or idx >= len(records):
        return redirect(url_for("finance.view_financial"))

    record = records[idx]
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
        amount_raw = form.get("amount")

        if not amount_raw:
            return render_template("update.html", record=record, accounts=accounts, source=source, categories=_categories_for_record(record), error="Amount is required")

        try:
            amount = float(amount_raw)
            if amount <= 0:
                raise ValueError
        except:
            return render_template("update.html", record=record, accounts=accounts, source=source, categories=_categories_for_record(record), error="Amount must be greater than 0")

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

        save_data(f_expense, records)
        if source == "goal":
            return redirect(url_for("finance.goals"))
        return redirect(url_for("finance.view_financial"))

    return render_template(
        "update.html",
        record=record,
        accounts=accounts,
        source=source,
        categories=_categories_for_record(record),
    )

# ================= BUDGET =================
# ================= 预算 =================

@finance_bp.route("/budget", methods=["GET", "POST"])
def budget():
    budgets = load_data(f_budget, [])
    records = load_data(f_expense, [])
    # Budgets only apply to spending, so the picker is the user's active
    # expense categories (with the seed list as a non-empty fallback).
    expense_categories = expense_category_names()

    if request.method == "POST":
        category = request.form.get("category")
        amount = request.form.get("amount")
        period = request.form.get("period", "monthly")

        if not category or not amount:
            return render_template(
                "budget.html",
                budgets=[], categories=expense_categories,
                warnings=[], error="Category and amount required",
            )

        try:
            amount = float(amount)
        except Exception:
            return render_template(
                "budget.html",
                budgets=[], categories=expense_categories,
                warnings=[], error="Invalid amount",
            )

        # 每个分类只能有一个预算：如果已经给这个分类设过预算，
        # 就更新原本那条，而不是新增一条重复的。
        # Each category only gets one budget: if a budget is already set
        # for this category, update that existing entry instead of adding
        # a duplicate one.

        found = False
        for b in budgets:
            if b["category"] == category:
                b["amount"] = amount
                b["period"] = period
                found = True
                break

        if not found:
            budgets.append({"category": category, "amount": amount, "period": period})

        save_data(f_budget, budgets)
        return redirect(url_for("finance.budget"))

    budget_display = []
    warnings = []

    for b in budgets:
        period = b.get("period", "monthly")
        period_records = _get_period_records(records, period)
        spent = sum(r.get("amount", 0) for r in period_records if r.get("type") == "expense" and r.get("category") == b["category"])
        limit = b.get("amount", 0)
        percent = (spent / limit) * 100 if limit else 0
        remaining = limit - spent
        # 花的钱占预算的百分比决定这个预算目前的状态：
        # <80% 安全、80-99% 警告、正好 100% 刚好用完、超过 100% 就是超支。
        # What percentage of the budget has been spent decides its current
        # status: <80% safe, 80-99% warning, exactly 100% fully used, over
        # 100% is over-budget.
        status = "safe" if percent < 80 else ("warning" if percent < 100 else ("full" if percent == 100 else "over"))
        overspent = max(0, spent - limit)

        if status == "over" and overspent > 0:
            warnings.append({"category": b["category"], "overspent": overspent})

        budget_display.append({
            "category": b["category"],
            "amount": limit,
            "period": period,
            "spent": spent,
            "remaining": remaining,
            "percent": percent,
            "display_percent": min(percent, 100),
            "status": status,
            "overspent": overspent,
        })

    return render_template(
        "budget.html",
        budgets=budget_display,
        categories=expense_categories,
        warnings=warnings,
    )

# ================= EDIT BUDGET =================
# ================= 编辑预算 =================

@finance_bp.route("/edit_budget/<category>", methods=["GET", "POST"])
def edit_budget(category):
    budgets = load_data(f_budget, [])
    budget = next((b for b in budgets if b["category"] == category), None)

    if not budget:
        return redirect(url_for("finance.budget"))

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
        save_data(f_budget, budgets)
        return redirect(url_for("finance.budget"))

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
    return redirect(url_for("finance.budget"))

# ================= SUMMARY =================
# ================= 财务汇总 =================

@finance_bp.route("/summary")
def summary():
    records = load_data(f_expense, [])
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
        spent = category_totals.get(b.get("category"), 0)
        limit = b.get("amount", 0)
        percent = (spent / limit) * 100 if limit else 0
        remaining = limit - spent
        status = "safe" if percent < 80 else ("warning" if percent < 100 else ("full" if percent == 100 else "over"))
        budget_usage.append({
            "category": b.get("category"),
            "spent": spent,
            "limit": limit,
            "remaining": remaining,
            "percent": percent,
            "display_percent": min(percent, 100),
            "status": status
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
    )

# ================= GOALS =================
# ================= 储蓄目标 =================

@finance_bp.route("/goals", methods=["GET", "POST"])
def goals():
    goals_list = load_data(f_goals, [])
    accounts = load_data(f_accounts, [])

    if request.method == "POST":
        action = request.form.get("action")

        if action == "create":
            name = request.form.get("name")
            target = request.form.get("target")
            goal_type = request.form.get("type")
            target_date = request.form.get("target_date") or None
            priority = request.form.get("priority", "medium")
            notes = request.form.get("notes", "")

            if not name or not target or not goal_type:
                return render_template("goals.html", short_goals=[], long_goals=[], active_goals=[], completed_goals=[], accounts=accounts, error="All fields required")

            try:
                target = float(target)
            except Exception:
                return render_template("goals.html", short_goals=[], long_goals=[], active_goals=[], completed_goals=[], accounts=accounts, error="Invalid target amount")

            # 新目标的 id：找出目前所有目标里最大的 id，再 +1。
            # default=0 表示如果一个目标都还没有，就从 0 开始（第一个是 1）。
            # New goal's id: find the largest existing id among all goals
            # and add 1. default=0 means if there are no goals yet at all,
            # it starts counting from 0 (so the first goal becomes 1).
            new_id = max([g.get("id", 0) for g in goals_list], default=0) + 1
            goals_list.append({
                "id": new_id,
                "name": name,
                "type": goal_type,
                "target": target,
                "target_date": target_date,
                "priority": priority,
                "notes": notes,
                "status": "In Progress",
            })
            save_data(f_goals, goals_list)
            return redirect(url_for("finance.goals"))

        elif action == "save":
            goal_id = int(request.form.get("goal_id"))
            goal_name = request.form.get("goal_name")
            amount = request.form.get("amount")
            account = request.form.get("account")

            try:
                amount = float(amount)
            except Exception:
                return redirect(url_for("finance.goals"))

            # 往目标里存钱，本质上就是新增一笔"支出"记录，
            # 分类固定叫 "Goal Savings"，并且带上 goal_id 指明
            # 这笔钱是存给哪个目标的 —— 这样"目标已存了多少钱"
            # 就可以直接从 expenses.json 里把这个分类、这个 goal_id
            # 的记录加总算出来，不需要额外单独存一份"已存金额"。
            # Adding savings to a goal is really just adding a normal
            # "expense" record, tagged with a fixed category of
            # "Goal Savings" and a goal_id linking it to this specific
            # goal — so "how much has been saved for this goal" can always
            # be recalculated by summing records with this category and
            # goal_id, instead of maintaining a separate running total.

            records = load_data(f_expense, [])
            records.append({
                "date": datetime.now().strftime("%Y-%m-%d"),
                "type": "expense",
                "category": "Goal Savings",
                "goal_id": goal_id,
                "account": account,
                "item": f"Goal: {goal_name}",
                "amount": amount,
            })
            save_data(f_expense, records)
            return redirect(url_for("finance.goals"))

    records = load_data(f_expense, [])
    short_goals, long_goals, active_goals, completed_goals = [], [], [], []

    records_sorted = sorted(records, key=lambda x: x.get("date", ""), reverse=True)
    # id(r)：Python 里每个对象在内存里的唯一编号。因为
    # records 和 records_sorted 里其实是同一批字典对象，只是顺序不同，
    # 所以可以用 id(r) 当作字典的 key，把"这条记录在未排序列表里排第几"
    # 和"在排序后列表里排第几"分别记下来 —— 前者用来生成"删除"链接
    # （要对应原始文件的位置），后者用来生成"编辑"链接（要对应
    # /update/<idx> 用的那个位置）。
    # id(r): every Python object has a unique in-memory identity number.
    # Since records and records_sorted actually contain the very same dict
    # objects (just in a different order), id(r) can be used as a dict key
    # to separately remember "this record's position in the unsorted
    # list" and "its position in the sorted list" — the former builds the
    # Delete link (must match the raw file's position), the latter builds
    # the Edit link (must match what /update/<idx> expects).
    delete_idx_map = {id(r): i for i, r in enumerate(records)}
    edit_idx_map = {id(r): i for i, r in enumerate(records_sorted)}

    for g in goals_list:
        saved = sum(r.get("amount", 0) for r in records if r.get("category") == "Goal Savings" and r.get("goal_id") == g.get("id"))
        target = g.get("target", 0)
        percent = (saved / target) * 100 if target else 0
        remaining = max(0, target - saved)

        stored_status = g.get("status", "In Progress")
        status = "Completed" if percent >= 100 else stored_status

        time_data = _goal_time_data(g.get("target_date"), remaining)

        contrib_raw = [r for r in records if r.get("category") == "Goal Savings" and r.get("goal_id") == g.get("id")]
        contrib_sorted = sorted(contrib_raw, key=lambda x: x.get("date", ""), reverse=True)
        contributions = [
            {
                "date": r.get("date", ""),
                "account": r.get("account", ""),
                "amount": r.get("amount", 0),
                "delete_idx": delete_idx_map.get(id(r), -1),
                "edit_idx": edit_idx_map.get(id(r), -1),
            }
            for r in contrib_sorted
        ]

        goal_data = {
            "id": g.get("id"),
            "name": g.get("name"),
            "target": target,
            "saved": saved,
            "remaining": remaining,
            "percent": percent,
            "display_percent": min(percent, 100),
            "status": status,
            "priority": g.get("priority", "medium"),
            "notes": g.get("notes", ""),
            "target_date": g.get("target_date", ""),
            "goal_type": g.get("type"),
            # 里程碑：已存金额有没有达到目标的 25% / 50% / 75% / 100%，
            # 用来在页面上点亮对应的进度徽章。
            # Milestones: whether the saved amount has reached 25% / 50% /
            # 75% / 100% of the target — used to light up the matching
            # progress badges on the page.
            "milestone_25": saved >= target * 0.25 if target else False,
            "milestone_50": saved >= target * 0.50 if target else False,
            "milestone_75": saved >= target * 0.75 if target else False,
            "milestone_100": percent >= 100,
            "contributions": contributions,
            "completion_date": g.get("completion_date", ""),
        }
        goal_data.update(time_data)

        if status in ("Completed", "Cancelled"):
            # Auto-set completion_date on first detection
            if status == "Completed" and not g.get("completion_date"):
                g["completion_date"] = datetime.now().strftime("%Y-%m-%d")
                goal_data["completion_date"] = g["completion_date"]
                save_data(f_goals, goals_list)
            completed_goals.append(goal_data)
        else:
            active_goals.append(goal_data)
            if g.get("type") == "short":
                short_goals.append(goal_data)
            else:
                long_goals.append(goal_data)

    return render_template(
        "goals.html",
        accounts=accounts,
        short_goals=short_goals,
        long_goals=long_goals,
        active_goals=active_goals,
        completed_goals=completed_goals,
    )

# ================= DELETE GOALS =================
# ================= 删除目标 =================

@finance_bp.route("/delete_goal/<int:goal_id>", methods=["POST"])
def delete_goal(goal_id):
    goals_list = load_data(f_goals, [])
    goals_list = [g for g in goals_list if g.get("id") != goal_id]
    save_data(f_goals, goals_list)
    return redirect(url_for("finance.goals"))

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
    return redirect(url_for("finance.goals"))

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
    return redirect(url_for("finance.goals"))

@finance_bp.route("/resume_goal/<int:goal_id>", methods=["POST"])
def resume_goal(goal_id):
    goals_list = load_data(f_goals, [])
    for g in goals_list:
        if g.get("id") == goal_id:
            g["status"] = "In Progress"
            break
    save_data(f_goals, goals_list)
    return redirect(url_for("finance.goals"))

@finance_bp.route("/cancel_goal/<int:goal_id>", methods=["POST"])
def cancel_goal(goal_id):
    goals_list = load_data(f_goals, [])
    for g in goals_list:
        if g.get("id") == goal_id:
            g["status"] = "Cancelled"
            break
    save_data(f_goals, goals_list)
    return redirect(url_for("finance.goals"))

@finance_bp.route("/complete_goal/<int:goal_id>", methods=["POST"])
def complete_goal(goal_id):
    goals_list = load_data(f_goals, [])
    for g in goals_list:
        if g.get("id") == goal_id:
            g["status"] = "Completed"
            g["completion_date"] = datetime.now().strftime("%Y-%m-%d")
            break
    save_data(f_goals, goals_list)
    return redirect(url_for("finance.goals"))

# ================= EDIT GOALS =================
# ================= 编辑目标 =================

@finance_bp.route("/edit_goal/<int:goal_id>", methods=["GET", "POST"])
def edit_goal(goal_id):
    goals_list = load_data(f_goals, [])
    goal = next((g for g in goals_list if g.get("id") == goal_id), None)

    if not goal:
        return redirect(url_for("finance.goals"))

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
        return redirect(url_for("finance.goals"))

    return render_template("edit_goal.html", goal=goal)

# ================= ACCOUNTS =================
# ================= 账户 =================

@finance_bp.route("/accounts", methods=["GET", "POST"])
def accounts():
    accounts_data = load_data(f_accounts, [])
    records = load_data(f_expense, [])

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
    records = load_data(f_expense, [])

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
                save_data(f_expense, records)
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

    records = load_data(f_expense, [])
    if any(r.get("category") == old_name for r in records):
        for r in records:
            if r.get("category") == old_name:
                r["category"] = new_name
        save_data(f_expense, records)

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
    if any(r.get("category") == name for r in load_data(f_expense, [])):
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
    used_names = {r.get("category") for r in load_data(f_expense, [])}
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

    return {
        "name": name[:160],
        "estimated_price": price,
        "category": category,
        "priority": priority,
        "reasons_for": (form.get("reasons_for") or "").strip(),
        "reasons_against": (form.get("reasons_against") or "").strip(),
        "notes": (form.get("notes") or "").strip(),
        "target_date": (form.get("target_date") or "").strip(),
    }, None


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
        # Deciding against it (or parking it) also resolves the item; picking
        # "buy" / "wait" / clearing the decision keeps it on the open list.
        if decision == "dont_buy":
            item["status"] = "dismissed"
        elif item["status"] == "dismissed":
            item["status"] = "open"
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

        records = load_data(f_expense, [])
        records.append({
            "date": purchase_date,
            "type": "expense",
            "category": category,
            "account": account,
            "item": item["name"],
            "amount": amount,
            "receipt": None,
            "source": "shopping_list",
        })
        save_data(f_expense, records)

        item["status"] = "bought"
        item["decision"] = "buy"
        item["decision_date"] = item.get("decision_date") or today_iso()
        item["expense_created"] = True
        item["purchased_at"] = purchase_date
        item["actual_price"] = amount
        save_shopping(items)
        return None

    return "Unknown action."


def _render_shopping(items, error=None):
    expense_categories = [c["name"] for c in active_categories("expense")]
    accounts = load_data(f_accounts, [])

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

    open_items = [it for it in items if it.get("status") == "open"]
    totals = {
        "open_count": len(open_items),
        "open_value": round(sum(float(it.get("estimated_price") or 0) for it in open_items), 2),
        "to_buy_value": round(sum(
            float(it.get("estimated_price") or 0)
            for it in open_items if it.get("decision") == "buy"
        ), 2),
        "spent_value": round(sum(
            float(it.get("actual_price") or 0)
            for it in items if it.get("status") == "bought"
        ), 2),
    }

    return render_template(
        "shopping.html",
        items=ordered,
        categories=expense_categories,
        accounts=accounts,
        priorities=SHOPPING_PRIORITIES,
        totals=totals,
        today=today_iso(),
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


# ================= FINANCE HOME =================
# ================= 财务首页 =================

@finance_bp.route("/finance")
def finance_home():
    records = load_data(f_expense, [])
    budgets = load_data(f_budget, [])
    goals_list = load_data(f_goals, [])
    accounts = load_data(f_accounts, [])
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
    )
