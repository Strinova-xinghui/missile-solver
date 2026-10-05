# -*- coding: utf-8 -*-
"""跑判据之前先问一句："游戏数据在位吗？"（`tests/run_tests.py` 原样复用它）。

⚠ **文件名故意叫 `_vendor_data`**：`tests/run_tests.py` 是从现有仓**逐字节抄来的**，
它 `import _vendor_data` 并读 `ready()` / `missing_reason()` / `HOWTO` —— 换个名字就得改运行器。
本仓没有 `vendor/` 目录了，数据位置由**环境变量 `DATA_DIR`** 给（见 `data/README.md`）。

口径与上游一致：**数据缺席不是失败、也不算通过** —— 运行器把因此抛出的异常记成 SKIP。
"""

from __future__ import annotations

import os
from pathlib import Path

DATA_ENV = "DATA_DIR"
HOWTO = ("取数据与校验的步骤见 data/README.md；"
         "脚本：python scripts/fetch_data.py --help")

#: 内核要读的三样（`manifest.json` / `presets.json` / 168 份 `*.blk`）。
_REQUIRED = ("manifest.json", "presets.json")


def data_dir() -> Path | None:
    raw = (os.environ.get(DATA_ENV) or "").strip()
    return Path(raw).expanduser() if raw else None


def ready() -> bool:
    """`DATA_DIR` 设了、是目录、且里面那套数据看着成套。"""
    p = data_dir()
    if p is None or not p.is_dir():
        return False
    if any(not (p / n).is_file() for n in _REQUIRED):
        return False
    return len(list(p.glob("*.blk"))) >= 160


def missing_reason() -> str:
    p = data_dir()
    if p is None:
        return f"{DATA_ENV} 没设 —— 本仓不分发游戏数据（权利归 Gaijin）"
    if not p.is_dir():
        return f"{DATA_ENV}={p} 不是目录"
    miss = [n for n in _REQUIRED if not (p / n).is_file()]
    if miss:
        return f"{DATA_ENV}={p} 里缺 {miss}"
    return f"{DATA_ENV}={p} 里只有 {len(list(p.glob('*.blk')))} 份 *.blk（期望 ≥168）"
