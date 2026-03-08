#!/bin/bash
# paper_trade_daily_predict.sh
# 每天收盘后运行，自动完成：
#   1. 更新 K 线数据到最新
#   2. 将 workflow_config_paper_trade.yaml 的测试集时间和回测结束时间更新为最新日期
#   3. 训练模型并导出明日应买/应卖/持有的股票清单
#
# 用法：bash paper_trade_daily_predict.sh [--skip-data-update] [--skip-train]
# --skip-data-update  : 跳过第一步数据更新（已有最新数据时使用）
# --skip-train        : 跳过训练步骤，直接从上次 mlruns 导出信号

set -e

# ── 参数解析 ────────────────────────────────────────────────
SKIP_DATA_UPDATE=false
SKIP_TRAIN=false

for arg in "$@"; do
    case $arg in
        --skip-data-update) SKIP_DATA_UPDATE=true ;;
        --skip-train)       SKIP_TRAIN=true ;;
    esac
done

# ── 路径配置 ────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG="$SCRIPT_DIR/workflow_config_paper_trade.yaml"
CONFIG_BACKUP="$SCRIPT_DIR/workflow_config_paper_trade.yaml.bak"
OUTPUT_DIR="$SCRIPT_DIR/paper_trade_output"
HOLDINGS="$OUTPUT_DIR/current_holdings.csv"

QLIB_DATA_DIR="$HOME/.qlib/qlib_data/cn_data"
SOURCE_DIR="$HOME/.qlib/stock_data/source/cn_data"
DAILY_UPDATE_PY="$HOME/Development/qlib/qlib/scripts/data_collector/yahoo/daily_update.py"

# ── 日期计算 ────────────────────────────────────────────────
# 今天（即本次脚本运行日，预测信号对应的是下一个交易日的操作）
TODAY=$(date +%Y-%m-%d)

# 最新已入库数据日期：从 Qlib 日历文件读取
CALENDAR_FILE="$QLIB_DATA_DIR/calendars/day.txt"

echo "============================================"
echo " Qlib 模拟盘 · 每日预测流程"
echo " 运行日期: $TODAY"
echo "============================================"

# ── Step 1: 更新 K 线数据 ───────────────────────────────────
echo ""
echo "=== Step 1: 更新 K 线数据 ==="

if [ "$SKIP_DATA_UPDATE" = true ]; then
    echo "  [跳过] --skip-data-update 已指定，跳过数据更新"
else
    if [ ! -f "$DAILY_UPDATE_PY" ]; then
        echo "  [错误] 未找到 daily_update.py: $DAILY_UPDATE_PY"
        exit 1
    fi

    echo "  正在下载并更新最新 K 线..."
    python3 "$DAILY_UPDATE_PY" \
        --source_dir "$SOURCE_DIR" \
        --qlib_data_dir "$QLIB_DATA_DIR" \
        --region CN

    echo "  [完成] K 线数据已更新"
fi

# ── 读取更新后的最新交易日 ──────────────────────────────────
if [ ! -f "$CALENDAR_FILE" ]; then
    echo "  [错误] 找不到 Qlib 日历文件: $CALENDAR_FILE"
    exit 1
fi

# 从 day.txt 读取最后一个有效日期作为数据终止日
LATEST_DATE=$(tail -n 1 "$CALENDAR_FILE" | tr -d '[:space:]')

if [ -z "$LATEST_DATE" ]; then
    echo "  [错误] 无法从日历文件读取最新日期"
    exit 1
fi

echo ""
echo "  Qlib 日历中最新交易日: $LATEST_DATE"

# ── Step 2: 更新 YAML 配置文件日期 ──────────────────────────
echo ""
echo "=== Step 2: 更新 workflow_config_paper_trade.yaml 日期 ==="

# 备份原始配置
cp "$CONFIG" "$CONFIG_BACKUP"
echo "  已备份配置文件 → $(basename "$CONFIG_BACKUP")"

# 使用 Python 精确替换 YAML 中的日期字段，保留所有注释和格式
python3 - <<PYEOF
import re
from pathlib import Path

