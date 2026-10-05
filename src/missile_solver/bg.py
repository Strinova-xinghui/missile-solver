# -*- coding: utf-8 -*-
"""β–γ 能力平面：两种存能能力，以及由它们派生的机动指标。

**两个坐标轴**（都是弹的固有属性）：

    β    = m燃尽 / (SD · CxK)                          直飞保能，[kg/m²]，**越大越好**
    ginv = 1 / γ                                       转向能力，[m²/kg]，**越大越好**
    γ    = m燃尽 · SD · CxK · CxAoA / (SL² · CyK²)      转向损失，[kg/m²]，越小越好

    SD = π·d²/4                      = 内核 drag_area（wt_missile.py:758）
    SL = 0.3·d·L·wingAreaMult        = 内核 lift_area （wt_missile.py:757）

⚠ **`ginv` 只是 γ 的另一种写法，不是另一个物理量**（用户 2026-10-04 口径：「将 γ 和 β 一样
取倒数（β 物理意义上是零升阻力减速度的倒数），以此使二者单调性一致」）。γ 的公式**一个字不改**，
内核那一头照样吃 γ 去算 `CxAoA`；`ginv` 只出现在**轴 / 派生指标 / 产物 / 文案**这一层。

**为什么取倒数**：β 与 γ 的物理方向相反（β 大 = 阻力小 = 好；γ 大 = 转向损失大 = 差），
摆在同一张图上"右上角"就同时意味着最好与最差。取倒数后两根轴**同为"向上＝更好"**，
"双优区"是干净的右上角。

⚠ **换算只在本模块**：`ginv_of()` / `gamma_of()` 是**唯一**的一对互换函数，
别处一律不写 `1/gamma`。调用方（图2 / 图3 / WebUI / 产物）一律说 `ginv`，
只有真正要注入内核时（`cxaoa_scale_for()`）才经过 `gamma_of()` 落回物理 γ。

**为什么是两个独立维度**：β 的分母只有口径与 `CxK`；γ 的分母还含升力面积平方与 `CyK²`，
数学上互不包含（β 甚至不含 `wingAreaMult`）。内核把两者放在同一个阻力式里
（`wt_missile.py:810`）：

    D = q·SD·CxK·[ f(M) + CxAoA·F(M)·sin²α ]

把 `sinα` 换成过载（`L = q·SL·CyK·sinα`，`n ≡ L/(m·g0)`）再除质量：

    a_loss = q·f(M)/β  +  γ·F(M)·(n·g0)²/q  =  q·f(M)/β  +  F(M)·(n·g0)²/(q·ginv)
             └基础项┘     └── 诱导阻力项 ──┘

两项对动压的依赖**方向相反**（∝q 与 ∝1/q）—— 这就是"高空高速看 β、低空缠斗看 ginv"的根源。

**派生指标**（含 `q`，所以要给定参考工况）：

    Q     = √(β·γ·F(M)/f(M))      机动敏感度 [kg/m²]。`F/f` 在 Ma 上只波动 ±6%，
                                  所以 Q 近似是弹的固有量，最适合当坐标轴。
                                  ⚠ 它**必须**用物理 γ：`Q = √(β·F/(f·ginv))`
    n*    = q/(Q·g0)              临界过载 [G]：机动损失恰好等于基础损失。越小越"贵"
    R     = (n/n*)²               机动惩罚比；R=1 时两项等量
    β_eff = β/(1+R)               等效弹道系数（把机动折回成 BC 的口径）

⚠ **口径**：β、γ 都用**标称**值（不含马赫修正 `f`、`F`）。含进去它们就随马赫变 2 倍，
不再是定值、没法当坐标轴。这与第一张图把 `BC` 定义成不含 `f` 的做法一致。

⚠ **适用边界**：仅升力线性段（`CyK·sinα ≤ CyMaxAoA`）、准平衡无角速度、`n` 是实际气动
过载。详见 `docs/BETA-GAMMA.md`。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from . import mapping, solver
from .spec import Scaling

#: 轴量（`ginv`）的单位。γ 是 kg/m²，取倒数就是 m²/kg。
GINV_UNIT = "m²/kg"

#: β–γ 的默认参考工况。与 `_recon/make_fig2_turn_energy.py` 一致（10 km / 800 m/s），
#: 这样新图与既有参考图的等 `n*` 线可以直接对照。
REF_ALT_M = 10_000.0
REF_SPEED_MS = 800.0

#: **参考图（2.57 基线）自己的层**，不是现行制品层 —— 现行层见 `figures.BG_NSTAR_LEVELS`
#: （2026-10-05 起为 5~50 G 共 10 条）。用户给的那张参考图上确实只有 14~20 共 7 条虚线，
#: 这份快照就记这个来历（配合 `results/bg_ref_curves.json`）；本模块**没有任何地方**用它画图，
#: 解析式 `nstar_slope()` / `nstar_curve()` 都是把 n 当参数收的。
#: ⚠ 旧名是 `REF_NSTAR_LEVELS`（2026-10-05 改名）：旧名容易被当成"现行层"，改名只为消歧义。
REF_FIGURE_NSTAR_LEVELS = (14.0, 15.0, 16.0, 17.0, 18.0, 19.0, 20.0)

G0 = 9.81


# --------------------------------------------------------------------- 唯一的换算点


def ginv_of(gamma: float) -> float:
    """物理 γ [kg/m²] → 轴量 `ginv = 1/γ` [m²/kg]。**全仓唯一的正向换算。**"""
    g = float(gamma)
    if not (g > 0.0) or not math.isfinite(g):
        raise ValueError(f"γ 必须是有穷正数，收到 {gamma!r}")
    return 1.0 / g


def gamma_of(ginv: float) -> float:
    """轴量 `ginv` [m²/kg] → 物理 γ [kg/m²]。**全仓唯一的反向换算**（注入内核前用）。"""
    v = float(ginv)
    if not (v > 0.0) or not math.isfinite(v):
        raise ValueError(f"ginv 必须是有穷正数，收到 {ginv!r}")
    return 1.0 / v



@dataclass(frozen=True)
class BgPoint:
    """一型弹在 β–γ 平面上的固有位置（不含参考工况）。

    ⚠ 存的是**物理 γ**（内核对 `CxAoA` 要它）；轴量 `ginv` 由 `ginv` 属性现算 ——
    派生量不存第二份，才不会出现"改了 γ 忘了改 ginv"。
    """

    key: str
    beta: float          # kg/m²  直飞保能
    gamma: float         # kg/m²  转向损失（物理量）
    dry_kg: float        # 燃尽质量（β/γ 的分子）
    sd_m2: float         # 迎风面积 πd²/4
    sl_m2: float         # 升力面积 0.3·d·L·w
    cxk: float
    cx_aoa: float        # 内核实际值（blk 缺失时为内核兜底 9.0，含 w² 修正）
    cyk: float           # 同上（兜底 2.2）
    caliber_m: float
    length_m: float
    wing_area_mult: float

    @property
    def ginv(self) -> float:
        """轴量（纵轴）：`1/γ` [m²/kg]，越大越好。"""
        return ginv_of(self.gamma)

    def as_dict(self) -> dict:
        return {"key": self.key, "beta": self.beta, "gamma": self.gamma,
                "ginv": self.ginv,
                "dry_kg": self.dry_kg, "sd_m2": self.sd_m2, "sl_m2": self.sl_m2,
                "cxk": self.cxk, "cx_aoa": self.cx_aoa, "cyk": self.cyk,
                "caliber_m": self.caliber_m, "length_m": self.length_m,
                "wing_area_mult": self.wing_area_mult}


@dataclass(frozen=True)
class BgReference:
    """给定参考工况后，某一点/某一弹的机动指标。

    ⚠ `gamma` 是**物理量**，`q_sens` / `n_star` / `ratio` / `beta_eff` 全部只用它算
    （取倒数不改变任何一个数，见 `tests/test_bg_ginv.py` 的参照实现对比）。
    `ginv` 是它的另一种写法，**不参与**任何派生量。
    """

    alt_m: float
    speed_ms: float
    mach: float
    q_pa: float          # 动压
    f_mach: float        # 内核的 f(M)：零升阻力马赫修正
    cap_f: float         # 内核的 F(M) = 3.247·[0.308+0.75(f-0.308)]
    beta: float
    gamma: float
    q_sens: float        # Q  = √(β·γ·F/f)
    n_star: float        # 临界过载 [G]
    n: float             # 本次评估用的过载
    ratio: float         # R  = (n/n*)²
    beta_eff: float      # β/(1+R)

    @property
    def ginv(self) -> float:
        """轴量（纵轴）：`1/γ` [m²/kg]。"""
        return ginv_of(self.gamma)

    def as_dict(self) -> dict:
        return {"alt_m": self.alt_m, "speed_ms": self.speed_ms, "mach": self.mach,
                "q_pa": self.q_pa, "f_mach": self.f_mach, "cap_f": self.cap_f,
                "beta": self.beta, "gamma": self.gamma, "ginv": self.ginv,
                "q_sens": self.q_sens,
                "n_star": self.n_star, "n": self.n, "ratio": self.ratio,
                "beta_eff": self.beta_eff}


# --------------------------------------------------------------------- 固有量


def point_of(entry, version: str | None = None) -> BgPoint:
    """池项 → β–γ 坐标。`entry` 是 `pool.PoolEntry`（或任何有 `.key`/`.native` 的对象）。"""
    params = solver.profile(entry.native, version)
    cx_aoa, cyk = solver.body_aero(entry.native, version)
    m_dry = mapping.blk_metrics(params).dry_kg
    return _point(entry.key, params, m_dry, cx_aoa, cyk)


def _point(key, params, m_dry, cx_aoa, cyk) -> BgPoint:
    d = float(params["caliber"])
    length = float(params["length"])
    w = float(params.get("wingAreaMult", 1.0))
    cxk = float(params["CxK"])
    sd = math.pi * d * d / 4.0
    sl = 0.3 * d * length * w
    if m_dry <= 0 or sd <= 0 or sl <= 0 or cxk <= 0 or cx_aoa <= 0 or cyk <= 0:
        raise ValueError(f"{key}: β/γ 的分母出现非正量（m={m_dry} SD={sd} SL={sl} "
                         f"CxK={cxk} CxAoA={cx_aoa} CyK={cyk}）")
    return BgPoint(key=str(key), beta=m_dry / (sd * cxk),
                   gamma=m_dry * sd * cxk * cx_aoa / (sl * sl * cyk * cyk),
                   dry_kg=m_dry, sd_m2=sd, sl_m2=sl, cxk=cxk, cx_aoa=cx_aoa,
                   cyk=cyk, caliber_m=d, length_m=length, wing_area_mult=w)


def points(keys=None, *, version: str | None = None) -> list:
    """散点数据集。`keys=None` ⇒ 全池；顺序照 `pool`。"""
    from . import pool                                   # 循环导入：只在调用时取
    return [point_of(e, version) for e in pool.resolve_many(keys)]


# --------------------------------------------------------------------- 参考工况


def reference(alt_m: float | None = None, speed_ms: float | None = None) -> tuple:
    """参考工况 → `(alt_m, speed_ms, mach, q_pa, f_mach, cap_f)`。

    `q = ½ρv²`、`M = v/a`、`f = 内核 _mach_function(M)`、
    `F = 3.247·[0.308 + 0.75·(f − 0.308)]`（内核 `induced/cx_aoa` 的那个因子）。
    """
    alt = float(REF_ALT_M if alt_m is None else alt_m)
    speed = float(REF_SPEED_MS if speed_ms is None else speed_ms)
    if speed <= 0:
        raise ValueError("参考速度必须为正")
    rho, sound = solver.atmosphere(alt)
    if sound <= 0:
        raise ValueError("该高度的声速非正，参考工况无效")
    mach = speed / sound
    f_mach = solver.mach_function(mach)
    cap_f = 3.247 * (0.308 + 0.75 * (f_mach - 0.308))
    return alt, speed, mach, 0.5 * rho * speed * speed, f_mach, cap_f


def q_sens(beta: float, gamma: float, cap_f: float, f_mach: float) -> float:
    """`Q = √(β·γ·F/f)` —— 机动敏感度 [kg/m²]。"""
    if beta <= 0 or gamma <= 0:
        raise ValueError("β、γ 必须为正")
    return math.sqrt(beta * gamma * cap_f / f_mach)


def n_star_of(q_pa: float, q_value: float) -> float:
    """临界过载 `n* = q/(Q·g0)` [G]：机动损失恰好等于基础损失的那个过载。"""
    if q_value <= 0:
        raise ValueError("Q 必须为正")
    return float(q_pa) / (float(q_value) * G0)


def evaluate(beta: float, gamma: float, *, alt_m: float | None = None,
             speed_ms: float | None = None, n: float = 20.0) -> BgReference:
    """在参考工况下评估一对 `(β, γ)`。

    `R = (n/n*)²` 是机动惩罚比；`β_eff = β/(1+R)` 是把机动折回 BC 口径的等效值。
    """
    alt, speed, mach, q_pa, f_mach, cap_f = reference(alt_m, speed_ms)
    qv = q_sens(beta, gamma, cap_f, f_mach)
    ns = n_star_of(q_pa, qv)
    nn = float(n)
    ratio = (nn / ns) ** 2 if ns > 0 else math.inf
    return BgReference(alt_m=alt, speed_ms=speed, mach=mach, q_pa=q_pa, f_mach=f_mach,
                       cap_f=cap_f, beta=float(beta), gamma=float(gamma), q_sens=qv,
                       n_star=ns, n=nn, ratio=ratio, beta_eff=float(beta) / (1.0 + ratio))


def evaluate_point(p: BgPoint, *, alt_m: float | None = None,
                   speed_ms: float | None = None, n: float = 20.0) -> BgReference:
    """`point_of()` 的结果 → 参考工况下的机动指标。"""
    return evaluate(p.beta, p.gamma, alt_m=alt_m, speed_ms=speed_ms, n=n)


def evaluate_ginv(beta: float, ginv: float, *, alt_m: float | None = None,
                  speed_ms: float | None = None, n: float = 20.0) -> BgReference:
    """轴量口味的 `evaluate()`：收 `(β, ginv)`，内部经 `gamma_of()` 落回物理 γ。

    派生量（Q/n*/R/β_eff）与 `evaluate(beta, gamma_of(ginv))` **逐位相同**。
    """
    return evaluate(beta, gamma_of(ginv), alt_m=alt_m, speed_ms=speed_ms, n=n)


# --------------------------------------------------------------------- 等 n* 线


def nstar_slope(n_star: float, *, alt_m: float | None = None,
                speed_ms: float | None = None) -> float:
    """等 `n*` 线在 (β, ginv) 平面上的斜率 `m`：`ginv = m · β`。

    由 `n* = q/(Q·g0)`、`Q = √(βγF/f)` 反解出旧口径的双曲线

        γ = (q / (n*·g0))² · (f/F) / β  ≡  C / β

    取倒数即得**过原点的直线**

        ginv = β / C,     C ≡ (q / (n*·g0))² · (f/F)
        m    = 1/C = (n*·g0 / q)² · (F/f)

    这就是"两轴单调性一致"的数学形态：旧图上这族线是双曲线（`γ` 随 `β` 增大而降），
    新图上是**从原点发散的直线族**（`ginv` 随 `β` 线性增大）。
    斜率 `m ∝ n*²`：`n*` 越大线越陡 —— 也就是"同样的过载门限下，这型弹的转向能力更强"。
    """
    _alt, _speed, _mach, q_pa, f_mach, cap_f = reference(alt_m, speed_ms)
    if not (float(n_star) > 0.0):
        raise ValueError("n* 必须为正")
    return (float(n_star) * G0 / q_pa) ** 2 * (cap_f / f_mach)


def nstar_ginv_at(n_star: float, beta: float, *, alt_m: float | None = None,
                  speed_ms: float | None = None) -> float:
    """某条等 `n*` 线在给定 β 处的 **ginv**（= `m·β`）。"""
    return nstar_slope(n_star, alt_m=alt_m, speed_ms=speed_ms) * float(beta)


def nstar_curve(n_star: float, betas, *, alt_m: float | None = None,
                speed_ms: float | None = None) -> list:
    """等 `n*` 线：给定 β 序列，返回**轴量 ginv** 序列（`ginv = m·β`，过原点直线）。

    ⚠ 返回的是 **ginv [m²/kg]**，不是 γ —— 旧版返回 γ（双曲线）。要 γ 用 `gamma_of()`。
    """
    m = nstar_slope(n_star, alt_m=alt_m, speed_ms=speed_ms)
    return [m * float(b) for b in betas]


def levels(*, alt_m: float | None = None, speed_ms: float | None = None) -> tuple:
    """参考工况下的 `(q_pa, f_mach, cap_f, F/f)`，供图上标注口径用。"""
    _alt, _speed, _mach, q_pa, f_mach, cap_f = reference(alt_m, speed_ms)
    return q_pa, f_mach, cap_f, cap_f / f_mach


# --------------------------------------------------------------------- 图上的一行


@dataclass(frozen=True)
class BgRow:
    """β–ginv 图上的一行：固有坐标 + 参考工况下的派生量 + 气泡用的 ΔV。

    出图模块只吃这一种输入，免得它同时理解 `BgPoint`、`BlkMetrics` 和参考工况三样东西。

    `beta` 是横轴、`ginv` 是纵轴（都由属性/字段现给）；`gamma` 一并留着 ——
    产物与文案要能对照内核口径，**两列都记**（`ginv·γ == 1`，判据在
    `tests/test_bg_ginv.py`）。
    """

    key: str
    label: str
    beta: float          # 横轴
    gamma: float         # 物理量（内核口径），= 1/ginv
    dv: float            # 气泡大小 ∝ 理想 ΔV（本项目权威口径，与第一张图同源）
    q_sens: float        # Q
    n_star: float        # 临界过载
    ratio: float         # R(参考过载)
    beta_eff: float

    @property
    def ginv(self) -> float:
        """纵轴（轴量）：`1/γ` [m²/kg]，越大越好。"""
        return ginv_of(self.gamma)

    def as_dict(self) -> dict:
        return {"key": self.key, "label": self.label, "beta": self.beta,
                "gamma": self.gamma, "ginv": self.ginv, "dv": self.dv,
                "q_sens": self.q_sens,
                "n_star": self.n_star, "ratio": self.ratio, "beta_eff": self.beta_eff}


def rows(keys=None, *, version: str | None = None, alt_m: float | None = None,
         speed_ms: float | None = None, n: float = 20.0) -> list:
    """β–γ 图的完整数据集（顺序照 `pool`）。"""
    from . import pool                                   # 循环导入：只在调用时取

    out = []
    for e in pool.resolve_many(keys):
        p = point_of(e, version)
        m = mapping.blk_metrics(solver.profile(e.native, version))
        ref = evaluate_point(p, alt_m=alt_m, speed_ms=speed_ms, n=n)
        out.append(BgRow(key=p.key, label=e.label, beta=p.beta, gamma=p.gamma,
                         dv=m.dv, q_sens=ref.q_sens, n_star=ref.n_star,
                         ratio=ref.ratio, beta_eff=ref.beta_eff))
    return out


def row_of(entry, *, version: str | None = None, alt_m: float | None = None,
           speed_ms: float | None = None, n: float = 20.0) -> BgRow:
    """单个池项 → 一行（`rows()` 的单项版）。"""
    return rows([entry], version=version, alt_m=alt_m, speed_ms=speed_ms, n=n)[0]


# --------------------------------------------------------------------- 反解：格点 → 乘数


def cxaoa_scale_for(beta_target: float | None, gamma_target: float | None,
                    p: BgPoint, cx_scale: float) -> float:
    """`(β_t, γ_t)` → `cxaoa_scale`（乘到内核 `CxAoA`）。

    ⚠ **这一层收的是物理 γ，不是 ginv** —— 它是"注入内核"的最后一站（内核吃 `CxAoA`，
    而 `CxAoA ∝ γ·β`）。轴量换成 `ginv` 之后，调用方由 `scaling_for()` 统一转好再下来；
    这里再收一次 `ginv` 就会有两套口径。

    推导（`docs/BETA-GAMMA.md` §4.1）—— 这个平面上只有 `CxK` 与 `CxAoA` 两个自由度：

        CxK_new   = m / (SD · β_t)
        CxAoA_new = γ_t · SL² · CyK² / (m · SD · CxK_new)

    把 `CxK_new` 代进去，`SD` 被约掉：

        CxAoA_new = γ_t · β_t · SL² · CyK² / m²

    同理 `CxAoA_blk = γ_blk · β_blk · SL² · CyK² / m²`，相除即得**纯相对**形式：

        cxaoa_scale = (γ_t / γ_blk) / (β_blk / β_t) = (γ_t / γ_blk) / cx_scale

    直觉：β 变小 ⇒ 该点 `CxK` 变大（`cx_scale = β_blk/β_t > 1`），而 γ ∝ `CxK·CxAoA`，
    所以要维持同一个 γ，`CxAoA` 必须**按同样的比例缩小**。两轴由此解耦：
    改 β 只通过 `cx_scale` 一项影响 `CxAoA`，改 γ 是纯比例。

    `cx_scale` 由调用方给（就是同一个格点的 `mapping.cx_scale_for` 结果），
    避免这里再写一遍 `β_blk/β_t` 而和真正注入的乘数出现第二套口径。
    """
    if beta_target is None and gamma_target is None:
        return 1.0
    g_t = float(p.gamma if gamma_target is None else gamma_target)
    if g_t <= 0 or p.gamma <= 0:
        raise ValueError("γ 必须为正")
    if not (cx_scale > 0):
        raise ValueError(f"cx_scale 必须为正，收到 {cx_scale!r}")
    return (g_t / p.gamma) / float(cx_scale)


def scaling_for(p: BgPoint, metrics: mapping.BlkMetrics, *,
                beta: float | None = None, ginv: float | None = None,
                dv: float | None = None) -> Scaling:
    """β–ginv 平面的一个格点 → `Scaling`。

    与第一张图的 `mapping.scaling_for` **共用同一条通路**：`bc_target` 位置放的就是 `β_t`
    （β 与 BC 恒等，见 `docs/BETA-GAMMA.md` §2.1），所以 `cx_scale` 不需要第二套公式；
    本函数只多补一个 `cxaoa_scale`。

    **只收轴量 `ginv`**：内部经 `gamma_of()` 落回物理 γ 再注入内核（唯一换算点）。
    不收 `gamma=` 是为了掐掉"有的地方 γ、有的地方 1/γ"这条路 —— 想直接用 γ 就得自己写
    `gamma_of()`，那一眼就能看出是特例。

    `dv=None` ⇒ 该弹标称 ΔV（第二张图**固定 ΔV**，与第一张图扫 ΔV 相对）。
    """
    b_t = float(p.beta if beta is None else beta)
    g_t = p.gamma if ginv is None else gamma_of(ginv)
    base = mapping.scaling_for(dv, b_t, metrics)
    return replace(base, cxaoa_scale=cxaoa_scale_for(b_t, g_t, p, base.cx_scale))
