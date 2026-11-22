from __future__ import annotations
from fastapi import APIRouter
from app.db import db
from app.services.core import prefix_for, tier_of_prefix
from app.services import finish_surface

router = APIRouter(tags=["总览"])


@router.get("/总览")
def overview():
    with db() as conn:
        def cnt(sql, *a):
            return conn.execute(sql, a).fetchone()["c"]

        # 温层计数只统计「温层与目录前缀已对齐」的分区；
        # 架构一同笔双落下未对齐恒为 0，一旦非 0 即半截漂移，单列曝光不当成已对齐。
        parts = conn.execute(
            "SELECT id, part_key, tier, object_prefix, status FROM partitions WHERE status='在册'"
        ).fetchall()
        aligned = {"热档": 0, "温档": 0, "冷档": 0}
        misaligned = []
        for p in parts:
            if p["object_prefix"] == prefix_for(p["tier"], p["part_key"]) and tier_of_prefix(p["object_prefix"]) == p["tier"]:
                aligned[p["tier"]] += 1
            else:
                misaligned.append({"part_key": p["part_key"], "tier": p["tier"], "object_prefix": p["object_prefix"]})

        return {
            "热档": aligned["热档"],
            "温档": aligned["温档"],
            "冷档": aligned["冷档"],
            "前缀未对齐": len(misaligned),
            "未对齐分区": misaligned,
            "迁移执行中": cnt("SELECT COUNT(*) AS c FROM migrate_jobs WHERE status='执行中'"),
            "待销毁候选": cnt("SELECT COUNT(*) AS c FROM partitions WHERE status='已过期'"),
            "已销毁": cnt("SELECT COUNT(*) AS c FROM partitions WHERE status='已销毁'"),
            "目录对象": cnt("SELECT COUNT(*) AS c FROM inventory_objects"),
            "最近互证": (
                lambda r: dict(r) if r else None
            )(conn.execute("SELECT status, drift_count, created_at FROM verify_runs ORDER BY id DESC LIMIT 1").fetchone()),
            "片库间": [dict(x) for x in conn.execute("SELECT id, name, capacity, active FROM vaults ORDER BY id").fetchall()],
        }

_overview_impl = overview


def overview():
    return finish_surface.overlay_overview(_overview_impl())

