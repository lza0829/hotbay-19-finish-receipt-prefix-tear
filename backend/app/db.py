from __future__ import annotations
import sqlite3
from contextlib import contextmanager
from app.config import DB_PATH

SCHEMA = '''
CREATE TABLE IF NOT EXISTS vaults (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  capacity INTEGER NOT NULL,
  active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS partitions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  part_key TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  release_day TEXT NOT NULL,
  vault_id INTEGER NOT NULL,
  tier TEXT NOT NULL,
  object_prefix TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT '在册',
  created_at TEXT NOT NULL,
  FOREIGN KEY(vault_id) REFERENCES vaults(id)
);
CREATE TABLE IF NOT EXISTS clips (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  partition_id INTEGER NOT NULL,
  object_key TEXT NOT NULL UNIQUE,
  bytes INTEGER NOT NULL,
  entered_day TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(partition_id) REFERENCES partitions(id)
);
CREATE TABLE IF NOT EXISTS migrate_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  partition_id INTEGER NOT NULL,
  from_tier TEXT NOT NULL,
  to_tier TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  finished_at TEXT,
  note TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(partition_id) REFERENCES partitions(id)
);
CREATE TABLE IF NOT EXISTS retention_policies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  max_age_days INTEGER NOT NULL,
  action TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS destroy_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  partition_id INTEGER NOT NULL,
  policy_id INTEGER NOT NULL,
  mode TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(partition_id) REFERENCES partitions(id),
  FOREIGN KEY(policy_id) REFERENCES retention_policies(id)
);
CREATE TABLE IF NOT EXISTS inventory_objects (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  object_key TEXT NOT NULL UNIQUE,
  bytes INTEGER NOT NULL,
  vault_name TEXT NOT NULL,
  scanned_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS verify_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  status TEXT NOT NULL,
  drift_count INTEGER NOT NULL,
  detail TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
'''


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    # 写锁最多等待 5s，并发完成时第二个请求等待而非立刻 database is locked
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


@contextmanager
def db():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def write_tx():
    """同笔双落专用：立即取写锁的单事务，块内任一侧失败整体回滚。

    BEGIN IMMEDIATE 在第一条语句即拿到 RESERVED 写锁，
    把「校验执行中 → 落账」的读改写收进同一把锁内，杜绝并发双完成。
    """
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with db() as conn:
        conn.executescript(SCHEMA)
