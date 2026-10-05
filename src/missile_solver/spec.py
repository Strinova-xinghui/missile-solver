# -*- coding: utf-8 -*-
"""外壳契约：**唯一的跨模块接口面**。

三层分工（换任何一层都不动另外两层）：

    spec.py     本文件 —— 只描述"求解器要什么 / 吐什么"和"图上要什么"，纯数据结构
    solver.py   与 `vendor/wt-missile/` 的唯一接触面（换求解器只动它）
    figures.py  唯一出图处（换图只动它）

本文件不 import numpy / matplotlib / vendor，也不读任何文件。
"""

from __future__ import annotations

import math
import os

from . import _data
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------- 常量与路径

#: `src/`（本包根的上一层）与包目录本身。
ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = Path(__file__).resolve().parent

#: 内核随包发布（`kernel/wt_missile.py`，**逐字节**与上游一致 ⇒ 身份哈希不变）。
KERNEL_DIR = PACKAGE_DIR / "kernel"

#: ⚠ **内核的运行时目录**：内核自己写死 `ROOT = Path(wt_missile.__file__).parent`，并在 import 时
#: 读 `<ROOT>/inputs/resources/<VERSION>/presets.json` + `manifest.json`；`load_profile()` 同样从
#: `<ROOT>/inputs/resources/<VERSION>/` 读 blk。所以"数据放哪"没法用参数传给它 ——
#: 本包在**第一次真正要用内核时**由 `_data.ensure_kernel_home()` 物化出这个目录：
#: 复制一份（逐字节相同的）`wt_missile.py`，再把 `inputs/resources/<VERSION>` **联接**到 `DATA_DIR`
#: （联接不可用时退回复制）。游戏数据不在本仓、也不随包，见 `data/README.md` 与 `_data.py`。
#: ⚠ `import missile_solver` **不需要**数据；只有真跑求解器/读 blk 时才要求 `DATA_DIR`。
VENDOR_DIR = _data.KERNEL_HOME

#: 目录类产物的落点。本包**不出图、不出产物**；只有 `catalog.write_json()` 会写这个目录
#: （默认 `<cwd>/results`，可用 `MISSILE_SOLVER_RESULT_DIR` 覆盖）。
RESULT_DIR = Path(os.environ.get("MISSILE_SOLVER_RESULT_DIR") or (Path.cwd() / "results"))

VERSION_28 = "2.59.0.28"
SOLVER_DT = 0.02083333395421505                     # 客户端对象时钟 1/48 s
DT_MIN = 0.00025

TIERS = ("standard", "fast")
OBSERVATIONS = ("ideal", "locked")
HIT_EVENTS = ("contact", "proximity_fuse")
TERMINAL_EVENTS = ("time_limit", "lifetime", "ground", "contact", "proximity_fuse")

DEFAULT_MISSILE = "PL-12"                           # 基准弹（池内短名）

# --------------------------------------------------------------------- 输入：环境


