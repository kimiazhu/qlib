#!/bin/bash
# daily_paper_trade.sh
# 每天收盘后（16:00之后）运行此脚本
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG="$SCRIPT_DIR/workflow_config_paper_trade.yaml"
CONFIG_BACKUP="$SCRIPT_DIR/workflow_config_paper_trade.yaml.bak"

# ── 日期计算 ────────────────────────────────────────────────
# TODAY: 今天（预测目标日）
TODAY=$(date +%Y-%m-%d)

# YESTERDAY: 昨天（最新已入库数据）
YESTERDAY=$(date -v-1d +%Y-%m-%d)

# 验证集: 最近1年（严格不与训练集重叠）
#   VALID = [昨天-1年+1天, 昨天]
VALID_END="$YESTERDAY"
VALID_START=$(date -v-1y +%Y-%m-%d)          # 昨天往前推1年

# 训练集: 从2008年开始，截止到验证集开始的前一天
#   TRAIN = [2008-01-01, VALID_START-1天]
TRAIN_START="2008-01-01"
TRAIN_END=$(date -v-1y -v-1d +%Y-%m-%d)     # VALID_START 的前一天

# 测试集: 最近6个月到今天（只用来生成预测，可与 valid 重叠，无问题）
TEST_START=$(date -v-6m +%Y-%m-%d)
TEST_END="$TODAY"

echo "============================================"
echo " Qlib 模拟盘日常流程 - $TODAY"
echo "============================================"
echo " 训练集: $TRAIN_START ~ $TRAIN_END"
echo " 验证集: $VALID_START ~ $VALID_END"
echo " 测试集: $TEST_START ~ $TEST_END"
echo "--------------------------------------------"

# ── Step 1: 更新股票数据 ─────────────────────────────────────
echo ""
echo "=== Step 1: 更新股票数据 ==="
#python3 ~/Development/qlib/qlib/scripts/data_collector/yahoo/daily_update.py \
#  --qlib_dir ~/.qlib/qlib_data/cn_data \
#  --max_workers 4

python3 ~/Development/qlib/qlib/scripts/data_collector/yahoo/daily_update.py --source_dir ~/.qlib/stock_data/source/cn_data --qlib_data_dir ~/.qlib/qlib_data/cn_data --region CN

# ── Step 2: 动态更新 YAML 配置日期 ──────────────────────────
echo ""
echo "=== Step 2: 更新配置文件日期 ==="
cp "$CONFIG" "$CONFIG_BACKUP"

# 用 python3 做 yaml 精确替换，避免 sed 的跨平台问题
python3 - <<PYEOF
import yaml, re
from pathlib import Path

config_path = Path("$CONFIG")
text = config_path.read_text()

# 使用正则逐行替换关键日期字段（保留缩进和格式）
replacements = {
    r"(end_time:\s*)[\d-]+":        r"\g<1>$TEST_END",
    r"(fit_end_time:\s*)[\d-]+":    r"\g<1>$TRAIN_END",
    r"(fit_start_time:\s*)[\d-]+":  r"\g<1>$TRAIN_START",
    r"(train:.*?)([\d-]+)(,\s*)([\d-]+)(\])": r"\g<1>$TRAIN_START\g<3>$TRAIN_END\g<5>",
    r"(valid:.*?)([\d-]+)(,\s*)([\d-]+)(\])": r"\g<1>$VALID_START\g<3>$VALID_END\g<5>",
    r"(test:.*?)([\d-]+)(,\s*)([\d-]+)(\])":  r"\g<1>$TEST_START\g<3>$TEST_END\g<5>",
}
for pattern, repl in replacements.items():
    text = re.sub(pattern, repl, text)

# backtest end_time 也更新（在 port_analysis_config 下）
text = re.sub(r"(backtest:.*?start_time:.*?\n.*?end_time:\s*)[\d-]+",
              r"\g<1>$TRAIN_END", text, flags=re.DOTALL)

config_path.write_text(text)
print("配置已更新：")
print(f"  end_time      → $TEST_END")
print(f"  fit_end_time  → $TRAIN_END")
print(f"  train segment → $TRAIN_START ~ $TRAIN_END")
print(f"  valid segment → $VALID_START ~ $VALID_END")
print(f"  test  segment → $TEST_START ~ $TEST_END")
PYEOF

# ── Step 3: 训练模型并生成预测 ──────────────────────────────
echo ""
echo "=== Step 3: 训练模型 + 生成预测 ==="
cd "$SCRIPT_DIR"
qrun workflow_config_paper_trade.yaml

# ── Step 4: 导出今日信号 ─────────────────────────────────────
echo ""
echo "=== Step 4: 导出今日交易信号 ==="
HOLDINGS="$SCRIPT_DIR/paper_trade_output/current_holdings.csv"
if [ -f "$HOLDINGS" ]; then
    python3 "$SCRIPT_DIR/export_today_signal.py" \
      --current-holdings "$HOLDINGS"
else
    echo "  [提示] 未找到持仓文件，跳过差异计算"
    echo "  [提示] 首次运行后请将 latest_topk.csv 复制为 current_holdings.csv"
    python3 "$SCRIPT_DIR/export_today_signal.py"
fi

echo ""
echo "============================================"
echo " 完成！查看输出文件："
echo "  持仓目标: paper_trade_output/latest_topk.csv"
echo "  买卖清单: paper_trade_output/trade_diff.csv"
echo "============================================"
