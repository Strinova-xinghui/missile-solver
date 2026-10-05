# -*- coding: utf-8 -*-
"""全弹池目录：把内核认得的**全部**预设算成三张图要用的坐标（ΔV / β / γ / ginv）。

**为什么有这个东西**（用户 2026-10-04 口径）：三张图要能按"用户自选弹池"**实时**自适应，
就要求弹的坐标在**页面本地**可得。于是把全表在构建期算好、打成 `results/missile_catalog.json`
随包（也随静态站）发布 —— 静态站只发**派生数值**，**不发** Gaijin 的 blk 文件；
这份判定的两条阻塞见 `docs/STATIC-LAYER-REALTIME.md` §1。

**单一出处（第一原则）**：本模块**不写任何物理公式** ——
ΔV/BC 走 `mapping.blk_metrics()`，β/γ 走 `bg.point_of()`，预设名单走 `solver.preset_names()`，
参考工况走 `bg.reference()`。它只负责**枚举 / 折叠 / 打包 / 自校验**。

**命名与折叠规则**（写死，避免 UI 抖动）：

* 我们默认对比集的 11 弹沿用 `pool.POOL` 的短名与全名（`in_pool11 = True`）；
* 其余型号 `key = native`，`label = key`；
* `*_default` 是同一型号的孪生条目 ⇒ 标 `duplicate_of = 去掉后缀的名字`、`default_hidden = True`，
  **保留在表里但默认不显示**（不删，删了总数就对不上内核预设表了）；
* 不可算的型号（当前是 `cn_pl15*` 与 `us_aim_54a/b/c/c_plus*`，`propulsion*` 多发动机族）
  进 `unsupported`，**带内核给出的原话原因**，不猜、不近似。
"""

from __future__ import annotations

import json
from collections import namedtuple
from datetime import datetime, timezone
from pathlib import Path

from . import bg, mapping, pool, solver
from .spec import RESULT_DIR

#: 目录 schema：**加字段就 +1**（消费者据此判断能否直接吃）。
SCHEMA = 1

#: 产物名（`results/` 下）。
NAME = "missile_catalog.json"

#: 参考工况字段的顺序（与 `bg.reference()` 一一对应）。
_REF_FIELDS = ("alt_m", "speed_ms", "mach", "q_pa", "f_mach", "cap_f")

#: `bg.point_of()` 只要求对象有 `.key` / `.native`（见其 docstring）——
#: 全表枚举时不值得为每个预设构造 `pool.PoolEntry`，用一个轻量替身即可。
_Entry = namedtuple("_Entry", "key native")


class CatalogError(RuntimeError):
    """目录自校验失败（数值对不上、条目缺失、折叠规则坏了）。"""


def default_path() -> Path:
    return Path(RESULT_DIR) / NAME


def _pool_by_native() -> dict:
    return {e.native: e for e in pool.POOL}


def base_of(native: str) -> str:
    """`x_default` → `x`；其余原样。"""
    s = str(native)
    return s[: -len("_default")] if s.endswith("_default") else s


def key_of(native: str) -> str:
    """短名：11 弹池用池里的短名，其余用预设名本身。"""
    e = _pool_by_native().get(str(native))
    return e.key if e else str(native)


