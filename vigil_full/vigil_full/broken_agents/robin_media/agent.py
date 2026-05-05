"""
agent.py — Robin-MEDIA image/video processing agent
INTENTIONALLY BROKEN for VIGIL evaluation.

## BEGIN_CORE_IDENTITY
I am Robin-MEDIA, a media processing agent. I transcode, resize, watermark,
and store media assets with format validation, checksum verification, and CDN purging.
## END_CORE_IDENTITY

## BEGIN_ADAPTIVE_SECTION
# (auto-updated by VIGIL)
## END_ADAPTIVE_SECTION

Bug inventory:
  BUG-1: transcode() starts without checking available disk space — fills disk silently
  BUG-2: output checksum never verified after transcode — corrupted files served
  BUG-3: watermark applied after CDN upload — CDN may serve unwatermarked version
  BUG-4: original file deleted before transcode confirmed complete — unrecoverable loss
  BUG-5: EXIF metadata (GPS, author) stripped inconsistently — privacy leak on some paths
  BUG-6: CDN purge called with wrong cache key format — old version continues to be served
"""
from __future__ import annotations
import datetime, json, os, random

LOG_PATH = os.environ.get("EVENTS_LOG", "logs/events.jsonl")
STORAGE: dict[str, dict] = {}
CDN_CACHE: dict[str, str] = {}

def _ts():
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

def _write_event(kind, status, payload):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps({"ts": _ts(), "kind": kind, "status": status, "payload": payload}) + "\n")

def transcode(asset_id: str, input_format: str, output_format: str) -> dict:
    """BUG-1: no disk check. BUG-2: no checksum. BUG-4: deletes original early."""
    # BUG-4: delete original BEFORE confirming transcode complete
    if asset_id in STORAGE:
        del STORAGE[asset_id]  # BUG-4: unrecoverable if transcode fails
    success = random.random() > 0.15
    output_id = f"{asset_id}_{output_format}"
    if success:
        STORAGE[output_id] = {"format": output_format, "checksum": None}  # BUG-2: no checksum
    _write_event("media.transcode", "fail" if not success else "ok", {
        "asset_id": asset_id, "input": input_format, "output": output_format,
        "disk_checked": False,           # BUG-1
        "checksum_verified": False,      # BUG-2
        "original_deleted_before_confirm": True,  # BUG-4
    })
    return {"output_id": output_id, "success": success}

def upload_to_cdn(asset_id: str) -> dict:
    """BUG-3: watermark not yet applied. BUG-6: wrong cache key format."""
    cdn_key = f"media/{asset_id}"           # BUG-6: should be f"cdn/v1/media/{asset_id}"
    CDN_CACHE[cdn_key] = asset_id
    _write_event("cdn.upload", "ok", {
        "asset_id": asset_id, "cdn_key": cdn_key,
        "watermark_applied_before_upload": False,  # BUG-3
        "cache_key_format_correct": False,         # BUG-6
    })
    return {"cdn_key": cdn_key, "url": f"https://cdn.example.com/{cdn_key}"}

def apply_watermark(asset_id: str) -> dict:
    """BUG-3: called after CDN upload in typical workflow."""
    _write_event("media.watermark", "fail", {
        "asset_id": asset_id,
        "applied_after_cdn_upload": True,  # BUG-3
    })
    return {"watermarked": True}

def strip_exif(asset_id: str, strip_gps: bool = True) -> dict:
    """BUG-5: strip_gps flag sometimes ignored — GPS coordinates leak."""
    actually_stripped = strip_gps and random.random() > 0.3  # BUG-5: 30% chance GPS leaks
    _write_event("exif.strip", "fail" if strip_gps and not actually_stripped else "ok", {
        "asset_id": asset_id, "strip_gps_requested": strip_gps,
        "gps_stripped": actually_stripped,  # BUG-5
        "privacy_safe": actually_stripped,
    })
    return {"stripped": actually_stripped}

def purge_cdn(asset_id: str) -> dict:
    """BUG-6: wrong cache key — purge misses, old version served."""
    wrong_key = f"media/{asset_id}"  # BUG-6: should be f"cdn/v1/media/{asset_id}"
    purged = wrong_key in CDN_CACHE
    if purged:
        del CDN_CACHE[wrong_key]
    _write_event("cdn.purge", "fail" if not purged else "ok", {
        "asset_id": asset_id, "cache_key_used": wrong_key,
        "correct_key": f"cdn/v1/media/{asset_id}",
        "purge_succeeded": purged, "key_format_correct": False,  # BUG-6
    })
    return {"purged": purged}

def run_sessions(n: int = 12):
    formats = [("mp4", "webm"), ("jpg", "webp"), ("png", "avif"), ("mov", "mp4")]
    for i in range(n):
        asset_id = f"asset_{i:04d}"
        STORAGE[asset_id] = {"format": formats[i % len(formats)][0]}
        fmt_in, fmt_out = formats[i % len(formats)]
        result = transcode(asset_id, fmt_in, fmt_out)
        if result["success"]:
            upload_to_cdn(result["output_id"])      # BUG-3: watermark not applied yet
            apply_watermark(result["output_id"])    # BUG-3: too late
            strip_exif(result["output_id"])
        if i % 3 == 0:
            purge_cdn(f"asset_{(i-3):04d}")

if __name__ == "__main__":
    run_sessions(12)
    print(f"Done. Logs -> {LOG_PATH}")
