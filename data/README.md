# 数据怎么来（**本仓不分发游戏数据**）

这个包能算，靠两样东西：**内核源码**（随包，`src/missile_solver/kernel/wt_missile.py`）和
**游戏的导弹参数数据**（`.blk`）。后者**不入库、不随包** —— 数据权利归 **Gaijin**，
本仓与 Gaijin 无隶属关系。你需要自己把数据放到一个目录里，然后设 `DATA_DIR`。

---

## 1. 规范来源：作者的交付包 `wt-missile-backend-runtime`

**取其中 `inputs/resources/<版本>/**` 那一层当 `DATA_DIR`**（本轮钉 `2.59.0.28`）。
该目录里应当是**扁平的 168 份 `*.blk` + `presets.json` + `manifest.json`**：

```
<DATA_DIR>/
├── gamedata__weapons__rocketguns__cn_pl12.blk     ← 168 份这样命名的 BLK 文本
├── gamedata__weapons__rocketguns__cn_pl12a.blk
├── …（共 168 份）
├── presets.json                                    ← 内核 import 期就要读
└── manifest.json                                   ← 逐文件 sha256 + archive_sha256
```

文件内容形如（**BLK 文本**，不是 JSON）：

```
rocketGun:b = true
preset_cost:i = 20
```

⚠ **`presets.json` 与 `manifest.json` 一个都不能少**：内核 import 时会当场核对
`manifest.json['files']['presets.json']` 的哈希，缺或不符直接抛
`Preset catalog identity mismatch`。

### ⚠ 交付包里有两套形态，别拿错

`wt-missile-backend-runtime` 的 `inputs/resources/<版本>/` 有**完整**与**最小**两种：

| 形态 | 目录里有什么 | 能不能当 `DATA_DIR` |
| --- | --- | --- |
| **完整**（要用这个） | 168 份 blk + `presets.json` + `manifest.json` | ✅ 能（`--verify` 过 169 条 + manifest） |
| **最小**（本机实测过一份 `1.0.1`） | **11 份 blk + `manifest.json`，没有 `presets.json`** | ❌ 不能：内核 import 期就缺 `presets.json` |

所以**先数一数**：`*.blk` 要 ≥168，且 `presets.json` 必须在。**别凭"目录名对"就当真** ——
拿最小形态去跑，报错发生在内核 import 之后，看起来像"包坏了"。

## 2. 核对：`verify_data()` / `fetch_data.py --verify`（硬闸门）

```powershell
# 脚本（免安装可跑；它内部调的就是包里那份实现）
python scripts/fetch_data.py --verify <DATA_DIR>      # 通过 → 退出码 0；任一不符 → 退出码 1
```

```python
# 或者代码里自查（同一个实现，只有一份）
import missile_solver as ms
ok, problems = ms.verify_data("<DATA_DIR>")     # 不给参数 ⇒ 用 $DATA_DIR
```

核对口径（`data/checksums.json` 与包内 `missile_solver/data/checksums.json`，两份逐字节相同）：

* **169 条必核**：168 份 `*.blk` + `presets.json` ⇒ 逐条 sha256 必须相符；
* **`manifest.json`**：它**不在自己的 files 表里** ⇒ 单独记 `manifest_sha256`，必须相符；
* 3 个解包器侧辅助件（`metadata.txt` / `rocket-inventory.json` / `rvv-ae-extraction.txt`）：
  内核不读、最小形态里也没有 ⇒ **有就核、缺席不算失败**（`problems` 里带 `⚠` 前缀，不影响 `ok`）。

**实测**：完整数据 `OK 170/170` 退出码 0；把一份 blk 改一个字节 ⇒ 报出期望/实际并退出码 1；
指向最小形态/空目录 ⇒ 报缺件并退出码 1。

## 3. ⚠ 上游 Datamine 仓的 JSON **不能**当 `DATA_DIR`

`gszabi99/War-Thunder-Datamine` 的 `aces.vromfs.bin_u/gamedata/weapons/rocketguns/*.blkx`
是 **JSON 文本**，而内核要的是**解包器产出的 BLK 文本**。同一枚弹实测（tag `2.59.0.28`，
commit `6d41baa6c1ab31a4bc2232e3ed24b0a2b9efb90c`）：

| 来源 | 文件 | 大小 | sha256 |
| --- | --- | ---: | --- |
| 上游 Datamine（JSON） | `cn_pl12.blkx` | 29,345 B | `0aca5cbc554989e8632ead3535a02d15adb9ed7319a348c44b69fdd1d04968ec` |
| 内核要的（BLK 文本） | `gamedata__weapons__rocketguns__cn_pl12.blk` | 20,972 B | `fdbd68a941db7d56557ee25d8da4a9ba4ac2f40b1d491b02ea84c8c957162ed3` |

**两者字节不同 ⇒ 抄一份 JSON 过来改个名字，哈希对不上、内核也解析不了。**
所以 `scripts/fetch_data.py --fetch-datamine` 只做**出处核对**（partial clone
`--filter=blob:none` + sparse-checkout 取那一个目录，并记 repo / tag / commit / 逐文件 sha256），
**它产出的目录不是 `DATA_DIR`**。内核要的那套数据只有**交付包**里那一种形态。

```powershell
# 上游 JSON（字段/出处核对用；不是 DATA_DIR）
python scripts/fetch_data.py --fetch-datamine --out .\datamine
python scripts/fetch_data.py --explain        # 打印数据出处与两条步骤
```

## 4. 用起来

```powershell
$env:DATA_DIR = "<放 168 份 blk + presets.json + manifest.json 的目录>"
python -c "import missile_solver as m; s = m.run(missile='cn_pl12'); print(s.t_hit)"
# → 45.1861（金标工况：50 km 迎头 / 10 km 同高 / 双方 300 m/s）
```

包不会往 site-packages 里写东西：它在你机器的**缓存目录**里物化一个"内核 + 指向 `DATA_DIR`
的联接"当宿主（内核写死了 `ROOT/inputs/resources/<版本>`，没有 env 钩子，见 `_data.py`）。
可用 `MISSILE_SOLVER_HOME` 换宿主位置、`MISSILE_SOLVER_CHECKSUMS` 换哈希表位置。