def build(version: str | None = None) -> dict:
    """算全表并返回目录记录（不落盘）。实测 2.59.0.28：168 条里 159 可算，约 2~8 s。"""
    names = list(solver.preset_names())
    if not names:
        raise CatalogError("内核没给出预设名单（solver.preset_names() 为空）—— 版本变了？")
    known = set(names)
    by_native = _pool_by_native()
    missiles, unsupported = [], []
    for native in names:
        info = solver.preset_info(native)
        key = key_of(native)
        entry = by_native.get(native)
        row = {
            "native": native,
            "key": key,
            "label": key,
            "full_name": (entry.full_name if entry else
                          (str(info.get("bullet_name") or "") or native)),
            "seeker": info.get("seeker"),
            "resource": str(info.get("resource") or ""),
            "resource_sha256": info.get("resource_sha256"),
            "in_pool11": bool(entry),
            "duplicate_of": (base_of(native)
                             if base_of(native) != native and base_of(native) in known else None),
        }
        row["default_hidden"] = row["duplicate_of"] is not None
        try:
            metrics = mapping.blk_metrics(solver.profile(native, version))
            point = bg.point_of(_Entry(key=key, native=native), version)
        except Exception as exc:                                # noqa: BLE001
            # 用户 2026-10-04 口径：**置灰原因就写「需要动态解算」**（面用户的话），
            # 内核/公式给的原话另存 `kernel_reason`（诊断与判据要用，别丢出处）。
            unsupported.append({**row, "reason": "需要动态解算",
                                "kernel_reason": f"{type(exc).__name__}: {exc}"})
            continue
        missiles.append({**row, "dv": metrics.dv, "bc": metrics.bc,
                         "gamma": point.gamma, "ginv": bg.ginv_of(point.gamma)})
    ident = solver.identity()
    return {
        "schema": SCHEMA,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "solver": {k: ident.get(k) for k in
                   ("solver", "model", "version", "sha256", "archive_sha256", "n_profiles")},
        "archive_sha256": ident.get("archive_sha256"),
        "reference": dict(zip(_REF_FIELDS, (float(x) for x in bg.reference()))),
        "count": len(names),
        "computable": len(missiles),
        "unsupported": unsupported,
        "missiles": missiles,
    }


def write_json(path=None, version: str | None = None) -> Path:
    """算全表并落盘；返回写出的路径。行尾与其它 `results/*.json` 一致（Windows 上 CRLF）。"""
    rec = build(version)
    path = Path(path) if path is not None else default_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return path


def load(path=None) -> dict:
    path = Path(path) if path is not None else default_path()
    return json.loads(path.read_text(encoding="utf-8"))


def verify(rec: dict, *, version: str | None = None, full: bool = True) -> None:
    """自校验：形状 + **逐值重算**（默认全表；`full=False` 只核 11 弹池，快）。

    有齿的地方：**改了记录里的任何一个坐标值都会在这里炸**（反证用例就是靠这个）。
    没装游戏数据/内核不可用的机器上，本函数会抛出 `SolverUnavailable`（上层按需跳过）。
    """
    if int(rec.get("schema") or 0) != SCHEMA:
        raise CatalogError(f"schema 不是 {SCHEMA}：{rec.get('schema')!r}")
    names = list(solver.preset_names())
    if int(rec.get("count") or 0) != len(names):
        raise CatalogError(f"count={rec.get('count')} 与内核预设数 {len(names)} 不一致")
    rows = {str(m.get("native")): m for m in (rec.get("missiles") or [])}
    bad = {str(u.get("native")) for u in (rec.get("unsupported") or [])}
    if set(rows) | bad != set(names):
        miss = sorted(set(names) - set(rows) - bad)[:5]
        raise CatalogError(f"目录没有覆盖内核预设表；缺 {miss} 等")
    if sum(1 for m in rows.values() if m.get("in_pool11")) != len(pool.POOL):
        raise CatalogError("`in_pool11` 的条目数与我们 11 弹池不一致")
    for native in (list(rows) if full else [e.native for e in pool.POOL]):
        metrics = mapping.blk_metrics(solver.profile(native, version))
        want = bg.point_of(_Entry(key=key_of(native), native=native), version)
        got = rows[native]
        for field, expect in (("dv", metrics.dv), ("bc", metrics.bc),
                              ("gamma", want.gamma), ("ginv", bg.ginv_of(want.gamma))):
            if float(got.get(field, float("nan"))) != float(expect):
                raise CatalogError(
                    f"{native} 的 {field} 对不上：目录 {got.get(field)!r} vs 现算 {expect!r}")


def describe(rec: dict) -> str:
    """一行摘要（CLI 打印用）。"""
    return (f"全弹池目录：{rec.get('computable')}/{rec.get('count')} 可算，"
            f"不可算 {len(rec.get('unsupported') or [])} 条"
            f"（{', '.join(sorted({str(u.get('key')) for u in (rec.get('unsupported') or [])}))}）")
