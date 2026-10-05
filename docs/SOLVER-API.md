# 求解器输入/输出 API（换求解器只动一个文件）

> **本文件是镜像**：从上游抽取源仓库（含 GUI/出图/产物的那一个，源提交 `3850547477f3b988c86750b8cdd0df57a5131dfd`）的 `docs/SOLVER-API.md` 逐节搬来，**只改了两类东西**：① `missile_sim` → `missile_solver` 的路径名；② 只在那边成立的引用（WebUI / CLI / 图册 / `results/*.json` 的仓库内布局 / 进程池缓存层 `engine` / 航向指令模型层 `flightpath`）。**契约本身（`Scene` 字段与坐标约定、映射公式、`Shot` 与 68 列、内核能力边界、两档语义）一字未动。**

> **本文是求解器与调用方之间唯一的规范。** 调用方的其余部分（编排、扫描、等时线、出图、
> 产物 —— 都在别的仓）都只认本文描述的结构；换一个求解器、换一个版本、换一种物理，改的都是
> **`src/missile_solver/solver.py` 这一个文件**。
>
> 内核现状：`src/missile_solver/kernel/wt_missile.py`（**自有代码**；SHA-256 `dad09cae…f730`；输出 **68 列**，逐列契约见 §3.1），
> 自报 `MODEL = python-game-6dof-v1`，2.59.0.28 原生 6DOF 的纯 Python 复刻。

---

## 0. 一分钟

```python
from missile_solver import solver, pool, mapping
from missile_solver.spec import Scene

scene = Scene(range_m=50_000, alt_m=10_000, target_speed_ms=300, target_heading_deg=180)

shot = solver.run(scene, missile="cn_pl12", tier="fast")
print(shot.t_hit, shot.backend)          # 45.1861  compiled-c

# 只改推力与阻力系数，扫 (ΔV, BC)
m = pool.metrics_of(pool.standard_entry())        # PL-12 权威量
sc = mapping.scaling_for(800.0, 3000.0, m)
shot = solver.run(scene, missile="cn_pl12", scaling=sc, tier="fast")
```

四个入口函数就是全部 API：

| 函数 | 作用 |
| --- | --- |
| `solver.load_solver(verify=True)` | 懒加载内核 + SHA-256 校验；不符抛 `SolverUnavailable` |
| `solver.identity()` | 身份块（不跑积分）：`model` / `version` / `sha256` / `archive_sha256` / `n_profiles` |
| `solver.profile(native_name)` | 读一份 blk 参数块（弹池现算 ΔV/BC 用） |
| `solver.run(scene, *, missile, scaling=None, tier, want_rows=False)` | 跑一次，返回 `Shot` |

---

## 1. 输入 API：`Scene`（用户视角）

`Scene` 描述**用户脑子里的那一场交战**（水平距离 + 角度），不是求解器的 3D 向量。
折成向量的唯一落点是 `solver.scenario_dict()` —— 换个求解器时这个折法就是要重新写的东西。

### 1.1 坐标约定（与内核一致）

* **x / z 水平、y 向上**；
* 零航向沿 **+x**，正航向转向 **+z**；
* 俯仰以**抬头为正**；发射方在原点上方 `alt_m`。
* 速度一律写成 **标量 + 航向 + 俯仰**（`spec.velocity_vector()`），因此 `|v|` 恒等于标量。

### 1.2 字段

