#!/bin/bash

# Qlib 模拟盘自动化工作流
# 功能：更新数据 -> 运行预测 -> 生成报告

# 1. 环境配置
# // turbo
source $(conda info --base)/etc/profile.d/conda.sh
conda activate qlib

PROJECT_DIR="/Users/kc/Development/qlib/qlib"
CONFIG_PATH="$PROJECT_DIR/examples/benchmarks/LightGBM/workflow_config_paper_trade.yaml"
DATA_DIR="~/.qlib/qlib_data_0309/cn_data"
MLRUNS_DIR="$PROJECT_DIR/examples/benchmarks/LightGBM/mlruns"
REPORTS_DIR="$PROJECT_DIR/scripts/portfolio_selection/reports"

# 2. 更新数据 (Yahoo Finance)
echo "正在更新市场数据..."
python $PROJECT_DIR/scripts/data_collector/yahoo/daily_update.py \
    --qlib_data_dir $DATA_DIR \
    --region CN

# 3. 动态调整配置文件日期
# 获取当前日期和前几天的日期（Qlib 需要一段测试窗口）
CURRENT_DATE=$(date +%Y-%m-%d)
START_TEST_DATE=$(date -v-7d +%Y-%m-%d) 

echo "更新配置文件预测窗口: [$START_TEST_DATE, $CURRENT_DATE]"

# 使用 sed 修改 yaml 中的 test 段 (macOS sed 语法略有不同)
sed -i '' "s/test: \[.*\]/test: [$START_TEST_DATE, $CURRENT_DATE]/" $CONFIG_PATH

# 4. 运行预测
echo "正在运行模型预测..."
qrun $CONFIG_PATH

# 5. 生成选股报告
echo "正在生成每日选股报告..."
python $PROJECT_DIR/scripts/portfolio_selection/select_stocks.py \
    --mlruns_path $MLRUNS_DIR \
    --output_dir $REPORTS_DIR

echo "工作流执行完毕。查看报告请访问: $REPORTS_DIR"
