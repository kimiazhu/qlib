from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
import qlib
import yaml
from qlib.workflow import R
from qlib.workflow.recorder import LoadObjectError, Recorder


DEFAULT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = DEFAULT_DIR / "workflow_config_paper_trade.yaml"
DEFAULT_MLRUNS = DEFAULT_DIR / "mlruns"
DEFAULT_OUTPUT_DIR = DEFAULT_DIR / "paper_trade_output"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export the latest prediction ranking from the newest finished Qlib run."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="Workflow YAML used for qrun.")
    parser.add_argument("--mlruns", type=Path, default=DEFAULT_MLRUNS, help="MLflow tracking directory.")
    parser.add_argument(
        "--experiment-name",
        default="workflow",
        help="Experiment name inside mlruns. Defaults to the standard qrun experiment name.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory where ranking/topk/trade files will be written.",
    )
    parser.add_argument(
        "--topk",
        type=int,
        default=None,
        help="Override topk from the workflow config. Defaults to the strategy topk in YAML.",
    )
    parser.add_argument(
        "--current-holdings",
        type=Path,
        default=None,
        help="Optional CSV of current holdings. Used to export buy/sell/keep diffs.",
    )
    return parser.parse_args()


def load_workflow_config(config_path: Path) -> dict:
    with config_path.expanduser().open("r", encoding="utf-8") as file_obj:
        return yaml.safe_load(file_obj)


def normalize_provider_uri(provider_uri: str) -> str:
    return str(Path(provider_uri).expanduser())


def find_latest_finished_recorder(experiment_name: str):
    exp = R.get_exp(experiment_name=experiment_name)
    recorders = exp.list_recorders(rtype="list", status=Recorder.STATUS_FI)
    for recorder in recorders:
        try:
            recorder.load_object("pred.pkl")
        except (LoadObjectError, FileNotFoundError):
            continue
        return recorder
    raise RuntimeError(f"No finished recorder with pred.pkl found in experiment '{experiment_name}'.")


def extract_latest_scores(pred_df: pd.DataFrame | pd.Series) -> tuple[pd.Timestamp, pd.DataFrame]:
    if isinstance(pred_df, pd.Series):
        pred_df = pred_df.to_frame("score")

    if "score" not in pred_df.columns:
        pred_df = pred_df.rename(columns={pred_df.columns[0]: "score"})

    latest_dt = pd.Timestamp(pred_df.index.get_level_values("datetime").max())
    latest_scores = pred_df.xs(latest_dt, level="datetime").sort_values("score", ascending=False)
    latest_scores.index.name = "instrument"
    latest_scores = latest_scores.reset_index()
    latest_scores.insert(0, "rank", range(1, len(latest_scores) + 1))
    latest_scores.insert(0, "date", latest_dt.strftime("%Y-%m-%d"))
    return latest_dt, latest_scores


def load_current_holdings(holdings_path: Path) -> pd.Index:
    holdings_df = pd.read_csv(holdings_path.expanduser())
    if "instrument" in holdings_df.columns:
        series = holdings_df["instrument"]
    elif "stock_id" in holdings_df.columns:
        series = holdings_df["stock_id"]
    else:
        series = holdings_df.iloc[:, 0]
    return pd.Index(series.dropna().astype(str).str.strip().unique(), name="instrument")


def export_trade_diff(topk_df: pd.DataFrame, holdings_path: Path, output_dir: Path) -> Path:
    current_holdings = load_current_holdings(holdings_path)
    target_holdings = pd.Index(topk_df["instrument"].astype(str), name="instrument")

    buy_list = sorted(set(target_holdings) - set(current_holdings))
    sell_list = sorted(set(current_holdings) - set(target_holdings))
    keep_list = sorted(set(current_holdings) & set(target_holdings))

    max_len = max(len(buy_list), len(sell_list), len(keep_list), 1)
    trade_df = pd.DataFrame(
        {
            "buy": buy_list + [None] * (max_len - len(buy_list)),
            "sell": sell_list + [None] * (max_len - len(sell_list)),
            "keep": keep_list + [None] * (max_len - len(keep_list)),
        }
    )
    output_path = output_dir / "trade_diff.csv"
    trade_df.to_csv(output_path, index=False)
    return output_path


def main() -> None:
    args = parse_args()
    workflow_config = load_workflow_config(args.config)

    provider_uri = normalize_provider_uri(workflow_config["qlib_init"]["provider_uri"])
    region = workflow_config["qlib_init"]["region"]
    strategy_topk = workflow_config["port_analysis_config"]["strategy"]["kwargs"]["topk"]
    topk = args.topk or strategy_topk

    qlib.init(provider_uri=provider_uri, region=region)
    R.set_uri(f"file:{args.mlruns.expanduser().resolve()}")

    recorder = find_latest_finished_recorder(args.experiment_name)
    pred_df = recorder.load_object("pred.pkl")
    latest_dt, ranking_df = extract_latest_scores(pred_df)

    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    ranking_path = output_dir / "latest_ranking.csv"
    topk_path = output_dir / "latest_topk.csv"
    ranking_df.to_csv(ranking_path, index=False)
    ranking_df.head(topk).to_csv(topk_path, index=False)

    print(f"recorder_id={recorder.id}")
    print(f"latest_date={latest_dt.strftime('%Y-%m-%d')}")
    print(f"ranking_csv={ranking_path}")
    print(f"topk_csv={topk_path}")

    if args.current_holdings is not None:
        trade_diff_path = export_trade_diff(ranking_df.head(topk), args.current_holdings, output_dir)
        print(f"trade_diff_csv={trade_diff_path}")


if __name__ == "__main__":
    main()
