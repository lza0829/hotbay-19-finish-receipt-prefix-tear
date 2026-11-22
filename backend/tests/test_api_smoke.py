"""HTTP 端到端冒烟：完成落账 → 分区前缀 → 互证漂移条 → 总览 全链路联动。"""
from __future__ import annotations
import importlib
import os
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture()
def client(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    monkeypatch.setenv("HOTBAY_DB", tmp.name)
    import app.config as config
    importlib.reload(config)
    import app.db as dbmod
    importlib.reload(dbmod)
    import app.seed as seed
    importlib.reload(seed)
    import app.main as main
    importlib.reload(main)
    seed.seed_if_empty()
    with TestClient(main.app) as c:
        yield c
    os.unlink(tmp.name)


def test_full_settlement_flow(client):
    parts = client.get("/api/分区").json()
    target = next(p for p in parts if p["part_key"] == "P-红海2024-主成片")
    assert target["tier"] == "热档"
    assert target["object_prefix"].startswith("hot/")

    started = client.post("/api/迁移", json={"partition_id": target["id"], "to_tier": "冷档"}).json()
    job_id = started["job_id"]

    # 并发双完成：两笔都发出，恰好一成一拒（每线程独立客户端，共享同一 DB）
    import threading
    statuses, codes = [], []
    bar = threading.Barrier(2)

    def call():
        bar.wait()
        with TestClient(client.app) as tc:
            r = tc.post(f"/api/迁移/{job_id}/完成", json={"success": True})
        statuses.append(r.status_code)
        codes.append(r.json())

    t1, t2 = threading.Thread(target=call), threading.Thread(target=call)
    t1.start(); t2.start(); t1.join(); t2.join()
    assert sorted(statuses) == [200, 400]

    receipt = next(x for x in codes if isinstance(x, dict) and x.get("status") == "已完成")
    assert receipt["tier"] == "冷档"
    assert receipt["object_prefix"] == "cold/P-红海2024-主成片/"
    assert receipt["renamed"] == 3
    assert "snapshot" in receipt

    # 分区页前缀列已切
    parts2 = {p["id"]: p for p in client.get("/api/分区").json()}
    assert parts2[target["id"]]["tier"] == "冷档"
    assert parts2[target["id"]]["object_prefix"] == "cold/P-红海2024-主成片/"

    # 目录清单键已同步，无旧热档键残留
    inv = [x["object_key"] for x in client.get("/api/目录清单").json()]
    assert all(not k.startswith("hot/P-红海2024-主成片/") for k in inv)
    assert sum(k.startswith("cold/P-红海2024-主成片/") for k in inv) == 3


    v = client.post("/api/互证", json={}).json()
    assert v["status"] == "一致"
    assert v["integrity_drifts"] == []

    # 总览：未对齐为 0，热档减一、冷档加一
    o = client.get("/api/总览").json()
    assert o["前缀未对齐"] == 0
    assert o["热档"] == 1 and o["冷档"] == 2


def test_failed_finish_keeps_snapshot(client):
    parts = client.get("/api/分区").json()
    target = next(p for p in parts if p["tier"] != "冷档")
    job_id = client.post("/api/迁移", json={"partition_id": target["id"], "to_tier": "冷档"}).json()["job_id"]
    r = client.post(f"/api/迁移/{job_id}/完成", json={"success": False})
    assert r.json()["status"] == "已失败"
    again = client.get("/api/分区").json()
    same = next(p for p in again if p["id"] == target["id"])
    assert same["tier"] == target["tier"]
    assert same["object_prefix"] == target["object_prefix"]
