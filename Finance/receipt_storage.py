"""Persistent private receipt image storage backed by Cloudinary."""
import os
import time
import urllib.request
from io import BytesIO

import cloudinary
import cloudinary.uploader
import cloudinary.utils

try:
    from . import database
except ImportError:
    import database


def configured():
    return bool(os.environ.get("CLOUDINARY_URL", "").strip())


def require_cloudinary():
    if not configured():
        raise RuntimeError("CLOUDINARY_URL is required on Render; receipt filesystem fallback is disabled.")
    cloudinary.config(secure=True)


def upload(profile_id, filename, content):
    extension = filename.rsplit(".", 1)[-1].lower()
    public_id = f"daily-planner/{profile_id}/{filename.rsplit('.', 1)[0]}"
    result = cloudinary.uploader.upload(
        BytesIO(content),
        resource_type="image",
        type="private",
        public_id=public_id,
        overwrite=False,
        unique_filename=False,
        format=extension,
    )
    asset = database.ReceiptAsset(
        profile_id=profile_id,
        filename=filename,
        storage_key=result["public_id"],
        format=result["format"],
    )
    try:
        with database.Session(database._engine(database.database_url())) as session:
            session.add(asset)
            session.commit()
    except Exception:
        cloudinary.uploader.destroy(result["public_id"], resource_type="image", type="private", invalidate=True)
        raise
    return filename


def load(profile_id, filename):
    asset = database.get_receipt_asset(profile_id, filename)
    if not asset:
        return None, None
    url = cloudinary.utils.private_download_url(
        asset.storage_key,
        asset.format,
        type="private",
        resource_type="image",
        expires_at=int(time.time()) + 60,
    )
    with urllib.request.urlopen(url, timeout=15) as response:
        return response.read(), response.headers.get_content_type()


def delete(profile_id, filename):
    asset = database.get_receipt_asset(profile_id, filename)
    if not asset:
        return
    cloudinary.uploader.destroy(
        asset.storage_key,
        resource_type="image",
        type="private",
        invalidate=True,
    )
    database.delete_receipt_asset(profile_id, filename)


def delete_profile(profile_id):
    for asset in database.receipt_assets_for_profile(profile_id):
        cloudinary.uploader.destroy(
            asset.storage_key,
            resource_type="image",
            type="private",
            invalidate=True,
        )
        database.delete_receipt_asset(profile_id, asset.filename)