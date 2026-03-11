import pandas as pd
import numpy as np
import pickle
from pathlib import Path
import sys

def load_latest_predictions(mlruns_path, experiment_id=None):
    """Load the latest pred.pkl from mlruns."""
    mlruns_path = Path(mlruns_path)
    if experiment_id is None:
        # Find the latest experiment (assuming numeric folder names)
        exp_folders = [f for f in mlruns_path.iterdir() if f.is_dir() and f.name.isdigit()]
        if not exp_folders:
            raise ValueError("No experiment folders found in mlruns")
        experiment_id = max(exp_folders, key=lambda f: f.stat().st_mtime).name
    
    exp_path = mlruns_path / str(experiment_id)
    # Find the latest run in the experiment
    run_folders = [f for f in exp_path.iterdir() if f.is_dir() and (f / 'artifacts' / 'pred.pkl').exists()]
    if not run_folders:
        raise ValueError(f"No runs with pred.pkl found in experiment {experiment_id}")
    
    latest_run = max(run_folders, key=lambda f: f.stat().st_mtime)
    pred_path = latest_run / 'artifacts' / 'pred.pkl'
    pos_path = latest_run / 'artifacts' / 'portfolio_analysis' / 'positions_normal_1day.pkl'
    
    print(f"Loading predictions from: {pred_path}")
    pred = pd.read_pickle(pred_path)
    
    pos = None
    if pos_path.exists():
        print(f"Loading positions from: {pos_path}")
        pos = pd.read_pickle(pos_path)
    else:
        print(f"Warning: Positions not found at {pos_path}")
        
    return pred, pos

def generate_report(pred, pos, topk=20, output_dir='reports'):
    """Generate the stock selection report with daily tracking."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 1. Get latest scores
    latest_date = pred.index.get_level_values('datetime').max()
    date_str = latest_date.strftime('%Y-%m-%d')
    print(f"Generating report for latest signal date: {latest_date}")
    
    latest_pred = pred.xs(latest_date, level='datetime').sort_values('score', ascending=False)
    recommended_holdings = latest_pred.iloc[:topk]
    
    # 2. Get current holdings and amounts from Qlib Position object
    current_holdings_list = []
    current_amounts = {}
    if pos is not None:
        last_pos_date = max(pos.keys())
        print(f"Current holdings from date: {last_pos_date}")
        p_obj = pos[last_pos_date]
        current_holdings_list = p_obj.get_stock_list()
        # Extract amounts if possible (Qlib Position usually stores this)
        try:
            current_amounts = {s: p_obj.get_stock_amount(s) for s in current_holdings_list}
        except AttributeError:
            print("Warning: Could not get stock amounts from position object.")
    
    # 3. Calculate Scores for Current Holdings
    current_holdings_scores = latest_pred.loc[latest_pred.index.intersection(current_holdings_list)]
    
    # 4. Identify Buy/Sell
    recommended_list = recommended_holdings.index.tolist()
    buy_list = [s for s in recommended_list if s not in current_holdings_list]
    sell_list = [s for s in current_holdings_list if s not in recommended_list]
    
    # 5. Construct Final Dataframe for CSV
    max_len = max(len(current_holdings_list), len(recommended_list), len(buy_list), len(sell_list))
    
    report_data = {
        'current_holding': current_holdings_list + [None] * (max_len - len(current_holdings_list)),
        'current_amount': [current_amounts.get(s, 0) for s in current_holdings_list] + [None] * (max_len - len(current_holdings_list)),
        'current_score': [current_holdings_scores.get(s, [np.nan])[0] if s in current_holdings_scores.index else np.nan for s in current_holdings_list] + [None] * (max_len - len(current_holdings_list)),
        'recommended_holding': recommended_list + [None] * (max_len - len(recommended_list)),
        'recommended_score': recommended_holdings['score'].tolist() + [None] * (max_len - len(recommended_list)),
        'buy_stock': buy_list + [None] * (max_len - len(buy_list)),
        'sell_stock': sell_list + [None] * (max_len - len(sell_list))
    }
    
    df_report = pd.DataFrame(report_data)
    
    # Save both a "latest" report and a dated report
    latest_file = output_dir / 'latest_portfolio_report.csv'
    dated_file = output_dir / f'report_{date_str}.csv'
    
    df_report.to_csv(latest_file, index=False, encoding='utf-8-sig')
    df_report.to_csv(dated_file, index=False, encoding='utf-8-sig')
    
    print(f"Report saved to {latest_file} and {dated_file}")
    return df_report

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--mlruns_path", type=str, default='/Users/kc/Development/qlib/qlib/examples/benchmarks/LightGBM/mlruns')
    parser.add_argument("--output_dir", type=str, default='/Users/kc/Development/qlib/qlib/scripts/portfolio_selection/reports')
    args = parser.parse_args()

    try:
        pred, pos = load_latest_predictions(args.mlruns_path)
        generate_report(pred, pos, output_dir=args.output_dir)
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
