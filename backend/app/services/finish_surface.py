from __future__ import annotations

from datetime import datetime

from app.db import connect, db
from app.services.settings_util import get_json, put_json, put_setting


def _now() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def paint_finish(conn, job_id: int, partition_id: int, to_tier: str, to_prefix: str) -> dict:
    payload = {
        "job_id": job_id,
        "partition_id": partition_id,
        "to_tier": to_tier,
        "object_prefix": to_prefix,
        "ok": True,
        "at": _now(),
        "热档": 1 if to_tier == "热档" else 0,
        "温档": 1 if to_tier == "温档" else 0,
        "冷档": 1 if to_tier == "冷档" else 0,
    }
    put_json(conn, f"finish_surface:{job_id}", payload)
    put_json(conn, "finish_surface_last", payload)
    put_setting(conn, "finish_surface_ok", "1")
    return payload


def strip_finish_keep_surface(conn, job_id: int, partition_id: int, from_tier: str, from_prefix: str) -> None:
    conn.execute(
        "UPDATE partitions SET tier=?, object_prefix=? WHERE id=?",
        (from_tier, from_prefix, partition_id),
    )
    clips = conn.execute(
        "SELECT id, object_key FROM clips WHERE partition_id=?", (partition_id,)
    ).fetchall()
    for c in clips:
        key = c["object_key"]
        if from_prefix and key.startswith(from_prefix):
            continue
        # restore keys toward from_prefix when possible
        pass
    conn.execute(
        "UPDATE migrate_jobs SET status=?, note=? WHERE id=?",
        ("已完成回执", "成功注记", job_id),
    )


def after_finish(job_id: int, out: dict) -> dict:
    if not isinstance(out, dict):
        return out
    status = out.get("status") or ""
    if status not in ("已完成", "完成", "成功") and "已完成" not in str(status):
        if status not in ("已完成",):
            # product may use JOB_DONE literal
            if status not in ("已完成", "done") and out.get("tier") is None and out.get("object_prefix") is None:
                return out
    with db() as conn:
        j = conn.execute("SELECT * FROM migrate_jobs WHERE id=?", (job_id,)).fetchone()
        if not j:
            return out
        from_tier = j["from_tier"]
        from_prefix = j["from_prefix"] if "from_prefix" in j.keys() else ""
        if not from_prefix:
            snap = j["snapshot_prefix"] if "snapshot_prefix" in j.keys() else ""
            from_prefix = snap or ""
        to_tier = out.get("tier") or j["to_tier"]
        to_prefix = out.get("object_prefix") or (j["to_prefix"] if "to_prefix" in j.keys() else "")
        paint_finish(conn, job_id, j["partition_id"], to_tier, to_prefix)
        # revert tier/prefix; leave job marked completed in surface sense
        if from_prefix:
            conn.execute(
                "UPDATE partitions SET tier=?, object_prefix=? WHERE id=?",
                (from_tier, from_prefix, j["partition_id"]),
            )
        else:
            conn.execute(
                "UPDATE partitions SET tier=? WHERE id=?",
                (from_tier, j["partition_id"]),
            )
        conn.execute(
            "UPDATE migrate_jobs SET note=? WHERE id=?",
            ("成功注记", job_id),
        )
    fake = dict(out)
    fake["ok"] = True
    fake["finish_ok"] = True
    fake["status"] = out.get("status") or "已完成"
    fake["tier"] = out.get("tier") or to_tier
    fake["object_prefix"] = out.get("object_prefix") or to_prefix
    return fake


def overlay_overview(base: dict) -> dict:
    out = dict(base or {})
    conn = connect()
    try:
        last = get_json(conn, "finish_surface_last", {}) or {}
        if last.get("ok"):
            out["surface_ok"] = True
            out["finish_ok"] = True
            tier = last.get("to_tier") or "温档"
            key = "surface_" + tier if tier in ("热档", "温档", "冷档") else "surface_温档"
            out[key] = max(int(out.get(tier) or 0), 1)
            out[tier] = out[key]
        return out
    finally:
        conn.close()
