from __future__ import annotations
import json
from app.db import db
from app.services.core import now, prefix_for, tier_of_prefix


def inventory_list():
    with db() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM inventory_objects ORDER BY object_key").fetchall()]


def run_verify():
    with db() as conn:
        meta_keys = {
            r["object_key"]: r["bytes"]
            for r in conn.execute(
                '''SELECT c.object_key, c.bytes FROM clips c
                   JOIN partitions p ON p.id=c.partition_id
                   WHERE p.status IN ('在册','已过期')'''
            ).fetchall()
        }
        inv_keys = {
            r["object_key"]: r["bytes"]
            for r in conn.execute("SELECT object_key, bytes FROM inventory_objects").fetchall()
        }
        only_meta = sorted(set(meta_keys) - set(inv_keys))
        only_inv = sorted(set(inv_keys) - set(meta_keys))
        size_drift = sorted(
            k for k in set(meta_keys) & set(inv_keys) if meta_keys[k] != inv_keys[k]
        )
        drifts = []
        for k in only_meta:
            drifts.append({"对象键": k, "问题": "元数据有、目录无"})
        for k in only_inv:
            drifts.append({"对象键": k, "问题": "目录有、元数据无"})
        for k in size_drift:
            drifts.append({"对象键": k, "问题": "字节不一致"})

        # 落账完整性：同笔双落口径下，温层与目录前缀必须始终对齐。
        # 任一处对不上即为半截漂移，互证不得给绿。
        integrity = []
        parts = conn.execute(
            '''SELECT p.id, p.part_key, p.tier, p.object_prefix, p.status
               FROM partitions p WHERE p.status IN ('在册','已过期')'''
        ).fetchall()
        for p in parts:
            expected_prefix = prefix_for(p["tier"], p["part_key"])
            # 1) 分区目录归属前缀必须与温层同源
            if p["object_prefix"] != expected_prefix:
                hung = tier_of_prefix(p["object_prefix"])
                integrity.append({
                    "分区": p["part_key"],
                    "问题": "目录前缀与温层不一致",
                    "明细": f"温层={p['tier']} 应挂 {expected_prefix}，实挂 {p['object_prefix']}"
                            + (f"（仍挂{hung}）" if hung else "（非温层前缀）"),
                })
            # 2) 每个素材段必须挂在本分区当前前缀下
            for c in conn.execute("SELECT object_key FROM clips WHERE partition_id=?", (p["id"],)).fetchall():
                key = c["object_key"]
                if not key.startswith(p["object_prefix"]):
                    integrity.append({
                        "分区": p["part_key"],
                        "问题": "素材段脱离分区前缀",
                        "明细": f"{key} 不挂在 {p['object_prefix']}",
                    })
                    continue
                # 3) 段键前缀根反查出的温层必须等于分区温层（温层已冷、前缀仍热即在此现形）
                seg_tier = tier_of_prefix(p["object_prefix"])
                if seg_tier is not None and seg_tier != p["tier"]:
                    integrity.append({
                        "分区": p["part_key"],
                        "问题": "段键温层归属漂移",
                        "明细": f"{key} 前缀归属{seg_tier}，分区温层为{p['tier']}",
                    })

        drift_count = len(drifts) + len(integrity)
        status = "一致" if drift_count == 0 else "漂移"
        cur = conn.execute(
            "INSERT INTO verify_runs(status, drift_count, detail, created_at) VALUES (?,?,?,?)",
            (status, drift_count, json.dumps({"对象漂移": drifts, "落账完整性": integrity}, ensure_ascii=False), now()),
        )
        return {
            "run_id": cur.lastrowid,
            "status": status,
            "drift_count": drift_count,
            "drifts": drifts,
            "integrity_drifts": integrity,
            "meta_count": len(meta_keys),
            "inventory_count": len(inv_keys),
        }


def list_verify_runs():
    with db() as conn:
        out = []
        for r in conn.execute("SELECT * FROM verify_runs ORDER BY id DESC").fetchall():
            d = dict(r)
            try:
                parsed = json.loads(d["detail"])
            except (json.JSONDecodeError, TypeError):
                parsed = d["detail"]
            d["detail_parsed"] = parsed
            out.append(d)
        return out