@dataclass(frozen=True)
class Scene:
    """一次求解的**环境输入**。

    这一层是**用户视角**的几何（水平距离 + 角度），不是求解器的 3D 向量 ——
    折成 `position_m` / `velocity_m_s` 的唯一落点是 `solver.scenario_dict()`。

    坐标约定（与求解器一致）：**x/z 水平、y 向上**；零航向沿 **+x**，正航向转向 **+z**；
    俯仰以抬头为正。求解器实测支持完整 3D（位置与速度都是 3 分量），所以载机与敌机
    都能给航向与俯仰。

    ⚠ `range_m` 是**水平距离**，不是斜距。斜距由 `range_slant_m` 现算（含高度差）。
    同高交战时两者相等，所以历史同高工况不受语义变化影响。
    """

    # —— 几何 ——
    range_m: float = 50_000.0             # 双方**水平**距离
    alt_m: float = 10_000.0               # 发射高度
    target_alt_m: float | None = None     # 目标高度（None ⇒ 与发射同高）
    target_bearing_deg: float = 0.0       # 目标方位角：0 = +x，正 → +z

    # —— 载机速度 ——
    vm0: float = 300.0                    # 速度标量（正）
    launch_heading_deg: float = 0.0       # 航向
    launch_pitch_deg: float = 0.0         # 速度俯仰（正 = 爬升）

    # —— 敌机状态（匀速直飞）——
    target_speed_ms: float = 300.0        # 速度标量（正）
    target_heading_deg: float = 180.0     # 航向（180° = 迎面）
    target_pitch_deg: float = 0.0         # 速度俯仰（正 = 爬升）

    # —— 技术参数 ——
    duration_s: float = 150.0             # 请求时域上限；实际可能提前终止
    dt_s: float = SOLVER_DT               # 物理步长；固定步长是 C 快路径的条件
    observation: str = "ideal"            # ideal | locked
    wind_m_s: tuple = (0.0, 0.0, 0.0)
    ground_height_m: float = 0.0
    proximity_fuse: bool = True
    hit_radius_m: float = 0.0             # 0 ⇒ 只用求解器自己的近炸判定
    maneuvers: tuple = ()                 # 内核原生能力；模型层见 flightpath.py（航向指令 → 这里），
                                          # UI / 表单仍未暴露（docs/MANEUVER-TARGETS.md）

    # —— 派生几何 ——

    @property
    def target_alt(self) -> float:
        return float(self.alt_m if self.target_alt_m is None else self.target_alt_m)

    @property
    def range_slant_m(self) -> float:
        """初始三维斜距 = √(水平² + 高度差²)。求解器真正吃的是这个。"""
        return math.hypot(float(self.range_m), self.target_alt - self.alt_m)

    @property
    def launch_position_m(self) -> tuple:
        return (0.0, float(self.alt_m), 0.0)

    @property
    def target_position_m(self) -> tuple:
        b = math.radians(float(self.target_bearing_deg))
        return (float(self.range_m) * math.cos(b), self.target_alt,
                float(self.range_m) * math.sin(b))

    @property
    def launch_velocity_m_s(self) -> tuple:
        return velocity_vector(self.vm0, self.launch_heading_deg, self.launch_pitch_deg)

    @property
    def target_velocity_m_s(self) -> tuple:
        return velocity_vector(self.target_speed_ms, self.target_heading_deg,
                               self.target_pitch_deg)

    @property
    def head_on(self) -> bool:
        """迎头与否（|航向差| > 90°）。只用于文案，不参与物理。"""
        d = abs(((float(self.target_heading_deg) - float(self.launch_heading_deg) + 180.0)
                 % 360.0) - 180.0)
        return d > 90.0

    def check(self) -> list:
        """返回不自洽项（空 = 通过）。**不抛异常**，由调用方决定怎么报。"""
        bad = []
        if self.range_m < 0:
            bad.append("range_m（水平距离）不能为负")
        if self.duration_s <= 0:
            bad.append("duration_s 必须为正")
        if not (DT_MIN - 1e-12 <= self.dt_s <= SOLVER_DT + 1e-12):
            bad.append(f"dt_s 必须落在 [{DT_MIN}, {SOLVER_DT}] 内，收到 {self.dt_s!r}")
        if self.observation not in OBSERVATIONS:
            bad.append(f"observation 只能是 {' / '.join(OBSERVATIONS)}，收到 {self.observation!r}")
        if self.hit_radius_m < 0:
            bad.append("hit_radius_m 不能为负")
        if self.vm0 < 0 or self.target_speed_ms < 0:
            bad.append("速度标量必须非负（方向交给航向/俯仰角）")
        return bad

    def as_dict(self) -> dict:
        return {
            "range_m": self.range_m, "alt_m": self.alt_m,
            "target_alt_m": self.target_alt_m, "target_alt": self.target_alt,
            "target_bearing_deg": self.target_bearing_deg,
            "range_slant_m": self.range_slant_m,
            "vm0": self.vm0, "launch_heading_deg": self.launch_heading_deg,
            "launch_pitch_deg": self.launch_pitch_deg,
            "target_speed_ms": self.target_speed_ms,
            "target_heading_deg": self.target_heading_deg,
            "target_pitch_deg": self.target_pitch_deg,
            "launch_position_m": list(self.launch_position_m),
            "launch_velocity_m_s": list(self.launch_velocity_m_s),
            "target_position_m": list(self.target_position_m),
            "target_velocity_m_s": list(self.target_velocity_m_s),
            "duration_s": self.duration_s, "dt_s": self.dt_s,
            "observation": self.observation, "wind_m_s": list(self.wind_m_s),
            "ground_height_m": self.ground_height_m,
            "proximity_fuse": self.proximity_fuse, "hit_radius_m": self.hit_radius_m,
            "maneuvers": [list(m) for m in (self.maneuvers or ())],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Scene":
        """只吃自己 `as_dict()` 认得的字段，未知键交给调用方报错（不静默吞）。

        `as_dict()` 里那些派生量（`range_slant_m` / `*_position_m` / `*_velocity_m_s`）
        是只读的，回灌时跳过。
        """
        known = set(cls.__dataclass_fields__) - {"maneuvers"}
        kw = {k: v for k, v in (d or {}).items() if k in known}
        if "wind_m_s" in kw and kw["wind_m_s"] is not None:
            kw["wind_m_s"] = tuple(float(x) for x in kw["wind_m_s"])
        if (d or {}).get("maneuvers"):
            kw["maneuvers"] = tuple((float(t), tuple(float(x) for x in a))
                                    for t, a in d["maneuvers"])
        return cls(**kw)

    def describe(self) -> str:
        """一行中文摘要（进图副标题、控制台与 manifest）。

        **非默认的量才写出来**：默认工况是"50 km 同高迎头、双方 300 m/s、150 s 理想观测"，
        把那些 0°／ideal 一律铺开会淹没真正被改过的那几项。3D 相关的量 —— 目标方位、
        载机俯仰、时长、风、地面高、命中半径、近炸开关 —— 都按这条规则露面：
        **工况改了，图上必须看得见**（曾经方位改了整张图毫无反应）。
        """
        alt = f"{self.alt_m:.0f}"
        if abs(self.target_alt - self.alt_m) > 1e-9:
            alt += f"→{self.target_alt:.0f}"
        kind = "迎头" if self.head_on else "尾追/侧向"
        if abs(self.target_bearing_deg) > 1e-9:
            kind = f"方位 {self.target_bearing_deg:g}° {kind}"
        launch = f"载机 {self.vm0:.0f} m/s 航向 {self.launch_heading_deg:g}°"
        if abs(self.launch_pitch_deg) > 1e-9:
            launch += f" 俯仰 {self.launch_pitch_deg:g}°"
        extras = []
        if abs(float(self.duration_s) - 150.0) > 1e-9:
            extras.append(f"时长 {self.duration_s:g} s")
        if not self.proximity_fuse:
            extras.append("近炸 关")
        if abs(float(self.hit_radius_m)) > 1e-9:
            extras.append(f"命中半径 {self.hit_radius_m:g} m")
        if abs(float(self.ground_height_m)) > 1e-9:
            extras.append(f"地面高 {self.ground_height_m:g} m")
        if any(abs(float(w)) > 1e-9 for w in (self.wind_m_s or ())):
            extras.append("风 " + "/".join(f"{float(w):g}" for w in self.wind_m_s) + " m/s")
        text = (f"{self.range_m / 1000:.0f} km（{self.range_slant_m / 1000:.1f} km 斜距）"
                f"{kind} · 高度 {alt} m · {launch} · "
                f"敌机 {self.target_speed_ms:.0f} m/s 航向 "
                f"{self.target_heading_deg:g}° 俯仰 {self.target_pitch_deg:g}° · "
                f"{self.observation}")
        return text + (" · " + " · ".join(extras) if extras else "")


def velocity_vector(speed: float, heading_deg: float, pitch_deg: float) -> tuple:
    """速度标量 + 航向 + 俯仰 → 世界坐标速度向量（x/z 水平、y 向上）。

    零航向沿 +x，正航向转向 +z；俯仰抬头为正。水平速度被 `cos(pitch)` 缩放，
    所以 `|v|` 恒等于 `speed`。
    """
    h = math.radians(float(heading_deg))
    p = math.radians(float(pitch_deg))
    s = float(speed)
    return (s * math.cos(p) * math.cos(h), s * math.sin(p), s * math.cos(p) * math.sin(h))


# --------------------------------------------------------------------- 输入：缩放


@dataclass(frozen=True)
class Scaling:
    """落在一个格点上的求解器乘数。

    **只改推力与阻力系数，基准弹的其余量全部不动**：

        thrust_scale = ΔV_target / ΔV_blk          （乘到 force / force1）
        cx_scale     = m_dry / (BC_target · SD · CxK_blk)   （乘到 CxK）
        cxaoa_scale  = CxAoA_target / CxAoA_blk    （乘到 CxAoA；诱导阻力项）

    `cxaoa_scale` 只有 β–γ 平面（第二张图）会用到：那里固定 ΔV、扫 (β, γ)，
    γ 的反解落在 `CxAoA` 上。**β 就是 `bc_target`** —— β 与 BC 恒等（见 docs/BETA-GAMMA.md §2.1），
    所以第一张图的纵轴乘数原封不动就是第二张图的横轴乘数。

    权威算法只在 `mapping.py`（ΔV/BC）与 `bg.py`（β/γ）；本结构只承载结果，
    供跨进程传递与**缓存键**使用（见 `engine.cache_key`）。
    """

    dv_target: float
    bc_target: float
    dv_blk: float
    bc_blk: float
    thrust_scale: float
    cx_scale: float
    cxaoa_scale: float = 1.0

    def as_dict(self) -> dict:
        return {"dv_target": self.dv_target, "bc_target": self.bc_target,
                "dv_blk": self.dv_blk, "bc_blk": self.bc_blk,
                "thrust_scale": self.thrust_scale, "cx_scale": self.cx_scale,
                "cxaoa_scale": self.cxaoa_scale}

    @classmethod
    def from_dict(cls, d: dict) -> "Scaling":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in (d or {}).items() if k in known})


