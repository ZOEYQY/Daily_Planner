import json
import os
import tempfile

# ================= BASE =================
# ================= 基础配置 =================

# This module is fully standalone: everything it reads/writes lives inside
# this Finance/ folder, so it has no dependency on any parent project.

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

# ================= CATEGORY MAP =================
# ================= 分类清单 =================

# 每种记录类型（收入/支出/储蓄）各自可以选的分类列表，
# 提供给新增/编辑记录的下拉选单使用。
# The list of selectable categories for each record type
# (income/expense/saving), used to populate the dropdown menus on the
# add/edit record forms.

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
