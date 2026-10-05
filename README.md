# missile-solver

War Thunder 主动雷达弹（ARH）的**纯求解器包**：把 6DOF 内核 + 求解层抽出来，**不含 GUI、不含
出图、不含产物**。运行期**只要 numpy**（`import missile_solver` 不会带进 matplotlib）。

📄 **接口契约（第三方从这里读）**：[`docs/SOLVER-API.md`](docs/SOLVER-API.md) —— `Scene` 的字段/坐标
约定/派生几何/校验、输入映射（`ΔV,BC → 乘数`；`β,γ` 平面）、`Shot` 与内核 68 列 `_CF_COLUMNS`、
内核能力边界（实测，不是转述）、两个档位，以及"**换求解器只动一个文件**"的步骤。
⚠ **本包只提供数据与计算** —— 画法、页面、产物（图册 / WebUI / 沙盒）在别的仓。

* 内核：`src/missile_solver/kernel/wt_missile.py` —— 2.59.0.28 原生 6DOF 例程的纯 Python 复刻
  （`MODEL = python-game-6dof-v1`，**不执行**游戏 ELF）。**逐字节保留**，SHA-256 即包的硬身份：
  `dad09caea2c31e0ef26a8327f3bb96c50f83b91bcb29d6b05616f23faba2f730`。
* 求解层：`spec`（契约）/ `solver`（唯一与内核接触的面）/ `mapping`（ΔV·BC ↔ 乘数）/
  `pool`（11 弹池）/ `bg`（β–ginv 能力平面）/ `catalog`（全弹池目录，168 预设）。
* 出处：从上游抽取源仓库（**私有**，含 GUI/出图/产物的那一个）**复制**抽取，源提交
  **`3850547477f3b988c86750b8cdd0df57a5131dfd`**（`3850547`）—— 只取求解器内核与求解层。

**两条身份口径**（本仓的验收就靠这两条）：

| 面 | 谁是权威 | 怎么核 |
| --- | --- | --- |
| **内核** | `solver.SOLVER_SHA256`（= `_data.KERNEL_SHA256` = `kernel/wt_missile.py` 的 sha256） | 判据①；换内核必须同时改这两处常量并在提交里说明 |
| **数据** | `data/checksums.json` 的逐条 sha256 | `missile_solver.verify_data(dir)` / `fetch_data.py --verify` |

## 装

```bash
pip install .            # 或：pip install -e .
```

`requires-python = ">=3.12,<3.15"`（内核硬要求 3.12+），依赖只有 `numpy>=2.5.2,<3`。

## 数据（**本包不分发游戏数据**）

内核要的是**解包器产出的 BLK 文本**（168 份 `*.blk` + `presets.json` + `manifest.json`），
**数据权利归 Gaijin，本仓不含、也不随包**。规范来源是作者的交付包
`wt-missile-backend-runtime` 里的 `inputs/resources/<版本>/**`（**完整形态**：168 份 blk +
`presets.json` + `manifest.json`；最小形态只有 11 份且缺 `presets.json`，**不能**用）。
自己准备一份，然后**设环境变量 `DATA_DIR`**：

```powershell
$env:DATA_DIR = "<放 168 份 blk + presets.json + manifest.json 的目录>"
```

* 数据从哪来、两种形态怎么分、为什么不能用 Datamine 仓里的 JSON（实测同一枚弹
  29,345 B JSON vs 20,972 B BLK，哈希对不上）⇒ **[`data/README.md`](data/README.md)**；
* **数据闸门**（169 条 + manifest 逐条 sha256；不符非零退出）：
  `python scripts/fetch_data.py --verify <DIR>`，或用包内 API
  `ok, problems = missile_solver.verify_data("<DIR>")`（脚本调的就是它，只有一份实现）；
* `DATA_DIR` 没设 / 指错时，报错是**可读的一句话 + 三步指引**（不静默）：
  `missile_solver.DataNotConfigured`。
* ⚠ `import missile_solver` **不需要**数据；只有真要用内核（跑工况 / 读 blk / 建目录）时才要求它。

包不会往 site-packages 里写东西：它在你机器的**缓存目录**里物化一个"内核 + 指向 `DATA_DIR`
的联接"当宿主（内核写死了 `ROOT/inputs/resources/<ver>`，没有 env 钩子；细节见 `_data.py`）。
换位置用 `MISSILE_SOLVER_HOME`。

## 最短示例（一行算出某个工况）

