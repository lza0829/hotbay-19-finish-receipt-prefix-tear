"""完成落账·同笔双落 架构一口径测试。

覆盖：
1. 成功落账：温层/分区前缀/段键/目录键/迁移单终态同笔切换
2. 任一侧失败整笔回滚到完成前快照（含人为打断双落）
3. 失败回执不改温层与目录
4. 并发双完成同一单：一成一拒，绝不一成一半

6. 总览：温层计数只认已对齐，未对齐单列不伪装
"""
from __future__ import annotations
import os
import sys
import tempfile
import threading
import traceback

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def conn_factory(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    monkeypatch.setenv("HOTBAY_DB", tmp.name)
    # 重新载入配置与数据库路径
    import importlib
    import app.config as config
    importlib.reload(config)
    import app.db as dbmod
    importlib.reload(dbmod)
    import app.services.core as core
    importlib.reload(core)
    import app.services.migrate as migrate
    importlib.reload(migrate)
    import app.services.partitions as parts_svc
    importlib.reload(parts_svc)
    import app.services.verify as verify_svc
    importlib.reload(verify_svc)
    import app.seed as seed
    importlib.reload(seed)
    seed.seed_if_empty()
    yield {
        "db": dbmod,
        "migrate": migrate,
        "verify": verify_svc,
        "partitions": parts_svc,
        "core": core,
        "path": tmp.name,
    }
    os.unlink(tmp.name)


def _executing_job(svc, dbmod, partition_id=1, to_tier="冷档"):
    return svc.start_migrate(partition_id, to_tier)["job_id"]


def _snapshot(dbmod, partition_id=1, job_id=None):
    with dbmod.db() as conn:
        p = conn.execute("SELECT * FROM partitions WHERE id=?", (partition_id,)).fetchone()
        clips = [r["object_key"] for r in conn.execute(
            "SELECT object_key FROM clips WHERE partition_id=? ORDER BY id", (partition_id,))]
        inv = [r["object_key"] for r in conn.execute(
            "SELECT object_key FROM inventory_objects ORDER BY object_key")]
        job = conn.execute("SELECT status FROM migrate_jobs WHERE id=?", (job_id,)).fetchone() if job_id else None
        return {
            "tier": p["tier"], "prefix": p["object_prefix"],
            "clips": clips, "inv": inv,
            "job": job["status"] if job else None,
        }


def test_success_dual_write_atomic(conn_factory):
    svc, dbmod = conn_factory["migrate"], conn_factory["db"]
    before = _snapshot(dbmod)
    job_id = _executing_job(svc, dbmod)
    receipt = svc.finish_migrate(job_id, True)

    assert receipt["status"] == "已完成"
    assert receipt["renamed"] == 3
    after = _snapshot(dbmod, job_id=job_id)
    # 温层切到冷档
    assert after["tier"] == "冷档"
    # 目录前缀同源切换
    assert after["prefix"] == "cold/P-红海2024-主成片/"
    assert after["prefix"] != before["prefix"]
    # 段键全部挂新前缀
    assert all(k.startswith("cold/P-红海2024-主成片/") for k in after["clips"])
    # 湖目录对象键同步改名，旧键一个不留
    assert not any(k.startswith(before["prefix"]) for k in after["inv"])
    assert all(k in after["inv"] for k in after["clips"])
    # 迁移单终态
    assert after["job"] == "已完成"


def test_failure_rolls_back_entire_tx(conn_factory, monkeypatch):
    svc, dbmod = conn_factory["migrate"], conn_factory["db"]
    job_id = _executing_job(svc, dbmod)
    before = _snapshot(dbmod, job_id=job_id)

    real = svc._rename_one_side
    calls = {"n": 0}

    def flaky(conn, sql, params, side):
        calls["n"] += 1
        # 元数据段已改、轮到目录对象时人为打断：必须整笔回滚
        if calls["n"] == 2:
            raise RuntimeError("人为打断双落")
        return real(conn, sql, params, side)

    monkeypatch.setattr(svc, "_rename_one_side", flaky)
    with pytest.raises(RuntimeError):
        svc.finish_migrate(job_id, True)

    after = _snapshot(dbmod, job_id=job_id)
    # 全部回到完成前快照：温层、前缀、段键、目录键、单据态
    assert after == before
    # 单据仍是执行中，未留下半截完成态
    assert after["job"] == "执行中"


def test_inventory_missing_side_full_rollback(conn_factory):
    svc, dbmod = conn_factory["migrate"], conn_factory["db"]
    job_id = _executing_job(svc, dbmod)
    before = _snapshot(dbmod, job_id=job_id)
    # 删掉一个目录对象，制造「目录侧缺失」
    with dbmod.db() as conn:
        conn.execute("DELETE FROM inventory_objects WHERE object_key=?", (before["clips"][0],))

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        svc.finish_migrate(job_id, True)
    assert ei.value.status_code == 500

    after = _snapshot(dbmod, job_id=job_id)
    # 元数据段键也不得先改：整笔回到快照
    assert after["clips"] == before["clips"]
    assert after["tier"] == before["tier"]
    assert after["prefix"] == before["prefix"]
    assert after["job"] == "执行中"


def test_explicit_failure_keeps_snapshot(conn_factory):
    svc, dbmod = conn_factory["migrate"], conn_factory["db"]
    job_id = _executing_job(svc, dbmod)
    before = _snapshot(dbmod, job_id=job_id)
    out = svc.finish_migrate(job_id, False)
    assert out["status"] == "已失败"
    after = _snapshot(dbmod, job_id=job_id)
    assert after["tier"] == before["tier"]
    assert after["prefix"] == before["prefix"]
    assert after["clips"] == before["clips"]
    assert after["inv"] == before["inv"]
    assert after["job"] == "已失败"


def test_concurrent_double_finish_one_succeeds_one_rejected(conn_factory):
    svc, dbmod = conn_factory["migrate"], conn_factory["db"]
    job_id = _executing_job(svc, dbmod)
    before = _snapshot(dbmod, job_id=job_id)
    results = []
    errors = []
    barrier = threading.Barrier(2)

    def worker():
        try:
            barrier.wait()
            results.append(svc.finish_migrate(job_id, True))
        except Exception as e:  # noqa: BLE001
            errors.append(f"{type(e).__name__}: {getattr(e, 'detail', str(e))}")

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start(); t2.start(); t1.join(); t2.join()

    assert len(results) == 1, "只能有一笔完成成功"
    assert len(errors) == 1, "第二笔必须被拒"
    assert "终态" in errors[0] or "执行中" in errors[0]
    after = _snapshot(dbmod, job_id=job_id)
    assert after["job"] == "已完成"
    # 不存在一成一半：要么整笔完成，要么整笔未动
    assert after["tier"] == "冷档"
    assert all(k.startswith("cold/") for k in after["clips"])


def test_verify_green_after_clean_dual_write(conn_factory):
    svc, verify_svc, dbmod = conn_factory["migrate"], conn_factory["verify"], conn_factory["db"]
    job_id = _executing_job(svc, dbmod)
    svc.finish_migrate(job_id, True)
    r = verify_svc.run_verify()
    assert r["status"] == "一致", "同笔双落成功不得新增可解释漂移"
    assert r["drift_count"] == 0
    assert r["integrity_drifts"] == []


def test_verify_catches_half_drift_no_false_green(conn_factory):
    verify_svc, dbmod = conn_factory["db"], conn_factory["verify"]
    # 手工制造半截漂移：温层已冷档，但前缀仍挂热档（绕过同笔双落直接改库）
    with dbmod.db() as conn:
        conn.execute("UPDATE partitions SET tier='冷档' WHERE id=1")
    r = conn_factory["verify"].run_verify()
    assert r["status"] == "漂移"
    assert r["drift_count"] > 0
    problems = {d["问题"] for d in r["integrity_drifts"]}
    assert "目录前缀与温层不一致" in problems
    # 漂移条必须能定位到具体分区
    assert any(d["分区"] == "P-红海2024-主成片" for d in r["integrity_drifts"])


def test_verify_catches_segment_prefix_drift(conn_factory):
    dbmod = conn_factory["db"]
    with dbmod.db() as conn:
        # 段键脱离分区当前前缀
        conn.execute("UPDATE clips SET object_key='stray/seg-01.bin' WHERE id=1")
    r = conn_factory["verify"].run_verify()
    assert r["status"] == "漂移"
    assert any(d["问题"] == "素材段脱离分区前缀" for d in r["integrity_drifts"])


def test_overview_counts_only_aligned(conn_factory):
    from app.routers import dashboard
    svc, dbmod = conn_factory["migrate"], conn_factory["db"]
    # 初始：热档2、温档1、冷档1
    o1 = dashboard.overview()
    assert o1["前缀未对齐"] == 0
    assert (o1["热档"], o1["温档"], o1["冷档"]) == (2, 1, 1)

    # 把一个热档分区同笔双落到冷档
    job_id = _executing_job(svc, dbmod, partition_id=1, to_tier="冷档")
    svc.finish_migrate(job_id, True)
    o2 = dashboard.overview()
    assert o2["前缀未对齐"] == 0
    assert (o2["热档"], o2["冷档"]) == (1, 2)

    # 制造半截漂移：未对齐分区不得计入任何温层卡
    with dbmod.db() as conn:
        conn.execute("UPDATE partitions SET tier='温档' WHERE id=1")
    o3 = dashboard.overview()
    assert o3["前缀未对齐"] == 1
    assert o3["未对齐分区"][0]["part_key"] == "P-红海2024-主成片"


def test_repeat_finish_rejected(conn_factory):
    svc, dbmod = conn_factory["migrate"], conn_factory["db"]
    job_id = _executing_job(svc, dbmod)
    svc.finish_migrate(job_id, True)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        svc.finish_migrate(job_id, True)
    assert ei.value.status_code == 400