# --------------------------------------------------------------------- 输出


@dataclass
class Shot:
    """一次求解的结构化结果。**字段语义即 API**，换求解器必须守住它们。

    `t_hit` 是 `nan` 表示未命中 —— 不要用 0 或 -1 代替，读图的人会把它当命中时间。
    `backend` / `fallback_reason` 只能来自**真跑过的** summary，不许按档位名猜。

    **三个"距离"的语义（A5 缺陷的修复 —— 不要再混着用）**：

    * `min_separation_m` —— 内核 `minimum_separation_until_event_m` 原值：**终止事件之前**
      的最小弹目距离。⚠ **命中时它等于引信半径**（默认 10 m）—— 引信一进半径就触发，
      后面的弹道根本不走。所以它**不是"脱靶量"**，把它当脱靶量报出去就是 A5。
    * `cpa_m` —— **真最近接近距离**（几何量，与引信何时触发无关）。命中工况靠
      **关掉引信复跑一次**量出来（`solver.run(want_cpa=True)`，多一次解算）；
      **未命中时恒等于 `min_separation_m`**（那次飞行本来就飞完了，零额外成本）；
      引信本来就关着时也等于 `min_separation_m`（没有提前触发这回事）。
      没量过（`want_cpa=False`）⇒ `nan`，那是"不知道"，**不是 0、也不是 10**。
    * `fuse_radius_m` —— 内核自报的引信半径 [m]；0 = 该弹没有近炸引信或本次关了引信，
      `nan` = 求解器没报这个键（**别把 nan 当 0**）。

    判"打得多准"看 `cpa_m`；判"引信怎么设的"看 `fuse_radius_m`；判"事件前最近到过多少"
    才看 `min_separation_m`。旧字段 `miss_m` 已删除：它的名字就是 A5 的病根。
    """

    ok: bool = False
    event: str = ""
    hit: bool = False
    t_end: float = math.nan
    t_hit: float = math.nan
    min_separation_m: float = math.nan
    cpa_m: float = math.nan
    fuse_radius_m: float = math.nan
    max_alt_m: float = math.nan
    v_impact: float = math.nan
    dv_measured: float = math.nan
    t_burnout: float = math.nan
    m_launch: float = math.nan
    m_dry: float = math.nan
    backend: str = ""
    fallback_reason: str | None = None
    resource: str = ""
    error: str = ""
    rows: tuple = ()
    cache_hit: bool = False

    def as_dict(self) -> dict:
        return {
            "ok": self.ok, "event": self.event, "hit": self.hit,
            "t_end": self.t_end, "t_hit": self.t_hit,
            "min_separation_m": self.min_separation_m, "cpa_m": self.cpa_m,
            "fuse_radius_m": self.fuse_radius_m,
            "max_alt_m": self.max_alt_m, "v_impact": self.v_impact,
            "dv_measured": self.dv_measured, "t_burnout": self.t_burnout,
            "m_launch": self.m_launch, "m_dry": self.m_dry,
            "backend": self.backend, "fallback_reason": self.fallback_reason,
            "resource": self.resource, "error": self.error,
            "rows": [dict(r) for r in self.rows], "cache_hit": self.cache_hit,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Shot":
        known = {f for f in cls.__dataclass_fields__}
        kw = {k: v for k, v in (d or {}).items() if k in known}
        if "rows" in kw:
            kw["rows"] = tuple(dict(r) for r in (kw["rows"] or ()))
        return cls(**kw)


# --------------------------------------------------------------------- 图


@dataclass(frozen=True)
class MissilePoint:
    """散点上的一个弹：**权威公式现算**的 (ΔV, BC) + 它的锚量。"""

    key: str            # 池内短名，如 "PL-12"
    label: str          # 图上标签，如 "PL-12"、"R-77-1"
    dv: float           # 真空等效总增速 [m/s]
    bc: float           # 简化弹道系数
    dry_kg: float
    cxk: float
    caliber_m: float
    two_stage: bool = False
    native: str = ""    # vendor 预设名，如 "cn_pl12"

    def as_dict(self) -> dict:
        return {"key": self.key, "label": self.label, "dv": self.dv, "bc": self.bc,
                "dry_kg": self.dry_kg, "cxk": self.cxk, "caliber_m": self.caliber_m,
                "two_stage": self.two_stage, "native": self.native}


@dataclass(frozen=True)
class AxisSpec:
    """坐标轴规格。范围由散点自适应，刻度在必要时按 nice 序列下调。"""

    xlim: tuple
    ylim: tuple
    x_major: float
    x_minor: float
    y_major: float
    y_minor: float

    def as_dict(self) -> dict:
        return {"xlim": list(self.xlim), "ylim": list(self.ylim),
                "x_major": self.x_major, "x_minor": self.x_minor,
                "y_major": self.y_major, "y_minor": self.y_minor}


@dataclass
class PlaneGrid:
    """(ΔV, BC) 网格。`triple` 语义 = (min, max, step)，范围与轴对齐。

    ⚠⚠ **字段名是图1 的老名字，装的东西随图变 —— 这是全仓最容易把两轴读反的地方**：

    | 字段 | 图1（ΔV–BC 平面） | 图2（β–ginv 能力平面，2026-10-04 起） |
    | --- | --- | --- |
    | `dv` | ΔV [m/s]（横轴） | **β [kg/m²]（横轴）** |
    | `bc` | BC [kg/m²]（纵轴） | **ginv = 1/γ [m²/kg]（纵轴）** |

    也就是说 `grid.bc` 里装的是 `ginv`、`grid.dv` 里装的是 `β`；步长字段同理
    （`make_grid(xlim, ylim, dv_step=req.beta_step, bc_step=req.ginv_step)`）。
    改名会牵动图1/网格/缓存全链，所以**保留字段名 + 就地写明语义**，另给只读别名
    `beta` / `ginv`（新代码请用别名，读起来就不用猜了）。
    """

    dv: tuple
    bc: tuple
    dv_step: float
    bc_step: float

    @property
    def beta(self) -> tuple:
        """只读别名：**图2 的横轴**（= `dv`，见类 docstring 的对照表）。"""
        return self.dv

    @property
    def ginv(self) -> tuple:
        """只读别名：**图2 的纵轴**（= `bc`，装的是 `ginv = 1/γ`，不是 γ）。"""
        return self.bc

    @property
    def shape(self) -> tuple:
        return (len(self.dv), len(self.bc))

    @property
    def n_cases(self) -> int:
        return len(self.dv) * len(self.bc)

    @property
    def cells(self):
        """按**行优先**（ΔV 主序）产出 (dv, bc)，与 `t_hit` 矩阵的 reshape 一致。"""
        for dv in self.dv:
            for bc in self.bc:
                yield float(dv), float(bc)

    def as_dict(self) -> dict:
        return {"n_dv": len(self.dv), "n_bc": len(self.bc), "n_cases": self.n_cases,
                "dv_min": self.dv[0], "dv_max": self.dv[-1], "dv_step": self.dv_step,
                "bc_min": self.bc[0], "bc_max": self.bc[-1], "bc_step": self.bc_step}


@dataclass(frozen=True)
class IsoGrid:
    """等时线输入：网格坐标 + t_hit 矩阵（形状 `(n_dv, n_bc)`，未命中处为 nan）。"""

    dv: tuple
    bc: tuple
    t_grid: object                       # numpy.ndarray

    @property
    def shape(self) -> tuple:
        return tuple(self.t_grid.shape)


@dataclass(frozen=True)
class Provenance:
    """产物自描述：**谁算的 / 用什么算的**。进图脚注与 manifest。"""

    tier: str
    backend: str
    solver: dict = field(default_factory=dict)
    standard: str = ""
    scene: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"tier": self.tier, "backend": self.backend, "solver": dict(self.solver),
                "standard": self.standard, "scene": dict(self.scene)}