config_path = Path("$CONFIG")
latest_date = "$LATEST_DATE"

text = config_path.read_text(encoding="utf-8")
original = text

# ── 更新 data_handler_config 下的 end_time ──────────────────
# 仅替换 data_handler_config 块中的 end_time（不影响 backtest 下的 end_time）
text = re.sub(
    r"(data_handler_config:.*?end_time:\s*)\S+",
    r"\g<1>" + latest_date,
    text,
    count=1,
    flags=re.DOTALL
)

# ── 更新 segments.test 的结束日期（第二个日期） ────────────
# 格式: test: [2023-01-01, 2025-12-31]
text = re.sub(
    r"(test:\s*\[\s*\S+\s*,\s*)(\S+?)(\s*\])",
    r"\g<1>" + latest_date + r"\g<3>",
    text
)

print(f"配置已更新：")
print(f"  data_handler_config.end_time  → {latest_date}")
print(f"  segments.test (结束日)         → {latest_date}")

config_path.write_text(text, encoding="utf-8")
PYEOF

echo ""
echo "  当前配置预览（关键日期字段）："
python3 - <<'PYEOF'
import re
from pathlib import Path

import sys
config_path = Path("$SCRIPT_DIR/workflow_config_paper_trade.yaml".replace("$SCRIPT_DIR", sys.argv[1] if len(sys.argv) > 1 else "."))
PYEOF

# 直接打印关键行，方便目测确认
grep -E "(end_time|test:)" "$CONFIG" | sed 's/^/    /'

# ── Step 3: 训练模型 + 生成预测 ─────────────────────────────
echo ""
echo "=== Step 3: 训练模型 + 生成预测 ==="

cd "$SCRIPT_DIR"

if [ "$SKIP_TRAIN" = true ]; then
    echo "  [跳过] --skip-train 已指定，直接使用上次训练结果"
else
    echo "  正在执行 qrun（训练 LightGBM + 生成预测信号）..."
    echo "  配置文件: $(basename "$CONFIG")"
    echo ""
    qrun workflow_config_paper_trade.yaml
    echo ""
    echo "  [完成] 训练与预测已完成"
fi

# ── Step 4: 导出明日交易信号 ────────────────────────────────
echo ""
echo "=== Step 4: 导出明日交易信号 ==="

mkdir -p "$OUTPUT_DIR"

if [ -f "$HOLDINGS" ]; then
    echo "  检测到持仓文件，将同时计算买入/卖出差异..."
    python3 "$SCRIPT_DIR/export_today_signal.py" \
        --current-holdings "$HOLDINGS" \
        --output-dir "$OUTPUT_DIR"
else
    echo "  [提示] 未找到当前持仓文件 ($HOLDINGS)"
    echo "  [提示] 首次运行后请将 latest_topk.csv 复制为 current_holdings.csv"
    echo "  将仅导出目标持仓列表..."
    python3 "$SCRIPT_DIR/export_today_signal.py" \
        --output-dir "$OUTPUT_DIR"
fi

# ── 打印汇总 ────────────────────────────────────────────────
echo ""
echo "============================================"
echo " ✅ 完成！数据截止日：$LATEST_DATE"
echo "    预测信号（明日操作参考）："
echo ""

if [ -f "$OUTPUT_DIR/latest_topk.csv" ]; then
    echo "  📋 目标持仓 Top-K：$OUTPUT_DIR/latest_topk.csv"
    echo ""
    echo "  --- 应买入股票 (目标持仓) ---"
    cat "$OUTPUT_DIR/latest_topk.csv" | column -t -s',' | head -25
fi

if [ -f "$OUTPUT_DIR/trade_diff.csv" ]; then
    echo ""
    echo "  📊 买卖差异：$OUTPUT_DIR/trade_diff.csv"
    echo ""
    echo "  --- 买入/卖出/持有 清单 ---"
    cat "$OUTPUT_DIR/trade_diff.csv" | column -t -s',' | head -30
fi

echo ""
echo "  📁 全部输出文件目录：$OUTPUT_DIR"
echo "============================================"