| 组 | 字段 | 默认 | 含义 |
| --- | --- | --- | --- |
| 几何 | `range_m` | 50000 | 双方**水平**距离 [m]（**不是斜距**） |
| | `alt_m` | 10000 | 发射高度 [m] |
| | `target_alt_m` | None | 目标高度；None ⇒ 与发射同高 |
| | `target_bearing_deg` | 0 | 目标方位角 [°]，0 = +x，正 → +z |
| 载机 | `vm0` | 300 | 速度标量 [m/s] |
| | `launch_heading_deg` | 0 | 航向 [°] |
| | `launch_pitch_deg` | 0 | 速度俯仰 [°]（正 = 爬升） |
| 敌机 | `target_speed_ms` | 300 | 速度标量 [m/s] |
| | `target_heading_deg` | 180 | 航向 [°]（180 = 迎面） |
| | `target_pitch_deg` | 0 | 速度俯仰 [°]（正 = 爬升） |
| 技术 | `duration_s` | 150 | 请求时域上限 [s]；实际可能提前终止 |
| | `dt_s` | 1/48 | 物理步长；**固定步长是 C 快路径的条件** |
| | `observation` | `ideal` | `ideal` 或 `locked` |
| | `wind_m_s` | (0,0,0) | 世界坐标风速 |
| | `ground_height_m` | 0 | 平地高度 |
| | `proximity_fuse` | True | 是否用资源近炸设置 |
| | `hit_radius_m` | 0 | 几何接触半径；**0 = 只用内核自己的近炸判定** |
| | `maneuvers` | () | 内核原生能力：世界系**分段常值加速度**，实际形状 `((t_s, (ax, ay, az)), …)`，时刻必须**非负且严格递增**（内核硬校验，见 §4）。⚠ 把"航向指令"解析编译成这里的**模型层不在本包内** |

### 1.3 派生几何（只读属性，`as_dict()` 里也带上）

| 属性 | 值 |
| --- | --- |
| `target_alt` | `target_alt_m or alt_m` |
| `range_slant_m` | `√(range_m² + Δh²)` —— **内核真正吃的是这个** |
| `launch_position_m` | `(0, alt_m, 0)` |
| `target_position_m` | `(range·cos β, target_alt, range·sin β)` |
| `launch_velocity_m_s` / `target_velocity_m_s` | `velocity_vector(速度, 航向, 俯仰)` |
| `head_on` | 航向差 > 90°（只用于文案，不参与物理） |

### 1.4 校验

`Scene.check() -> list[str]`（空 = 通过）：水平距离非负、`duration_s > 0`、
`dt_s ∈ [0.00025, 1/48]`、`observation ∈ {ideal, locked}`、`hit_radius_m ≥ 0`、速度标量非负。
**不抛异常** —— 由**调用方**决定怎么报（本包只提供数据与计算：`spec.Scene.check()` 返回问题清单，不替调用方决定抛不抛）。

---

## 2. 输入映射：`(ΔV, BC) → 乘数`

**扫描时基准弹除推力与阻力系数之外一个量都不动。** 算法只在 `mapping.py`：

```text
thrust_scale = ΔV_target / ΔV_blk            → 乘到 force / force1（等价于改比冲）
cx_scale     = m_dry / (BC_target · SD · CxK_blk)   → 乘到 CxK（等价于改阻力系数）
                                                       SD = π(d/2)²，迎面面积
```

权威量（`ΔV_blk` / `BC_blk` / `m_dry` / `CxK` / `caliber`）由 `mapping.blk_metrics()` 从
**`DATA_DIR` 里的 blk 现算**（分析侧还有一份同式实现，两边逐行同式）：

```text
fu1 = mass − massEnd;      Isp1 = force  · timeFire  / (9.81 · fu1)
dV1 = Isp1 · 9.81 · ln(mass / (mass − fu1))
fu2 = massEnd − massEnd1;  Isp2 = force1 · timeFire1 / (9.81 · fu2)     # 有二级时
dV2 = Isp2 · 9.81 · ln((mass − fu1) / (mass − fu1 − fu2))
ΔV  = dV1 + dV2（无二级则 dV1）;   dry = mass − fu1 − fu2
BC  = dry / (CxK · (caliber/2)² · π)          # caliber 在 blk 里已是【米】
```

**两个曾算错的坑**：二级装药在 `massEnd1` 里（`mass − massEnd` 只是一级）；
`caliber` 是米不是毫米。`mapping.BlkMetrics` 的 docstring 里写着，改之前先读。

乘数的**施加**在 `solver._apply()`：monkeypatch 内核的模块级 `load_profile`，
命中所有同名标量叶（`_patch_profile` 就地改写），调用结束在 `finally` 里还原。
`compiled_simulate` 与 `scene_simulate` 都走 `load_profile`，所以 **C 档同样吃到乘数**。

### 2.1 第二个平面：`(β, γ) → 乘数`

