# -*- coding: utf-8 -*-
"""② 金标工况：PL-12 / 水平 50 km / 10 km 同高 / 双方 300 m/s / ideal。

值**从现有仓的判据抄来**（`tests/test_solver_api.py` 的 `GOLD_T_HIT` / `GOLD_MAX_ALT`，
出处是 `docs/NEW-SOLVER-HANDOFF.md` §4），不是我另算的：

    t_hit = 45.1861 s（±5e-4）、max_alt = 12725.10 m（±5e-2）

⚠ 这里用 **`standard`（纯 Python）档**跑：`fast` 档要 C 编译器（或本机已编好的 fast 缓存），
本仓的判据不该依赖工具链；两档数值逐位一致是上游已有的判据，各自测各自的。
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "src", ROOT / "tests"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pytest  # noqa: E402

from missile_solver import solver, spec                    # noqa: E402

GOLD_T_HIT = 45.1861
GOLD_MAX_ALT = 12725.10


@pytest.fixture(scope="module")
def gold():
    """默认场景（`Scene()` = 50 km 迎头 / 10 km 高 / 双方 300 m/s）打一枚 PL-12。"""
    return solver.run(spec.Scene(), missile="cn_pl12", tier="standard")


def test_gold_hit_time_and_apex(gold):
    assert gold.t_hit == pytest.approx(GOLD_T_HIT, abs=5e-4), \
        "金标 t_hit 变了 —— 要么内核换了，要么乘数被悄悄改了"
    assert gold.max_alt_m == pytest.approx(GOLD_MAX_ALT, abs=5e-2)
    assert gold.hit and gold.t_hit > 0


def test_trajectory_rows_are_the_declared_schema():
    """逐采样数据的形状：内核声明 68 列，`solver.run(want_rows=True)` 给**精简行**。

    消费方按**列名**读（别按位置）：精简行是"重命名后的固定键集"，其中内核那三个扩展列
    （`mach` / `aoa_deg` / `aoa_eff_deg`）必须**原样带出来**（外壳只搬运、不派生）。
    """
    cols = solver.output_columns()
    assert len(cols) == 68 and len(set(cols)) == 68
    assert cols[-3:] == ("mach", "aoa_deg", "aoa_eff_deg"), cols[-4:]
    shot = solver.run(spec.Scene(), missile="cn_pl12", tier="standard", want_rows=True)
    assert shot.rows, "want_rows=True 应当带回逐采样行"
    keys = set(shot.rows[0])
    assert all(set(r) == keys for r in shot.rows), "每一行的键集必须一致"
    # 内核扩展列必须被带出来（2026-10-02 起是硬契约）
    assert {"mach", "aoa_deg", "aoa_eff_deg"} <= keys, keys
    # 精简行的固定键（坐标口径：水平面 (x, z)、y 是高度）
    assert {"t", "x", "y", "z", "v", "mass", "thrust", "range", "dist_flown"} <= keys, keys
    # ⚠ `shot.rows` 已经是精简后的行（`shot_from()` 内部过了 `trajectory()`）；
    # `solver.trajectory()` 是给**内核原始行**用的，别再套一层（会把键读空）。
    assert shot.rows[0]["t"] == 0.0
    assert shot.rows[0]["y"] == pytest.approx(10_000.0, abs=1e-6)   # 起手高度
    assert shot.rows[-1]["range"] < shot.rows[0]["range"]          # 在接近
    assert shot.rows[-1]["t"] == pytest.approx(GOLD_T_HIT, abs=5e-4)  # 末行 = 命中时刻


def test_scenario_mapping_is_the_same_number_as_the_source_repo():
    """`scenario_dict()` 的几何折算：水平 50 km → 位置/速度向量（与上游同一口径）。"""
    d = solver.scenario_dict(spec.Scene(), missile="cn_pl12")
    assert d["missile"] == "cn_pl12"
    assert d["version"] == "2.59.0.28"
    assert d["target"]["position_m"][0] == pytest.approx(50_000.0, rel=1e-12)
    assert d["launch"]["position_m"][1] == pytest.approx(10_000.0, rel=1e-12)
    assert d["target"]["velocity_m_s"][0] == pytest.approx(-300.0, rel=1e-12)
    assert d["launch"]["velocity_m_s"][0] == pytest.approx(300.0, rel=1e-12)


def test_a_miss_is_nan_not_zero():
    """不命中必须是 `nan`（0 会被下游当成"命中在 t=0"）。"""
    far = spec.Scene(range_m=200_000.0)
    shot = solver.run(far, missile="cn_pl12", tier="standard", want_cpa=True)
    assert math.isnan(shot.t_hit), shot.t_hit