```python
import missile_solver as ms
shot = ms.run(missile="cn_pl12")          # 默认场景：50 km 迎头 / 10 km 同高 / 双方 300 m/s
print(shot.t_hit, shot.max_alt_m)         # → 45.1861  12725.1
```

需要自定义场景 / 乘数 / 档位时走底层 API：

```python
from missile_solver import mapping, pool, solver, spec

scene = spec.Scene(range_m=40_000.0, alt_m=8_000.0)          # 契约见 spec.Scene 的 docstring
std = pool.standard_entry("PL-12")
scaling = mapping.scaling_for(900.0, 3000.0, pool.metrics_of(std))   # 改 ΔV/BC
shot = solver.run(scene, missile=std.native, scaling=scaling, tier="standard")
print(shot.t_hit, shot.hit, len(shot.rows))                  # rows 的列名 = solver.output_columns()
```

### 档位 `standard` / `fast`

| 档位 | 含义 | 什么时候用 |
| --- | --- | --- |
| `standard`（默认） | 同一套物理，纯 Python 逐步积分 | **没有 C 编译器的机器就用它**；金标工况约 1~2 s |
| `fast` | 同一套物理，编译成 C 内核（`fastmode`） | 想快 20× 左右；**没有编译器时它会自动退回 `python-legacy`** 并在 `summary`/`Shot` 里报告原因 |

* 两档**数值逐位一致**（同一条金标 `t_hit = 45.1861 s`），差别只在耗时；
* `fast` 的编译产物按内核哈希缓存在用户缓存目录，可用 **`WT_MISSILE_FAST_CACHE`** 指向
  **已有的**缓存目录（换机器时不必重新编译）；
* 本仓判据一律用 `standard`，**不要求工具链** —— 想测 `fast` 得自己保证机器上有编译器
  或缓存，否则测的是"退回纯 Python"的那条路。
* 内核身份以 **`SOLVER_SHA256`** 为准，数据以 **`verify_data()` / `--verify`** 为准（见上面的表）。

## 目录（`catalog`）与静态站的契约

`catalog.build()` 把内核**全部 168 个预设**算成 `{dv, bc, gamma, ginv, …}` 的目录
（159 可算 / 9 条 `reason="需要动态解算"`，内核原话另存 `kernel_reason`）。
`catalog.write_json()` 落盘的 `missile_catalog.json` 就是**静态站**（上游抽取源仓库里的
`webui_static/figs2d.js`）用来在浏览器里算轴域、画散点的那份数据 —— 本包是它的**生产端**：

```python
from missile_solver import catalog
path = catalog.write_json()          # 默认 <cwd>/results/missile_catalog.json
catalog.verify(catalog.load(path))   # 逐值重算自校验
```

⚠ 与出图有关的「轴 oracle」（`--axes` 那套、`figures.adaptive_axis()`/`bg_axis()`）**没有**抽进来 ——
它属出图层（要 matplotlib）。本包只提供**数据**，画法在静态站那侧。

## 判据

```bash
python tests/run_tests.py            # 本仓自带运行器（与上游同一套 runner 风格）
python tests/run_tests.py -l         # 列用例
```

四组：① 内核身份（哈希/MODEL/VERSION/168 预设/68 输出列）② 金标工况
（`t_hit = 45.1861 s`、`max_alt = 12725.10 m`）③ 目录契约（168/159/9 + 11 弹池逐值同源 + 反证）
④ `import missile_solver` 不带 matplotlib、包里没有出图/GUI 模块、`pyproject` 依赖里没有 matplotlib。
**数据缺席时依赖数据的用例记 SKIP**（不是失败、也不算通过）—— 运行器会打印怎么放数据。

## 许可与声明

* 代码：**GNU Affero General Public License v3.0 only**（SPDX `AGPL-3.0-only`），见 [`LICENSE`](LICENSE)。
* **游戏数据（`*.blk` / `*.blkx` / `aces.vromfs.bin` 等）的权利归 Gaijin Entertainment**；
  本仓**不包含、不分发**任何游戏数据，也不与 Gaijin 存在隶属、赞助或背书关系。
  使用者需自行从合法来源获得数据，并自负合规责任。
* 本包与 [War-Thunder-Datamine](https://github.com/gszabi99/War-Thunder-Datamine) 无隶属关系；
  该仓只在"字段口径与出处核对"的意义上被引用（见 `data/README.md`）。