`spec.Scaling` 还有第三个乘数 `cxaoa_scale`（默认 1.0 → 标称），只有 β–γ 平面会用到。
算法在 **`bg.py`**（`bg.scaling_for()`），不在 `mapping.py` —— `mapping` 的域是 `(ΔV, BC)`：

```text
cx_scale     = β_blk / β_t                          （与 BC 那条同式：β ≡ BC）
cxaoa_scale  = (γ_t / γ_blk) / cx_scale             → 乘到 CxAoA（诱导阻力项）
```

⚠ **两个必须一起守住的实现细节**（都踩过）：

1. **`_patch_profile()` 只能改已存在的键**。`CxAoA` 在 11 型 blk 里**一个都没有**，
   所以"直接赋值"的写法是**静默 no-op** —— 不报错、不生效，结果看起来完全正常
   （症状：γ 轴在图上完全不动，逐位相同）。现在末尾有"补缺"分支，把 blk 没有的键写到顶层。
2. **注进去的必须是"有效值"**。内核读的是 `p.get('CxAoA', 9.0) × (wingAreaMult² if
   applyWingAreaMultToCxAoA)`，所以 `solver.effective_cx_aoa()` 先把这条规则算准，
   注入时同时把 `applyWingAreaMultToCxAoA` 关掉；逐弹对拍属于使用层的判据。

**加/改乘数就是"输入 → 结果"的语义变化**：使用层若有缓存，必须升它的 schema （本包不带进程池/缓存层 `engine`，但这条口径对任何缓存层都成立）。

---

## 3. 输出 API：`Shot`

| 字段 | 语义 |
| --- | --- |
| `ok` | 求解是否成功返回（内核异常 / 环境缺失时为 False，`error` 带原因） |
| `event` | `time_limit` / `lifetime` / `ground` / `contact` / `proximity_fuse` |
| `hit` | `event ∈ {contact, proximity_fuse}`；且 `hit_radius_m > 0` 时还要 `miss_m ≤ hit_radius_m` |
| `t_end` | 实际飞行时长 [s] |
| `t_hit` | 命中时间；**未命中是 `nan`** —— 不要用 0 或 -1 代替，读图的人会当成命中 |
| `miss_m` | 终止前最小几何距离 [m] |
| `max_alt_m` / `v_impact` | 最高点 / 终点速度 |
| `dv_measured` / `t_burnout` | 燃尽点速度增益 / 燃尽时刻 |
| `m_launch` / `m_dry` | 起飞质量 / 全程最小质量 |
| `backend` | `compiled-c` / `python-legacy` / `python-standard` |
| `fallback_reason` | fast 档回退原因（内核自报） |
| `resource` | 形如 `wt-missile/2.59.0.28+compiled-c` |
| `rows` | 可选逐采样**精简投影**（`t/x/y/z/v/mass/thrust/range` ＋ 2026-10-02 新增的 `mach/aoa_deg/aoa_eff_deg` ＋ `tx/ty/tz`），`want_rows=True` 才有；**内核原始行是 68 列**，见 §3.1 |
| `cache_hit` | 是否来自缓存 |

**两条纪律**：

1. **`backend` / `fallback_reason` 只能来自真跑过的 summary，不许按档位名猜。**
   曾经猜过一次 —— 机器没有 C 编译器时产物就在撒谎。`standard` 档报
   `python-standard` 是构造事实（`fastmode` 恒为 False），所以不跑也能报；`fast` 档
   没跑过只能报 `unreported`。
2. **`t_hit` 用 `nan` 表示未命中**，出图与 CSV 都按"非有限 = 未命中"处理。

### 3.1 逐采样输出列：内核 `_CF_COLUMNS`（**68 列**，2026-10-02 起）

`rows` 的**完整**形态就是内核 `kernel/wt_missile.py` 的 `_CF_COLUMNS`：**68 列**
（交付包原样 65 列 ＋ 2026-10-02 用户拍板新增的 3 列）。**口径：这些列必须由内核给出，
调用方不自己派生** —— 换内核时，新内核必须给出这 68 列。

