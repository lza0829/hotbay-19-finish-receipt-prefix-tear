from __future__ import annotations
import os
from pathlib import Path

PROJECT_NAME = "热档片间"
ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path(os.environ.get("HOTBAY_DB", str(ROOT / "data" / "hotbay.db")))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
TIERS = ("热档", "温档", "冷档")
TIER_ORDER = {"热档": 0, "温档": 1, "冷档": 2}
# 温层 → 目录归属前缀根。全局唯一事实源：落账、互证、总览都听它。
# 架构一口径：同笔双落，温层与目录前缀必须同成败，不存在滞后态。
TIER_PREFIX_ROOT = {"热档": "hot", "温档": "warm", "冷档": "cold"}
