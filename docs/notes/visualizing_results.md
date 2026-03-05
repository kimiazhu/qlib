# Chat Conversation

Note: _This is purely the output of the chat conversation and does not contain any raw data, codebase snippets, etc. used to generate the output._

### User Input

我现在跑了一次 LightGBM 的一次训练，如何将结果进行可视化？给出步骤告诉我怎么做

*Searched filesystem*

*Listed directory [examples](file:///Users/kc/Development/qlib/qlib/examples) *

*Viewed [workflow_by_code.py](file:///Users/kc/Development/qlib/qlib/examples/workflow_by_code.py) *

*Grep searched codebase*

*Viewed [workflow_config_lightgbm_Alpha158.yaml](file:///Users/kc/Development/qlib/qlib/examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158.yaml) *

### Planner Response

在 Qlib 中运行完 LightGBM 训练（例如通过 `qrun workflow_config_lightgbm_Alpha158.yaml`）后，结果会自动通过 MLflow 被记录下来。通常有两种最主流的方法来进行可视化分析：

### 方法一：使用 MLflow UI（最快捷的网页端仪表盘）

由于 Qlib 默认将所有运行日志、指标（如 IC、Rank IC、Information Ratio）以及保存的模型序列化到运行目录下的 `mlruns/` 文件夹内，您可以直接启动控制台查看。

**步骤：**
1. **打开您的终端 (Terminal)**。
2. **切换到运行训练的所在目录**（在此次 LightGBM 跑测中，应当是 `LightGBM` 目录）：
   ```bash
   cd /Users/kc/Development/qlib/qlib/examples/benchmarks/LightGBM
   ```
3. **启动 MLflow 的 UI 服务**：
   ```bash
   mlflow ui
   ```
4. **浏览结果**：打开浏览器，访问 `http://localhost:5000`（或者 `127.0.0.1:5000`）。在左侧的 Experiments 列表中找到您刚运行完毕的记录，点击即可在网页内查看相关的指标图表和具体运行参数。

---

### 方法二：使用 Jupyter Notebook 画交互式图表（最详细）

如果您希望看到包含了累积收益率、超额收益 (Excess Return) 以及多空分层 (比如 Top-k Dropout) 回测曲线这类非常详尽的交易视角绘图，可以用 Qlib 原生的 `qlib.contrib.report` 分析模块。

**步骤：**
1. 请确保您处于与运行模型相同的 Python 环境中，打开 Jupyter Notebook（建议使用 Notebook，交互式图表展示最佳）。
2. 在任意 Cell 中运行以下代码：

```python
import qlib
from qlib.workflow import R
from qlib.contrib.report import analysis_model, analysis_position

# 1. 初始化 Qlib
# 注意：数据的 provider_uri 需要跟您 run LightGBM yaml 文件中保持一致
qlib.init(provider_uri="~/.qlib/qlib_data/cn_data", region="cn")

# 2. 获取刚才跑完保存的 Recorder 记录器
# 默认会获取最近一次运行的记录。如果您知道准确的 experiment_name 也可以传入
recorder = R.get_recorder() 

# ==========================================
# 3. 信号层可视化：绘制 IC / Rank IC 等因子的预测效用图
# ==========================================
ic_df = recorder.load_object("sig_analysis/ic.pkl")
ic_fig = analysis_model.model_performance_graph(ic_df)
ic_fig.show()

# ==========================================
# 4. 组合层可视化：绘制回测的累计收益率 (Cumulative Return)、风险指标等
# ==========================================
report_normal_df = recorder.load_object("portfolio_analysis/report_normal_1day.pkl")
# 如果您需要查看具体每天的仓位持仓，可以加载 positions_normal_1day.pkl
# positions = recorder.load_object("portfolio_analysis/positions_normal_1day.pkl")

report_fig = analysis_position.report_graph(report_normal_df)
report_fig.show()
```

**提示：** Qlib 底层的图表是基于 `plotly` 构建的。当运行 `show()` 之后，会呈现出精美的可交互式图表。如果您没有使用 Jupyter，把代码写在了普通的 Python 脚本 ([.py](cci:7://file:///Users/kc/Development/qlib/qlib/examples/run_all_model.py:0:0-0:0) 文件) 中运行，执行 `show()` 会自动在您的计算机默认浏览器中弹出一个新页签来展示这些绘制完成的图表。

### User Input

请给出安装和启动Jupyter Notebook 的详细步骤

### Planner Response

为了能够在刚才运行 Qlib 的环境中打开 Jupyter Notebook 并绘制图表，我们需要将 Jupyter 安装到您当前处于激活状态的 Python 环境（也就是您运行 `qrun` 所在的那个虚拟环境或 conda 环境）中。

以下是详细的安装和启动步骤：

### 步骤一：激活您的 Python/Conda 环境
如果您在运行 LightGBM 时使用了特别的虚拟环境（比如 `conda` 环境），请先激活它。如果您使用的是全局环境，可以跳过这一步。
```bash
# 如果您使用的是 conda，假设环境名叫做 qlib_env
conda activate qlib_env
```

### 步骤二：安装 Jupyter Notebook 及其相关依赖
在终端中运行以下命令，使用 `pip` 安装 Jupyter Notebook 以及 Qlib 绘图所依赖的包 (`plotly` 和 `ipykernel`)：
```bash
# 安装 Jupyter 和图表渲染支持库
pip install jupyter notebook plotly kaleido
```
*(注：`plotly` 是 Qlib 使用的交互式绘图库，`kaleido` 可以在有时需要静态图片导出时提供底层支持。)*

将您当前的虚拟环境注册到 Jupyter 中（这样 Jupyter 才能正确找到 Qlib 库）：
```bash
# 这里的 --name 后面可以随便起一个您能认出来的名字，比如 qlib_kernel
python -m ipykernel install --user --name qlib_kernel --display-name "Python (Qlib)"
```

### 步骤三：启动 Jupyter Notebook
在您的终端中，**切换到您想要保存代码和图表的目录**（例如 Qlib 的 examples 目录）：
```bash
cd /Users/kc/Development/qlib/qlib/examples/
```

然后启动 Jupyter：
```bash
jupyter notebook
```

### 步骤四：在浏览器中编写代码
1. 运行上一条启动命令后，您的默认浏览器（如 Chrome 或 Safari）会自动打开一个网页，地址通常是 `http://localhost:8888`。
2. 在网页右上角，点击 **"New" (新建)**，然后在下拉菜单中选择刚才我们创建的环境名称：**"Python (Qlib)"**。
3. 这时会打开一个新的代码笔记本 (Notebook) 页面。
4. 在里面的代码框 (Cell) 中，粘贴之前提供给您的可视化代码：

```python
import pandas as pd
import qlib
from qlib.workflow import R
from qlib.contrib.report import analysis_model, analysis_position

# 1. 初始化 Qlib (路径取决于您之前的数据存放位置)
qlib.init(provider_uri="~/.qlib/qlib_data/cn_data", region="cn")

# 2. 指定 mlruns 目录
mlruns_path = "/Users/kc/Development/qlib/qlib/examples/benchmarks/LightGBM/mlruns"
R.set_uri("file:" + mlruns_path)

# 3. 获取刚才跑完保存的 Recorder 记录器
recorder_id = "5494e6552cb641e18f152c8c76c95130"
recorder = R.get_recorder(recorder_id=recorder_id, experiment_name="workflow")

# ==================== 修复后的信号层分析 ====================
# 加载预测分数和真实标签
pred_df = recorder.load_object("pred.pkl")
label_df = recorder.load_object("label.pkl")
label_df.columns = ["label"] # 将标签列名统一

# 将预测分数和真实标签拼接到同一个 DataFrame 作为输入
pred_label = pd.concat([pred_df, label_df], axis=1, sort=True).reindex(pred_df.index)

# 使用完整的 pred_label 进行多维度模型表现画图
# 该函数会自动画出: 多空分组收益(Group Return), 信息系数(IC), Rank IC分布, 换手率等一整套图
analysis_model.model_performance_graph(pred_label)
# ========================================================

# 4. 组合层可视化 (回测分析)
report_normal_df = recorder.load_object("portfolio_analysis/report_normal_1day.pkl")
report_fig = analysis_position.report_graph(report_normal_df)
#report_fig.show()
```

5. 点击上方工具栏的 **"Run" (运行)** 按钮，或者按键盘快捷键 `Shift + Enter` 运行这段代码，精美的交互式回测图表就会直接显示在代码下方了！