| 新列（**追加在末尾**） | 定义 | 单位 | 与内核既有代码的关系 |
| --- | --- | --- | --- |
| `mach` | `\|air\| / sound(alt)` | 无量纲 | 内核本来就在算这个比值喂 `_mach_function()`，只是以前没输出 |
| `aoa_deg` | `angle(nose, ûair)`（**运动学攻角**） | deg | `air = v − wind`、`nose = R[:,0]` |
| `aoa_eff_deg` | `angle(nose, ûflow)`（**有效攻角**） | deg | `flow = û(air + R @ [0, lever·ω_z, −lever·ω_y])` —— **`aero()` 实际用的那个 flow** |

* **两条定义纪律**（实现处也有注释）：
  1. 攻角是 `acos` 出来的**非负角** `[0°, 180°]`。要带符号必须**另立列名**并写清约定 ——
     现在**没有**带符号的攻角列。
  2. 输出行是**步后**状态；C 路 `record()` 里的 `m->r` 是**步前**姿态（`body()` 只就地更新
     `m->q`），所以新列**就地重算** `R`；Python 路用行自己的 `q` 现算 —— 两条路同一条式子。
* **两档一致**（同物理、不同算术后端）：实测最大差 `mach` **4.6e-7**、两个攻角 **1.2e-4 deg**；
  与既有 `air_speed_m_s` 的两档相对差 1.2e-7 同源（`mach` 就是它除声速）。
* **判据**（`tests/test_solver_api.py`）：`test_output_columns_contract`（列数 = 68 ＋ 新列在末尾
  ＋ **既有 65 列的名字/顺序指纹** `00ea06f4…`，防止有人插在中间打乱索引）、
  `test_new_columns_are_kernel_output`（内核行有、`Shot.rows` 也接出来了）、
  `test_mach_matches_airspeed_over_sound`（相对差 ≤ 1e-12）、
  `test_new_columns_match_independent_recompute`（**四元数 ＋ 速度独立复算**，不复用内核 helper）、
  `test_tiers_agree_on_new_columns`、`test_new_columns_golden_example`（数值锚点）。
* **数值锚点**（`standard` 档、金标工况、`t ≥ 10 s` 的第一行：`t = 10.000000 s`、
  `alt = 12208.63 m`）：`mach = 3.5489671066`、`aoa_deg = 1.4947874925`、
  `aoa_eff_deg = 1.4953665185` —— **有效攻角比运动学攻角大 0.0005790°**（力臂×角速度那一项
  加上的角）。发射段转弯最快那一点（`t = 0.645833 s`）：`aoa_deg = 16.2237°`、
  `aoa_eff_deg = 16.2313°`（差 0.0077°）。
* **改这份契约要同时动三处**（漏一处就是"产物的身份在撒谎"）：
  `src/missile_solver/kernel/wt_missile.py` → `solver.SOLVER_SHA256` 与 `_data.KERNEL_SHA256`
  （判据①把这两处钉在一起）→ 提交信息里说明为什么。**并且**必须让 `fast` 的编译缓存失效
  （缓存键随源码变，文件名随之变）。
* **"加列"不是「输入 → 结果」变化**：契约规定 `want_rows=True` 的调用**永远重算、不读也不写
  缓存**（本包不带缓存层，但任何使用层的缓存都要照这条实现），所以逐采样输出不会命中"旧缓存
  缺新列"。判据要**实测命中数为 0**（而不是靠读代码），并配一条正向对照（确认缓存本身没坏）。
  ⚠ 这只对"**加列**"成立；改 `Scene`/`Scaling` 语义仍然**必须**让缓存失效。
* **输出 schema 也有身份**（2026-10-02 加，**不 bump `MODEL`**）：`solver.identity()` 现在带
  `output_columns`（列数 = 68）、`output_columns_sha256`（列名指纹，实测 `f097c34e…`）、
  `output_columns_tail`。理由：`MODEL` 描述物理、物理逐位未变，变的是 schema ⇒ 给 schema 单独一个身份，
  按列读产物的消费者才能自己发现 schema 变了。
* **没改的东西**：`MODEL`（仍是 `python-game-6dof-v1`）与 `VERSION`；物理一步没动 ——
  把改前那一版内核（`1bfa0fa2…`）与现在这一版在**同一个进程**里各跑一遍，金标工况
  **2170 行 × 既有 65 列全部逐位相同**（0 处差异；`standard` 档；`fast` 档由"两档一致"判据覆盖）。

