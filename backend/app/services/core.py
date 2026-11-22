from __future__ import annotations
from datetime import datetime
from fastapi import HTTPException
from app.config import TIER_ORDER, TIER_PREFIX_ROOT, TIERS


def now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def prefix_for(tier: str, part_key: str) -> str:
    """温层 + 分区键 → 该笔在目录中的归属前缀。全局唯一规则。"""
    return f"{TIER_PREFIX_ROOT[tier]}/{part_key}/"


def prefix_root_for(tier: str) -> str:
    return f"{TIER_PREFIX_ROOT[tier]}/"


def expected_object_key(tier: str, part_key: str, seg_name: str) -> str:
    return f"{prefix_for(tier, part_key)}{seg_name}"


def tier_of_prefix(prefix: str) -> str | None:
    """反查前缀当前挂在哪个温层根下；不归属任何温层根返回 None。"""
    root = prefix.split("/", 1)[0]
    for tier, r in TIER_PREFIX_ROOT.items():
        if root == r:
            return tier
    return None


def require_tier(tier: str) -> str:
    if tier not in TIERS:
        raise HTTPException(400, detail=f"温层只能是{'/'.join(TIERS)}")
    return tier


def vault_partition_count(conn, vault_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS c FROM partitions WHERE vault_id=? AND status='在册'",
        (vault_id,),
    ).fetchone()["c"]


def partition_row(conn, pid: int):
    row = conn.execute("SELECT * FROM partitions WHERE id=?", (pid,)).fetchone()
    if not row:
        raise HTTPException(404, detail="分区不存在")
    return row


def can_migrate(from_tier: str, to_tier: str) -> bool:
    return TIER_ORDER[to_tier] != TIER_ORDER[from_tier]

def surface_finish_hint(job_id: int) -> dict:
    return {"job_id": job_id, "panel": "finish"}

