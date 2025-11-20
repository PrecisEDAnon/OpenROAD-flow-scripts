import os
import json
import re
import glob
import sys

def get_sdc_target_clock(results_dir):
    # Try specific SDC files in order of preference
    candidates = ['6_final.sdc', 'updated_clks.sdc', '5_route.sdc', '1_synth.sdc', '2_floorplan.sdc']
    
    # Also generic search
    sdc_files = []
    for c in candidates:
        path = os.path.join(results_dir, c)
        if os.path.exists(path):
            sdc_files.append(path)
    
    # If no candidates found, search all .sdc
    if not sdc_files:
        sdc_files = glob.glob(os.path.join(results_dir, '*.sdc'))
        
    if not sdc_files:
        return None

    # Pick the first one
    target_sdc = sdc_files[0]
    
    try:
        with open(target_sdc, 'r') as f:
            for line in f:
                # Look for create_clock
                if 'create_clock' in line:
                    # regex for -period <val>
                    match = re.search(r'-period\s+([0-9\.]+)', line)
                    if match:
                        return float(match.group(1))
    except Exception:
        pass
    return None

def get_metrics_from_run(run_dir):
    # run_dir: .../logs/platform/design/variant
    parts = run_dir.strip('/').split('/')
    try:
        variant = parts[-1]
        design = parts[-2]
        platform = parts[-3]
        source = parts[-5]
    except IndexError:
        return None

    # Paths
    reports_dir = run_dir.replace('/logs/', '/reports/')
    results_dir = run_dir.replace('/logs/', '/results/')
    
    metrics = {
        'Source': source,
        'Design': design,
        'Platform': platform,
        'Variant': variant,
        'Count': 'N/A',
        'Area': 'N/A',
        'Power': 'N/A',
        'Routed_WL': 'N/A',
        'Eff_Clock': 'N/A',
        'Target_Clk': 'N/A',
        'Slack': 'N/A',
        'Report_Eff_Clock': 'N/A' # For debug/assertion
    }

    # 1. Read 6_report.json
    json_path = os.path.join(run_dir, '6_report.json')
    if os.path.exists(json_path):
        try:
            with open(json_path, 'r') as f:
                data = json.load(f)
                metrics['Count'] = data.get('finish__design__instance__count', 'N/A')
                metrics['Area'] = data.get('finish__design__instance__area', 'N/A')
                metrics['Power'] = data.get('finish__power__total', 'N/A')
                metrics['Slack'] = data.get('finish__timing__setup__ws', 'N/A')
        except Exception:
            pass

    # 2. Read 5_2_route.json / 5_3_route.json for Routed WL
    route_json = os.path.join(run_dir, '5_2_route.json')
    if not os.path.exists(route_json):
        route_json = os.path.join(run_dir, '5_3_route.json')
    
    if os.path.exists(route_json):
        try:
            with open(route_json, 'r') as f:
                data = json.load(f)
                max_iter = -1
                wl_val = 'N/A'
                for key, value in data.items():
                    if 'detailedroute__route__wirelength__iter:' in key:
                        try:
                            it = int(key.split(':')[-1])
                            if it > max_iter:
                                max_iter = it
                                wl_val = value
                        except ValueError:
                            continue
                metrics['Routed_WL'] = wl_val
        except Exception:
            pass

    # 4. Get Target Clock from SDC
    tgt = get_sdc_target_clock(results_dir)
    if tgt is not None:
        metrics['Target_Clk'] = tgt

    # 5. Calculate Effective Clock
    # Eff = Target - Slack
    if metrics['Target_Clk'] != 'N/A' and metrics['Slack'] != 'N/A':
        try:
            metrics['Eff_Clock'] = float(metrics['Target_Clk']) - float(metrics['Slack'])
            metrics['Eff_Clock'] = round(metrics['Eff_Clock'], 4)
        except ValueError:
            pass

    # 6. Comparison with 6_finish.rpt derived clock (Legacy Check)
    finish_rpt = os.path.join(reports_dir, '6_finish.rpt')
    if os.path.exists(finish_rpt):
        try:
            with open(finish_rpt, 'r') as f:
                content = f.read()
                # clk period_min = 667.19 fmax = ...
                p_match = re.search(r'clk period_min\s*=\s*([\d\.]+)', content)
                
                if p_match:
                    # The report explicitly states the min period (effective clock)
                    metrics['Report_Eff_Clock'] = float(p_match.group(1))
        except Exception:
            pass

    # Assertion / Warning
    if metrics['Eff_Clock'] != 'N/A' and metrics['Report_Eff_Clock'] != 'N/A':
        diff = abs(metrics['Eff_Clock'] - metrics['Report_Eff_Clock'])
        # Avoid division by zero
        if metrics['Eff_Clock'] != 0:
            pct = diff / abs(metrics['Eff_Clock'])
            if pct > 0.01:
                metrics['Notes'] = f"Eff_Clock Mismatch! Calc: {metrics['Eff_Clock']}, Rpt: {metrics['Report_Eff_Clock']}"
    
    if design == 'aes' and platform == 'asap7' and variant == 'base':
        print(f"DEBUG: {metrics}", file=sys.stderr)

    return metrics

def main():
    search_patterns = [
        'flow/backup_*/logs/*/*/*',
        'flow/logs/*/*/*'
    ]
    run_dirs = []
    for pattern in search_patterns:
        run_dirs.extend(glob.glob(pattern))
    run_dirs = [d for d in run_dirs if os.path.isdir(d)]
    
    all_metrics = []
    for d in run_dirs:
        m = get_metrics_from_run(d)
        if m:
            all_metrics.append(m)
    
    all_metrics.sort(key=lambda x: (x['Source'], x['Design'], x['Variant'], x['Platform']))
    
    headers = ['Source', 'Design', 'Platform', 'Variant', 'Count', 'Area', 'Power', 'Routed_WL', 'Eff_Clock', 'Target_Clk', 'Slack', 'Notes']
    widths = {h: len(h) for h in headers}
    for row in all_metrics:
        for h in headers:
            val = str(row.get(h, 'N/A'))
            widths[h] = max(widths[h], len(val))
            
    def format_row(row_data):
        return "| " + " | ".join(f"{str(row_data.get(h, 'N/A')):<{widths[h]}}" for h in headers) + " |"
    
    print("| " + " | ".join(f"{h:<{widths[h]}}" for h in headers) + " |")
    print("| " + " | ".join("-" * widths[h] for h in headers) + " |")
    for row in all_metrics:
        print(format_row(row))

if __name__ == "__main__":
    main()