---

## 4. 内核能力边界（实测，不是转述）

| 能力 | 结论 | 证据 |
| --- | --- | --- |
| **完整 3D** | ✅ | 目标/载机位置与速度都是 3 分量；输出 **68 列**（§3.1）含 `target_x/y/z_m`、`target_vx/vy/vz_m_s`、`target_accel_x/y/z_m_s2`。侧向 5 km 偏移、±120 m/s 侧滑、爬升 120 m/s 均跑通 |
| **实时敌机状态（闭环）** | ❌ **没有入口** | 只吃**预先给定**的完整目标时域。UI 上的"实时"= 改一下就重算 |
| 恒定转弯率 | ✅ `target.turn_rate_rad_s` | |
| 分段机动 | ✅ `target.maneuvers = [{time_s, acceleration_m_s2[3]}]` | |
| 录制轨迹 | ✅ `target.samples = [{time_s, position_m[3], velocity_m_s[3]}]` | |
| 三者混用 | ❌ 明确报错 | `Recorded target needs at least two samples and no maneuvers`<br>`Invalid constant turn rate or conflicting target motion` |

**`samples` 的硬约束**（实测报错原文）：

> `Recorded target must cover time zero through duration_s; no extrapolation`

必须**从 t=0 起**、**覆盖到 `duration_s` 末尾**、时间严格递增；只给到 4 s 而请求 8 s 直接报错。

**不支持的型号是硬拒绝**，不做近似替代（`unsupported()` 返回原因，168 预设里 156 可算、
12 明确拒绝：外环控制器 `orientationAutopilot` / `propulsionAutopilot` / `useThrustVectoring`、
重复 `endSpeed`、非恒定 `Cy` 表）。

**内核没有"导弹开机 / pitbull"事件。** `summary['seeker_assumptions']` 恒为
`{'permanent_lock': True, 'unlimited_acquisition': True, 'unlimited_tracking_rate': True,
'unlimited_off_boresight': True}`（`kernel/wt_missile.py`），逐采样行里的
`observed_range_channel_valid` 由导引头的**静态通道能力**决定（`wt_missile.py:1886` =
`p[P_locked] && p[P_range_valid]`），**不随时间变** —— 它回答的是"这枚弹有没有测距通道"，
不是"导引头开没开机"。⇒ **"导引头开机时刻"这个量内核不给**，由调用方自己定义（见下一条）。

**`guidance.lockDistance` 内核不读 —— 正好拿它当"导引头开机"的物理定义，不用改内核。**
在 `wt_missile.py` 里 grep 该字段名是**零命中**（只在 blk 资源里出现），所以口径由调用方定：
120A / 120C / 120D / MICA EM / Derby / AAM-4 / R-Darter = **20000 m**，
PL-12 / PL-12A / R-77 / R-77-1 = **16000 m**。
⚠ `radarSeeker.receiver.range` 对 11 弹**全是 16000**，**不能**当区分依据。
求"首次 `range ≤ lockDistance`"要用**一遍法**（在**不含该腿**的轨迹上求），**不要迭代** ——
那是自指条件，拿已加腿的轨迹迭代会漂移。
使用层要"开机时刻"就按这条口径自己求：取首个 `range ≤ lockDistance` 的**那一行**，该行 `t` 就是 `t_beam`（分辨率 ≈0.02 s，**不插值**）。

**`solver.trajectory()` 的 slim 行现在含 `z` / `tx` / `ty` / `tz`（画 3D / 俯视图必须知道）。**
`Shot.rows`（`solver.trajectory()` 的 slim 行）的键里，`x` / `z` 是**水平面**（与内核同口径：
`heading 0 = +x`、`90 = +z`）、**`y` 是高度**；`t?` 前缀是**目标**同一时刻的位置。
⚠ 名字与内核原始列**不同**：原始行叫 `time_s` / `missile_x_m` / `missile_y_m` / `missile_z_m` /
`speed_m_s` / `mass_kg` / `thrust_N` / `range_m` / `target_{x,y,z}_m`，别混用。

