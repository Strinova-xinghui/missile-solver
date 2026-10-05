# -*- coding: utf-8 -*-
"""① 内核身份：包内 `kernel/wt_missile.py` 与两处哈希常量、内核自报的 MODEL/VERSION 必须一致。

前三条**不需要游戏数据**（只读包内文件与常量）；最后一条要读 `manifest.json` ⇒ 没数据时
由运行器记成 SKIP（口径：数据缺席不是失败、也不算通过）。
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "src", ROOT / "tests"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from missile_solver import _data, solver, spec            # noqa: E402

#: 从现有仓 `src/missile_sim/solver.py` 抄来的身份常量（抽取时的快照，别改）。
GOLD_SOLVER_SHA256 = "dad09caea2c31e0ef26a8327f3bb96c50f83b91bcb29d6b05616f23faba2f730"
GOLD_MODEL = "python-game-6dof-v1"
GOLD_VERSION = "2.59.0.28"
GOLD_N_PRESETS = 168
GOLD_ARCHIVE_SHA256 = "303760d4897dd688fdc9d2503d1db8f6978c2d3bc6eef3d064ae2b48f26d6e9b"


def test_packaged_kernel_matches_both_hash_constants():
    """包内内核的 sha256 == `solver.SOLVER_SHA256` == `_data.KERNEL_SHA256`（三处任一不一致就红）。"""
    f = _data.KERNEL_DIR / _data.KERNEL_FILE
    assert f.is_file(), f"包内缺内核：{f}"
    got = hashlib.sha256(f.read_bytes()).hexdigest()
    assert got == GOLD_SOLVER_SHA256, f"内核被改过（现 {got}）"
    assert solver.SOLVER_SHA256 == GOLD_SOLVER_SHA256
    assert _data.KERNEL_SHA256 == GOLD_SOLVER_SHA256


def test_kernel_source_declares_the_expected_identity():
    """内核源码里自报的 MODEL / VERSION / 归档哈希（字符串级核对，不 import 内核）。"""
    src = (_data.KERNEL_DIR / _data.KERNEL_FILE).read_text(encoding="utf-8")
    assert re.search(rf"MODEL\s*=\s*['\"]{re.escape(GOLD_MODEL)}['\"]", src), "MODEL 不符"
    assert re.search(rf"VERSION\s*=\s*['\"]{re.escape(GOLD_VERSION)}['\"]", src), "VERSION 不符"
    assert re.search(r"ELF_SHA256\s*=\s*['\"][0-9a-f]{64}['\"]", src), "内核没声明 ELF 基准哈希"
    assert "inputs/resources" in src, "内核没写死数据目录？那本包的宿主物化逻辑要重看"


def test_paths_point_into_the_package_not_a_repo():
    """抽取的痕迹：路径必须落在**包内 / 用户缓存**，不许指回上游抽取源仓库的目录名。"""
    assert spec.PACKAGE_DIR.is_dir() and (spec.PACKAGE_DIR / "kernel").is_dir()
    assert spec.KERNEL_DIR == spec.PACKAGE_DIR / "kernel"
    assert "导弹包线图" not in str(_data.KERNEL_HOME), _data.KERNEL_HOME
    assert _data.KERNEL_HOME.name == GOLD_SOLVER_SHA256[:12], _data.KERNEL_HOME


def test_identity_block_values():
    """`solver.identity()` 的身份块（要读 manifest ⇒ 没数据时 SKIP）。"""
    ident = solver.identity()
    assert ident["solver"] == "wt-missile"
    assert ident["model"] == GOLD_MODEL
    assert ident["version"] == GOLD_VERSION
    assert ident["sha256"] == GOLD_SOLVER_SHA256
    assert int(ident["n_profiles"]) == GOLD_N_PRESETS
    assert ident["elf_executed"] is False
    assert str(ident["archive_sha256"]) == GOLD_ARCHIVE_SHA256
    # 输出 schema：68 列（现有仓 2026-10-02 扩列后的硬契约），末三列是那三个新增量
    assert int(ident["output_columns"]) == 68, ident["output_columns"]
    assert len(ident["output_columns_tail"]) == 3
