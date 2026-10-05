# -*- coding: utf-8 -*-
"""(ΔV, BC) ↔ 求解器乘数：**全项目唯一的算法落点**。

两条公式与 `scripts/missile_metrics.py::metrics()`（权威口径）逐行同式；改动这里等于改动
"ΔV/BC 是什么"，必须同步 `docs/SOLVER-API.md` 与弹池回归。

    thrust_scale = ΔV_target / ΔV_blk
    cx_scale     = m_dry / (BC_target · SD · CxK_blk)        SD = π(d/2)²

**扫描时基准弹除推力与阻力系数之外一个量都不动** —— 乘数就是这么施加的：
`force`/`force1` 乘 `thrust_scale`（等价于改比冲），`CxK` 乘 `cx_scale`（等价于改阻力系数）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .spec import Scaling

G = 9.81                                     # 与权威口径同值（missile_metrics.G）


@dataclass(frozen=True)
class BlkMetrics:
    """从 vendor blk 现算出的权威量。**散点与乘数共用它**，不可能口径分叉。"""

    dv: float                # 真空等效总增速 [m/s]
    bc: float                # 简化弹道系数
    dry_kg: float            # 末级结束质量（= massEnd1，单级时 = massEnd）
    cxk: float
    caliber_m: float         # blk 里已是【米】，不要再当毫米
    sd_m2: float             # 迎面面积 π(d/2)²
    two_stage: bool
    fu1: float
    fu2: float
    isp1: float
    isp2: float

    def as_dict(self) -> dict:
        return {"dv": self.dv, "bc": self.bc, "dry_kg": self.dry_kg, "cxk": self.cxk,
                "caliber_m": self.caliber_m, "sd_m2": self.sd_m2,
                "two_stage": self.two_stage, "fu1": self.fu1, "fu2": self.fu2,
                "isp1": self.isp1, "isp2": self.isp2}


class MetricsError(ValueError):
    """blk 参数块缺少权威公式需要的字段。"""


def _num(profile: dict, key: str, *, required: bool = True) -> float | None:
    v = profile.get(key)
    if v is None:
        if required:
            raise MetricsError(f"blk 参数块缺 `{key}`，无法套权威公式")
        return None
    try:
        f = float(v)
    except (TypeError, ValueError) as exc:
        raise MetricsError(f"blk 参数块 `{key}` 不是数：{v!r}") from exc
    if not math.isfinite(f):
        raise MetricsError(f"blk 参数块 `{key}` 非有限值：{v!r}")
    return f


def surface_area_m2(caliber_m: float) -> float:
    """迎面面积 SD = π(d/2)²。`caliber` 在 blk 里已经是米。"""
    return math.pi * (float(caliber_m) / 2.0) ** 2


def vacuum_dv(stages, *, scale: float = 1.0) -> float:
    """逐级齐奥尔科夫斯基真空增速。`stages` = ((m_start, m_end, force, time_fire), …)。

    与 `scripts/missile_metrics.py` 的 dV1 + dV2 同式：每级
    `ve = force·timeFire/Δm`，`ΔV_i = ve·ln(m_start/m_end)`，逐级相加。
    """
    total = 0.0
    for m_start, m_end, force, time_fire in stages:
        dm = float(m_start) - float(m_end)
        if dm <= 0:
            continue                                  # 该级没有装药 ⇒ 不贡献
        ve = float(force) * float(scale) * float(time_fire) / dm
        if float(m_start) <= 0 or float(m_end) <= 0:
            raise MetricsError("质量必须是正数")
        total += ve * math.log(float(m_start) / float(m_end))
    return total


def stages_of(profile: dict):
    """blk 参数块 → 逐级 `(m_start, m_end, force, time_fire)`。

    本项目 11 型弹都是 `mass → massEnd → massEnd1` 的分级写法；
    `propulsion0..3`（1~4 台发动机）这一族在求解器里存在但没有本项目用到的型号，
    遇到就明确报错，**不做近似替代**（求解器自己对不支持的型号也是硬拒绝）。
    """
    if any(k.startswith("propulsion") for k in profile):
        raise MetricsError("该型号用 propulsion* 多发动机族，本项目权威公式只覆盖两级写法")
    mass = _num(profile, "mass")
    mass_end = _num(profile, "massEnd")
    mass_end1 = _num(profile, "massEnd1", required=False)
    force = _num(profile, "force")
    time_fire = _num(profile, "timeFire")
    if mass_end1 is None:
        return ((mass, mass_end, force, time_fire),)
    force1 = _num(profile, "force1", required=False)
    time_fire1 = _num(profile, "timeFire1", required=False)
    if force1 and time_fire1:
        return ((mass, mass_end, force, time_fire),
                (mass_end, mass_end1, force1, time_fire1))
    return ((mass, mass_end, force, time_fire),)


def blk_metrics(profile: dict) -> BlkMetrics:
    """权威公式（`scripts/missile_metrics.py::metrics()` 的逐行复刻）。

    两个曾算错的坑记在这里，防止重建时再踩：
      * 二级装药在 `massEnd1` 里，`mass − massEnd` **只是**一级装药；
      * `caliber` 是**米**，不是毫米。
    """
    stages = stages_of(profile)
    fu1 = float(profile["mass"]) - float(profile["massEnd"])
    isp1 = stages[0][2] * stages[0][3] / (G * fu1) if fu1 > 0 else math.nan
    dv = vacuum_dv(stages)

    if len(stages) > 1:
        _, m_mid, f1, t1 = stages[1]
        fu2 = float(profile["massEnd"]) - float(profile["massEnd1"])
        isp2 = f1 * t1 / (G * fu2) if fu2 > 0 else math.nan
        two_stage = True
    else:
        fu2, isp2, two_stage = 0.0, math.nan, False

    dry = float(profile["mass"]) - fu1 - fu2
    cxk = _num(profile, "CxK")
    caliber = _num(profile, "caliber")
    sd = surface_area_m2(caliber)
    if cxk <= 0 or sd <= 0:
        raise MetricsError(f"CxK 与口径都必须是正数（CxK={cxk}, caliber={caliber}）")
    return BlkMetrics(dv=dv, bc=dry / (cxk * sd), dry_kg=dry, cxk=cxk,
                      caliber_m=caliber, sd_m2=sd, two_stage=two_stage,
                      fu1=fu1, fu2=fu2, isp1=isp1, isp2=isp2)


# --------------------------------------------------------------------- 乘数


def thrust_scale_for(dv_target: float | None, dv_blk: float) -> float:
    """`ΔV_target / ΔV_blk`。未指定（None 或 0）⇒ 1.0（标称）。"""
    if not dv_target:
        return 1.0
    if not dv_blk or dv_blk <= 0:
        raise MetricsError("ΔV_blk 必须为正数才能反解推力乘数")
    return float(dv_target) / float(dv_blk)


def cx_scale_for(bc_target: float | None, metrics: BlkMetrics) -> float:
    """`m_dry / (BC_target · SD · CxK_blk)`。未指定 ⇒ 1.0（标称）。

    与 `io.cx_scale_for_bc` 同式 —— 反解的分子固定取**干重**，`--bc-anchor` 之类没有入口。
    """
    if not bc_target:
        return 1.0
    denom = float(bc_target) * metrics.sd_m2 * metrics.cxk
    if denom <= 0:
        raise MetricsError("BC_target · SD · CxK 必须为正数才能反解阻力乘数")
    return metrics.dry_kg / denom


def scaling_for(dv_target: float | None, bc_target: float | None,
                metrics: BlkMetrics) -> Scaling:
    """装配一次扫描格点要用的 `Scaling`（带自洽信息，进缓存与产物）。"""
    dv_t = float(dv_target) if dv_target else metrics.dv
    bc_t = float(bc_target) if bc_target else metrics.bc
    return Scaling(dv_target=dv_t, bc_target=bc_t, dv_blk=metrics.dv, bc_blk=metrics.bc,
                   thrust_scale=thrust_scale_for(dv_t, metrics.dv),
                   cx_scale=cx_scale_for(bc_t, metrics))