**`want_rows=True` 只影响要不要带回逐采样行**（条目本来就**不带行**）：给 `Shot.rows` 加键
**不是**「输入 → 结果」语义变化，不用动任何缓存 schema；反之，任何改变缓存里那些**标量字段**
含义的改动照旧必须 bump。

**`maneuvers` 的语义与硬校验**（与上表"三者混用"互斥、与上文 `samples` 覆盖时域那条并列）：

* 世界系**分段常值加速度**，每条从自己的 `time_s` 生效、直到下一条接管；
* `time_s` 是**绝对**时间（不是相对上一条的偏移）；
* **固定方向的加速度不是转弯** —— 真转弯要按 `ω = n·g0 / |水平速度|` 逐点转动加速度方向；
* 时刻必须**非负且严格递增**，否则内核当场抛出（`wt_missile.py:247-248`）：

> `ValueError: Maneuver times must be nonnegative and strictly increasing`

**是硬拒绝，不是"静默按错序执行"**（旧表述写错了，已更正）。
⚠ 真正的**静默**风险是把**相对偏移**当绝对时刻填：那样的时刻表"看起来"递增、
内核照样接受，但结果全错。

---

## 5. 两个档位

| 档位 | 含义 | 后端 |
| --- | --- | --- |
| `standard` | 纯 Python 逐物理步 | `python-standard` |
| `fast` | **同一物理**编译成 C 内核 | `compiled-c`，缺编译器时内核自报回退 `python-legacy` |

* 两档**只差算术后端，不差物理**：金标工况实测 `Δmax_alt = 0`、`Δt_hit = 0`。
* C 内核**编一次即缓存**在用户缓存目录；可用 `WT_MISSILE_FAST_CACHE` 指向已有的缓存
  （换机器时不必重新编译）。⚠ **加速比因机器而异**，别把某一台机器的数字当常数，要看 `Shot.backend`。
* ⚠ 任何「输入 → 结果」语义变化（例如 `Scene.range_m` 从斜距改成水平距离那一次）都必须
  让使用层的缓存失效，否则新工况会命中语义完全不同的旧结果，而且**看起来一切正常**。
* **本包的底层 API 默认 `standard`**（`solver.run()`），不是笔误：底层 API 不该默认依赖一台
  装了 C 编译器的机器。使用层想默认 `fast` 是它自己的选择 ⇒ **跨层调用时显式传 `tier=`**，
  别赌默认值。

---

## 6. 换求解器的步骤

1. **实现四个函数**：`load_solver` / `identity` / `profile` / `run`，保持 `Shot` 字段语义
   （尤其是 `t_hit = nan` 表示未命中、`backend` 只能来自证据）。`Scene`/`Shot` 不用动。
2. **对齐权威量**：新内核的 blk 参数必须能喂进 `mapping.blk_metrics()`，否则
   `ΔV_blk` / `BC_blk` 与既有权威表对不上，散点和乘数会同时偏。
3. **跑自检**：`python tests/run_tests.py -k kernel_identity` 与 `-k gold_case`
   （内核身份与哈希、金标工况 —— 对不上就红）。
4. **升缓存 schema**：使用层若有缓存，任何影响"输入→结果"的改动都要让它失效/递增，
   旧缓存作废重建，绝不混口径。
5. 如果内核换了**资源版本**：`solver.SOLVER_SHA256` 与 `_data.KERNEL_SHA256` 必须
   **同时**改（判据①钉住这两处），并在提交信息里说明为什么。

---

## 7. 边界（本 API 不保证的事）

* **不保证绝对精度**：这是对游戏 2.59.0.28 的复刻，不是真值。金标 AIM-120D / 60 km 工况
  `max_alt` 与原生对拍到 **−0.40 m（0.0038%）**，但那只说明"复刻得准"，不说明"游戏等于现实"。
* **不做击毁评估**：`kill_assessed` 由内核恒为 False；接触/近炸**不等于**击毁。
* **无损伤、无地形、无多目标、无载机雷达/数据链调度**；`locked` 观测仍是永久跟踪假设。
* **`fast` 档的加速比不是所有机器的常数**：换机器要看 `Shot.backend`，别看档位名。
