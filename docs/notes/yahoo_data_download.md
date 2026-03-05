### Planner Response

**1. 为什么默认下载的数据比较老？**

你目前所使用的默认数据获取指令（例如 `python scripts/get_data.py qlib_data`）下载的是预先打包好的离线 `bin` 文件。根据下载版本的不同（v1 或 v2），数据仅仅截至 2020年9月 或 2021年6月。
此外，由于为了优化文件下载体积，这些离线数据删减了部分字段，因此**无法**在这些离线数据的基础上增量更新后续的新数据。

---

**2. 如何获取最新的股票数据（截至昨天 2026-03-03）？**

为了获取最新数据，你需要使用 Qlib 中内置的 `data_collector`，这个工具能够连接到雅虎财经（Yahoo Finance）拉取最新的逐日明细数据，并将其处理转换为 Qlib 可用的 `bin` 格式。

下面是获取并转换数据的**三个核心步骤**（假设你当前在 Qlib 的项目根目录下 `/home/kc/Development/qlib/` 运行）：

### Step 1: 拉取原始数据 (Download Data)
我们需要将指定时间内的 CSV 原始数据拉取到本地临时目录。

```bash
# 由于 end 属于左闭右开区间，如果要获取到 2026-03-03 的数据，结尾需要设置到后一天 2026-03-04
python3 scripts/data_collector/yahoo/collector.py download_data \
    --source_dir ~/.qlib/stock_data/source/cn_data \
    --start 2000-01-01 \
    --end 2026-03-04 \
    --delay 1 \
    --interval 1d \
    --region CN
```
*这会在指定的 `source_dir` 下为每一只股票下载包含高（High）、开（Open）、低（Low）、收（Close）、成交量（Volume）和复权收盘价（Adj Close）等基础字段的 CSV 原始文件。*

### Step 2: 数据的标准化与复权处理 (Normalize Data)
股票的纯原始价格无法直接用于量化分析，因为随着派息或分红，价格会出现断层跳空，我们需要将其抹平。

```bash
python3 scripts/data_collector/yahoo/collector.py normalize_data \
    --source_dir ~/.qlib/stock_data/source/cn_data \
    --normalize_dir ~/.qlib/stock_data/source/cn_1d_nor \
    --region CN \
    --interval 1d
```
这一步对于**数据格式处理的底层逻辑是这样的**：
1. **构建前复权价格**：脚本会使用雅虎财经本身携带的**复权收盘价 (Adj Close)** 作为基准，反推得出复权系数，然后用来重新计算每天的开、高、低、收盘价。这样可以消除历史上的股本拆分或者现金分红对股价曲线造成的影响。
2. **价格基准归一化**：为了统一刻度，以使得各股之间的价格基点在回测及特征计算中具有可比性，系统会将每只股票出现的**第一个有效交易日的收盘价格强制归一化为 1**。

### Step 3: 转储为高性能 Qlib Bin 格式 (Dump Data)
Qlib 是依靠一种特殊的列式存储来实现高速读取的，需要将 CSV 转为 Bin 文件以覆盖到你目前的运行环境中去。

```bash
# 将转储的最终结果直接存入 Qlib 的默认数据存放路径（假设为 ~/.qlib/qlib_data/cn_data）
python3 scripts/dump_bin.py dump_all \
    --data_path ~/.qlib/stock_data/source/cn_1d_nor \
    --qlib_dir ~/.qlib/qlib_data/cn_data \
    --freq day \
    --exclude_fields date,symbol \
    --file_suffix .csv
```
这一步处理的**格式构成细节**为：
1. **生成 `instruments` 和 `calendars`**：遍历所有日期和股票名，提取出交易日历表（如 `day.txt` 包括了所有的真实交易日）及成分股生命周期（如 `all.txt` 包括每只股从起跑到退市所覆盖的起止时间）。
2. **特征列存储（Column-store）转化**：Qlib 会采用 `numpy` 对标准化后的数据按列进行切割。这就意味着同一支股票中的 Open、Close、Volume 会被分别存成 `open.bin`、`close.bin`、`volume.bin` 等独立的文件。这是 Qlib 回测极速的关键（底层利用 `mmap` 进行内存映射）。

---

**3. 在你的模型中运用新数据**

重新完成上述三个步骤后，最新的历史数据就已经覆盖在 `~/.qlib/qlib_data/cn_data` 目录下了。

你可以正常切回 `qlib/examples` 并直接跑你的 `workflow_config_lightgbm_Alpha158.yaml`：
你可以拉长 `workflow_config_lightgbm_Alpha158.yaml` 第 27 行往下的回测阶段的 `end_time`：
例如，将 `test` 或回测周期 `end_time` 顺延调整至 `"2026-03-03"`。
