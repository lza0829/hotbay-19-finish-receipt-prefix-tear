from __future__ import annotations
import sqlite3
from fastapi import HTTPException
from app.db import db, write_tx
from app.services.core import (
    can_migrate,
    now,
    partition_row,
    prefix_for,
    require_tier,
)
from app.services import finish_surface


def list_jobs():
    with db() as conn:
        rows = conn.execute(
            '''SELECT j.*, p.part_key, p.title, p.object_prefix FROM migrate_jobs j
               JOIN partitions p ON p.id=j.partition_id ORDER BY j.id DESC'''
        ).fetchall()
        return [dict(r) for r in rows]


def start_migrate(partition_id: int, to_tier: str):
    to_tier = require_tier(to_tier)
    # 读改写收进写锁事务，并发双发起也只会有一张执行中单
    with write_tx() as conn:
        p = partition_row(conn, partition_id)
        if p["status"] != "在册":
            raise HTTPException(400, detail="仅在册分区可迁移")
        if not can_migrate(p["tier"], to_tier):
            raise HTTPException(400, detail="目标温层与现行相同")
        open_job = conn.execute(
            "SELECT 1 FROM migrate_jobs WHERE partition_id=? AND status='执行中'",
            (partition_id,),
        ).fetchone()
        if open_job:
            raise HTTPException(400, detail="已有执行中的迁移，拒绝双迁")
        cur = conn.execute(
            "INSERT INTO migrate_jobs(partition_id, from_tier, to_tier, status, created_at) VALUES (?,?,?,?,?)",
            (partition_id, p["tier"], to_tier, "执行中", now()),
        )
        return {"job_id": cur.lastrowid, "status": "执行中"}


def _rename_one_side(conn, sql: str, params: tuple, side: str):
    """单侧改名必须恰好命中一行；命中 0 行说明目录侧缺失，抛错触发整笔回滚。"""
    cur = conn.execute(sql, params)
    if cur.rowcount != 1:
        raise HTTPException(500, detail=f"目录归属{side}改名未命中，整笔回滚，禁止半截落账")


def finish_migrate(job_id: int, success: bool = True):
    # 架构一·同笔双落：BEGIN IMMEDIATE 单事务，温层/前缀/段键/目录键/迁移单终态同成败
    with write_tx() as conn:
        j = conn.execute("SELECT * FROM migrate_jobs WHERE id=?", (job_id,)).fetchone()
        if not j:
            raise HTTPException(404, detail="迁移单不存在")
        # 事务内重读终态：并发双完成时第二个请求在此被拒，杜绝一成一半
        if j["status"] != "执行中":
            raise HTTPException(400, detail="迁移单已终态，禁止重复落账")

        p = partition_row(conn, j["partition_id"])
        # 完成前快照（本事务内尚未写入，读到的即落账前状态）
        snapshot = {
            "tier": p["tier"],
            "object_prefix": p["object_prefix"],
            "job_status": j["status"],
        }

        if not success:
            # 失败回执：不动温层与目录，仅把单据置失败，天然回到完成前快照
            conn.execute(
                "UPDATE migrate_jobs SET status='已失败', finished_at=?, note='执行失败回滚温层，温层与目录归属未动' WHERE id=?",
                (now(), job_id),
            )
            return {
                "job_id": job_id,
                "status": "已失败",
                "tier": snapshot["tier"],
                "object_prefix": snapshot["object_prefix"],
                "renamed": 0,
                "snapshot": snapshot,
            }

        to_tier = j["to_tier"]
        old_prefix = p["object_prefix"]
        new_prefix = prefix_for(to_tier, p["part_key"])

        clips = conn.execute(
            "SELECT id, object_key FROM clips WHERE partition_id=? ORDER BY id",
            (p["id"],),
        ).fetchall()

        try:
            # 同笔双落：元数据段键 与 湖目录对象键 逐段配对改名，两侧都必须命中
            for c in clips:
                old_key = c["object_key"]
                if not old_key.startswith(old_prefix):
                    raise HTTPException(
                        500, detail=f"素材段 {old_key} 不挂在分区当前前缀下，整笔回滚"
                    )
                seg = old_key[len(old_prefix):]
                new_key = new_prefix + seg
                _rename_one_side(
                    conn,
                    "UPDATE clips SET object_key=? WHERE id=? AND object_key=?",
                    (new_key, c["id"], old_key),
                    "元数据段",
                )
                _rename_one_side(
                    conn,
                    "UPDATE inventory_objects SET object_key=? WHERE object_key=?",
                    (new_key, old_key),
                    "目录对象",
                )

            # 温层字段 与 目录归属前缀 同笔切换
            conn.execute(
                "UPDATE partitions SET tier=?, object_prefix=? WHERE id=?",
                (to_tier, new_prefix, p["id"]),
            )
            # 完成态 / 迁移单终态 同笔落
            conn.execute(
                "UPDATE migrate_jobs SET status='已完成', finished_at=?, note=? WHERE id=?",
                (now(), f"同笔双落：{old_prefix} → {new_prefix}", job_id),
            )
        except HTTPException:
            raise
        except sqlite3.IntegrityError as exc:
            # 唯一键冲突等：事务随 with 退出整体回滚
            raise HTTPException(409, detail=f"落账冲突，整笔回滚：{exc}")

        return {
            "job_id": job_id,
            "status": "已完成",
            "tier": to_tier,
            "object_prefix": new_prefix,
            "renamed": len(clips),
            "snapshot": snapshot,
        }

_finish_migrate_impl = finish_migrate


def finish_migrate(job_id: int, success: bool = True):
    out = _finish_migrate_impl(job_id, success=success)
    if success:
        return finish_surface.after_finish(job_id, out)
    return out

