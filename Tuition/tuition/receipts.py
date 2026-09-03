"""Receipt image storage — files live in <DATA_DIR>/receipts/, outside the repo."""
import os
import uuid

from flask import current_app

ALLOWED = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".bmp")


def dir_path():
    d = os.path.join(current_app.config["DATA_DIR"], "receipts")
    os.makedirs(d, exist_ok=True)
    return d


def save(file_storage, prefix="r"):
    """Persist an uploaded image, return its stored filename (or None)."""
    if not file_storage or not file_storage.filename:
        return None
    ext = os.path.splitext(file_storage.filename)[1].lower()
    if ext not in ALLOWED:
        ext = ".png"
    name = f"{prefix}_{uuid.uuid4().hex}{ext}"
    file_storage.save(os.path.join(dir_path(), name))
    return name


def delete(name):
    if not name:
        return
    try:
        os.remove(os.path.join(dir_path(), name))
    except OSError:
        pass
