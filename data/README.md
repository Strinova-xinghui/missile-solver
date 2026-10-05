# 数据怎么来（**本仓不分发游戏数据**）

这个包能算，靠两样东西：**内核源码**（随包，`src/missile_solver/kernel/wt_missile.py`）和
**游戏的导弹参数数据**（`.blk`）。后者**不入库、不随包** —— 数据权利归 **Gaijin**，
本仓与 Gaijin 无隶属关系。你（使用者）需要自己把数据放到一个目录里，然后设 `DATA_DIR`。

---

## 1. 内核要的数据长什么样（**这决定了"从哪弄"**）

内核 `wt_missile.py` 里写死了：

```python
ROOT = Path(__file__).resolve().parent
RAW  = ROOT / 'inputs/resources/2.59.0.28'         # import 时就读 presets.json + manifest.json
path = ROOT / 'inputs/resources' / version / f'gamedata__weapons__rocketguns__{name}.blk'
```

⇒ 它要的是一个目录，里面有 **168 份 `*.blk`（BLK 文本）+ `presets.json` + `manifest.json`**，
文件名形如 `gamedata__weapons__rocketguns__cn_pl12.blk`，内容形如：

```
rocketGun:b = true
preset_cost:i = 20
```

**这是"解包器"的产出**（`manifest.json` 里记了解包器与来源：`extractor` / `extractor_sha256` /
`archive_sha256`，归档是游戏安装目录里的 `aces.vromfs.bin`）。`_data.ensure_kernel_home()`
会把 `DATA_DIR` 联接/复制到内核认的那个位置（内核一个字节都不改，见 `_data.py` 的说明）。

## 2. ⚠ 上游 Datamine 仓给的是**另一种序列化**（实测，不是猜）

`gszabi99/War-Thunder-Datamine` 里的 `aces.vromfs.bin_u/gamedata/weapons/rocketguns/*.blkx`
是 **JSON 文本**：

```json
{
  "rocketGun": true,
  "preset_cost": 20,
```

同一枚弹实测（tag `2.59.0.28`，commit `6d41baa6c1ab31a4bc2232e3ed24b0a2b9efb90c`）：

| 来源 | 文件 | 大小 | sha256 |
| --- | --- | ---: | --- |
| 上游 Datamine（JSON） | `cn_pl12.blkx` | 29,345 B | `0aca5cbc554989e8632ead3535a02d15adb9eb7319a348c44b69fdd1d04968ec` |
| 内核要的（BLK 文本） | `gamedata__weapons__rocketguns__cn_pl12.blk` | 20,972 B | `fdbd68a941db7d56557ee25d8da4a9ba4ac2f40b1d491b02ea84c8c957162ed3` |

**两者字节不同 ⇒ 抄一份 JSON 过来改个名，哈希对不上、内核也解析不了。**
`scripts/fetch_data.py` 因此分成两件事，**不自己发明解包算法**：

1. **取上游 JSON**（可自动）：`--fetch-datamine` 用 partial clone（`--filter=blob:none`）+
   sparse-checkout 只取那个 `rocketguns/*.blkx` 目录，放到 `<OUT>/datamine/blkx/`，
   并写 `PROVENANCE.json`（repo / tag / commit / 逐文件 sha256）。
   **用途是"出处与字段核对"**（本仓的字段口径就是从这份 JSON 抄的），**不是给内核吃**。
2. **核对内核数据**（硬闸门）：`--verify <DIR>` 拿 `data/checksums.json` 里的 **172 条**
   sha256（168 blk + `presets.json` + `manifest.json` + `metadata.txt`）逐条比对
   `<DIR>`；**任何一条不符 / 缺件 / 多件都非零退出**。

## 3. 内核数据从哪来（唯一可靠来源：游戏本体的解包器）

`manifest.json` 记着它的来历：

```
archive          : .../War Thunder/aces.vromfs.bin      （你本机的游戏归档）
archive_sha256   : 303760d4897dd688fdc9d2503d1db8f6978c2d3bc6eef3d064ae2b48f26d6e9b
extractor        : research/20260916-arh-comparison/revised/source/extract_path
extractor_sha256 : 8436a925542ba81e79f71b9893a89a708a5005ac6791256ffd91866de04b7206
```

也就是说：**在装有 2.59.0.28 客户端的机器上，用那个只读解包器**（Rust 写的
`source/extract.rs`，见 `E:\WT\wt-missile-backend-runtime-1.0.1\research\20260916-arh-comparison\README.md`）
对 `aces.vromfs.bin` 抽取所选型号，得到上面那套 BLK 文本 ⇒ 放进 `DATA_DIR` ⇒ 用
`--verify` 核对哈希。**本仓不提供该解包器，也不提供数据**（这就是"不分发数据"的含义）。

> 换句话说：**哈希比对是"你手上的数据是不是那份 2.59.0.28"的闸门**，不是"从 GitHub 下载"的闸门。
> 从 Datamine 下 JSON 永远过不了这道闸门 —— 那是另一种格式，不是数据不对。

## 4. 用法

```powershell
# ① 取上游 JSON（出处/字段核对用；可自动）
python scripts/fetch_data.py --fetch-datamine --out .\datamine --tag 2.59.0.28

# ② 核对内核要的那套数据（把 <DIR> 换成你放 168 份 blk 的目录）
python scripts/fetch_data.py --verify <DIR>

# ③ 设环境变量后即可跑（包会把它联接到内核认的位置）
$env:DATA_DIR = "<DIR>"
python -c "import missile_solver as m; s = m.run(missile='cn_pl12'); print(s.t_hit)"
# → 45.1861（金标工况：50 km 迎头 / 10 km 同高 / 双方 300 m/s）
```

`--verify` 通过时打印 `OK：172/172 条哈希一致` 并以 0 退出；不通过时逐条列出**缺件 /
哈希不符 / 多出来的文件**，并以 1 退出（可直接进 CI）。
