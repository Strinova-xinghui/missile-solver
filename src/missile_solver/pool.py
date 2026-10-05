# -*- coding: utf-8 -*-
"""11 弹池：短名 ↔ vendor 预设名 ↔ 图上标签，以及**现算**的权威 (ΔV, BC)。

与旧实现的根本区别：**不再读 `results/missile_standards.json` 之类的 JSON 池**。
ΔV/BC 直接从 `vendor/wt-missile/` 的 blk 按权威公式现算（`mapping.blk_metrics`），
所以散点与求解器物理**同源**，也不可能出现"池里写的是一个版本、引擎跑的是另一个版本"。

已实测：11 型的现算值与既有权威表（`results/missile_standards.json` 的 `impulse.dv/bc`）
**逐值一致** —— 这是本模块的回归基准。
"""

from __future__ import annotations

from dataclasses import dataclass

from . import mapping, solver
from .spec import DEFAULT_MISSILE, VERSION_28, MissilePoint


@dataclass(frozen=True)
class PoolEntry:
    """池内一项：短名（用户输入用）、vendor 预设名、图上标签、全名。"""

    key: str            # 用户输入与产物里的短名，如 "PL-12"
    native: str         # vendor 预设名，如 "cn_pl12"
    label: str          # 图上标签（与 pool 键一致，便于对号）
    full_name: str      # 完整型号名，如 "PL-12"


#: 弹池（顺序即图例与产物里的顺序）。默认基准弹 = `spec.DEFAULT_MISSILE`。
POOL: tuple = (
    PoolEntry("120A", "us_aim_120a", "120A", "AIM-120A/B"),
    PoolEntry("120C", "us_aim_120c_5", "120C", "AIM-120C-5/7"),
    PoolEntry("120D", "us_aim_120d", "120D", "AIM-120D"),
    PoolEntry("MICA", "fr_mica_em", "MICA", "MICA-EM"),
    PoolEntry("AAM-4", "jp_aam4", "AAM-4", "AAM-4"),
    PoolEntry("PL-12", "cn_pl12", "PL-12", "PL-12"),
    PoolEntry("PL-12A", "cn_pl12a", "PL-12A", "PL-12A"),
    PoolEntry("R-77", "su_r_77", "R-77", "R-77/RVV-AE"),
    PoolEntry("R-77-1", "su_r_77_1", "R-77-1", "R-77-1/RVV-SD"),
    PoolEntry("Derby", "il_derby", "Derby", "Derby"),
    PoolEntry("R-Darter", "r_darter", "R-Darter", "R-Darter"),
)

_BY_KEY = {e.key: e for e in POOL}
_NORM = {e.key.lower().replace("_", "-"): e for e in POOL}


class PoolError(KeyError):
    """弹名不在池里。**不做近似替代** —— 求解器对不支持的型号也是硬拒绝。"""


def names() -> list:
    """池内全部短名（顺序 = `POOL`）。"""
    return [e.key for e in POOL]


def resolve(name: str) -> PoolEntry:
    """短名 → 池项。大小写与下划线不敏感；未知名字给出最接近的建议。"""
    key = str(name or "").strip()
    if key in _BY_KEY:
        return _BY_KEY[key]
    hit = _NORM.get(key.lower().replace("_", "-"))
    if hit is not None:
        return hit
    near = [e.key for e in POOL if key.lower() in e.key.lower() or e.key.lower() in key.lower()]
    hint = f"；是否想写 {' / '.join(near)}？" if near else ""
    raise PoolError(f"弹池里没有 {name!r}。可选：{' / '.join(names())}{hint}")


def resolve_many(text) -> list:
    """把 `--missiles` 的输入折成池项列表。

    接受：None（全池）、逗号/空格分隔的字符串、短名序列。
    重复项**保留用户给的顺序**并去重，方便按需裁剪图例。
    """
    if text is None or text == "" or text == "all":
        return list(POOL)
    if isinstance(text, str):
        raw = [s for s in text.replace(",", " ").split() if s]
    else:
        raw = list(text)
    out, seen = [], set()
    for item in raw:
        e = item if isinstance(item, PoolEntry) else resolve(item)
        if e.key not in seen:
            seen.add(e.key)
            out.append(e)
    if not out:
        raise PoolError("一个弹都没选中")
    return out


def metrics_of(entry: PoolEntry, version: str | None = None) -> mapping.BlkMetrics:
    """该型的权威量（现算）。"""
    return mapping.blk_metrics(solver.profile(entry.native, version))


def point_of(entry: PoolEntry, version: str | None = None) -> MissilePoint:
    """池项 → 散点用的一点。"""
    m = metrics_of(entry, version)
    return MissilePoint(key=entry.key, label=entry.label, dv=m.dv, bc=m.bc,
                        dry_kg=m.dry_kg, cxk=m.cxk, caliber_m=m.caliber_m,
                        two_stage=m.two_stage, native=entry.native)


def points(keys=None, *, version: str | None = None) -> list:
    """散点数据集：`keys=None` ⇒ 全 11 弹；否则按给定短名（顺序照用户给的）。"""
    entries = resolve_many(keys)
    return [point_of(e, version) for e in entries]


def standard_entry(name: str | None = None) -> PoolEntry:
    """基准弹解析（默认 `spec.DEFAULT_MISSILE`）。"""
    return resolve(name or DEFAULT_MISSILE)


def pool_table(keys=None, *, version: str | None = None) -> list:
    """产物用的一张表：短名 / 全名 / vendor 预设名 / ΔV / BC / 干重 / CxK / 口径。"""
    rows = []
    for p in points(keys, version=version):
        rows.append({"key": p.key, "label": p.label, "full_name": _full(p),
                     "native": p.native, "dv": p.dv, "bc": p.bc,
                     "dry_kg": p.dry_kg, "cxk": p.cxk, "caliber_m": p.caliber_m,
                     "two_stage": p.two_stage})
    return rows


def _full(p: MissilePoint) -> str:
    e = _BY_KEY.get(p.key)
    return e.full_name if e else p.label


def version() -> str:
    """池数据实际来自哪个资源版本（由求解器自报）。"""
    return str(solver.identity().get("version") or VERSION_28)
