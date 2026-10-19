"""Retrospectively score fixed requirement-signal support gates on frozen folds.

This script never fits or loads a model. The held-out predictions were made by
fold-specific donor models, whereas the deployable research bundles were fitted
on all nine main-campaign models. Registration-time parameter availability
cannot be established from this historical snapshot.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'data/audit_v1/measurements.parquet'
VRAM_FORECASTS = ROOT / 'artifacts/vram_conditional_v1/forecasts.csv'
LOAD_PREDICTIONS = ROOT / 'artifacts/load_support_v1/predictions.csv'
LOAD_SPLITS = ROOT / 'artifacts/load_support_v1/split_manifest.json'
VRAM_PUBLISHED = ROOT / 'artifacts/vram_conditional_v1/supported_loomo_summary.csv'
VRAM_FOLD_AGGREGATE = ROOT / 'artifacts/vram_conditional_v1/aggregate_metrics.csv'
LOAD_PUBLISHED = ROOT / 'artifacts/load_support_v1/aggregate_metrics.csv'
OUTPUT = ROOT / 'artifacts/requirement_signal_gate_audit_v1'
SOURCE_SHA256 = 'b7bba7963e8a309b6a8502a1d98eefb150476b1381ec016beeb68cbf63fae906'
CAMPAIGNS = ('khu-hf-local-exhaustive', 'khu-hf-local-novel-matrix',
             'khu-hf-local-space-filling')
MAIN = CAMPAIGNS[0]
PROFILE = {
    'host': 'icns', 'device': 'cuda',
    'device_name': 'NVIDIA RTX PRO 5000 Blackwell',
    'gpu_indices_json': '[0]', 'replica_count': 1,
    'cpu_core_limit': 2, 'cpu_threads': 2,
}
VRAM_KEY = ['fold', 'campaign', 'model_id', 'workload_group']
LOAD_KEY = ['fold', 'campaign', 'execution_id']
METHOD_VRAM = 'conditional_h1_q90'
METHOD_LOAD = 'support_aware_size_tree'


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def source_panel(path: Path) -> pd.DataFrame:
    if sha256(path) != SOURCE_SHA256:
        raise ValueError('Unexpected source snapshot SHA-256')
    raw = pd.read_parquet(path)
    selected = raw.campaign.isin(CAMPAIGNS) & raw.success.eq(1) & raw.eligible.eq(True)
    for column, value in PROFILE.items():
        selected &= raw[column].eq(value)
    panel = raw.loc[selected].copy()
    expected = {CAMPAIGNS[0]: 575, CAMPAIGNS[1]: 18, CAMPAIGNS[2]: 44}
    if panel.groupby('campaign').size().to_dict() != expected:
        raise ValueError('Selected profile or campaign rows changed')
    if panel.duplicated(['campaign', 'execution_id']).any() or panel.duplicated(
            ['campaign', 'model_id', 'workload_group']).any():
        raise ValueError('Ambiguous source-to-forecast join')
    panel['parameter_gib'] = panel.result_json.map(
        lambda raw_json: int(json.loads(raw_json)['model_parameter_bytes']) / (1024.0 ** 3))
    for column in ('parameter_gib', 'batch_size', 'max_length', 'request_concurrency'):
        if not np.isfinite(panel[column].to_numpy(dtype=float)).all() or (panel[column] <= 0).any():
            raise ValueError('Invalid static source metadata')
    per_model = panel.groupby('model_id').agg(
        sizes=('parameter_gib', 'nunique'), tasks=('workload_task', 'nunique'),
        revisions=('model_revision', 'nunique'))
    if (per_model != 1).any().any():
        raise ValueError('A model has conflicting parameter, task, or revision metadata')
    return panel


def split_map(path: Path, main: pd.DataFrame) -> dict[str, dict]:
    splits = json.loads(path.read_text())
    if len(splits) != 11 or len({part['fold'] for part in splits}) != 11:
        raise ValueError('Unexpected held-model fold inventory')
    all_models = set(main.model_id)
    by_fold = {}
    for part in splits:
        held, donor = set(part['held_model_ids']), set(part['donor_model_ids'])
        if not held or held & donor or held | donor != all_models:
            raise ValueError('Invalid donor/held-model partition')
        if part['train_campaign'] != MAIN:
            raise ValueError('Fold trained on another campaign')
        actual_ids = set(main.loc[main.model_id.isin(donor), 'execution_id'])
        if set(part['train_execution_ids']) != actual_ids:
            raise ValueError('Fold donor execution IDs disagree with source')
        by_fold[part['fold']] = part
    return by_fold


def opportunity_table(panel: pd.DataFrame, splits: dict[str, dict]) -> pd.DataFrame:
    main = panel.loc[panel.campaign.eq(MAIN)]
    rows = []
    for fold, part in splits.items():
        donor = main.loc[main.model_id.isin(part['donor_model_ids'])]
        donor_model = donor.drop_duplicates('model_id')
        task_sizes = {
            task: (len(group), float(group.parameter_gib.min()),
                   float(group.parameter_gib.max()))
            for task, group in donor_model.groupby('workload_task')
        }
        config_grid = set(map(tuple, donor.loc[donor.workload_task.eq('inference'),
                                               ['batch_size', 'max_length',
                                                'request_concurrency']].to_numpy()))
        if len(config_grid) != 64:
            raise ValueError('Fold donor inference grid is not the expected 64 cells')
        held = panel.loc[panel.model_id.isin(part['held_model_ids'])]
        for row in held.itertuples(index=False):
            count, low, high = task_sizes.get(row.workload_task, (0, math.nan, math.nan))
            task_ok = row.workload_task == 'inference' and count >= 2
            size_ok = bool(task_ok and low <= row.parameter_gib <= high)
            config = (row.batch_size, row.max_length, row.request_concurrency)
            config_ok = bool(config in config_grid)
            load_reason = ('ACCEPT' if size_ok else
                           'unsupported_task' if not task_ok else
                           'parameter_size_outside_same_task_training_range')
            vram_reason = (load_reason if load_reason != 'ACCEPT' else
                           'ACCEPT' if config_ok else 'unobserved_request_configuration')
            rows.append({
                'fold': fold,
                'evaluation_group': 'loomo' if fold.startswith('loomo:') else fold,
                'campaign': row.campaign,
                'model_id': row.model_id,
                'execution_id': row.execution_id,
                'workload_group': row.workload_group,
                'workload_task': row.workload_task,
                'parameter_gib': row.parameter_gib,
                'same_task_donor_models': count,
                'donor_size_min_gib': low,
                'donor_size_max_gib': high,
                'request_config_in_donor_grid': config_ok,
                'load_gate': load_reason,
                'vram_gate': vram_reason,
            })
    result = pd.DataFrame(rows)
    if len(result) != 1276 or result.duplicated(['fold', 'campaign', 'execution_id']).any():
        raise ValueError('Unexpected opportunity count or duplicate execution')
    return result


def _check_prediction_rows(panel: pd.DataFrame, opportunity: pd.DataFrame,
                           vram: pd.DataFrame, load: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    if set(vram.method) != {
            'ridge_point_floor', 'global_ratio_q90', 'task_ratio_q90',
            'conditional_h1_q90', 'conditional_h2_q90', 'donor_selected_q90'}:
        raise ValueError('VRAM method inventory changed')
    if set(load.method) != {
            'donor_model_median', 'size_log_ridge', 'size_only_extra_trees',
            'static_log_ridge', 'support_aware_size_tree'}:
        raise ValueError('Load method inventory changed')
    if vram.duplicated(VRAM_KEY + ['method']).any() or load.duplicated(
            LOAD_KEY + ['method']).any():
        raise ValueError('Duplicate frozen prediction')
    vram = vram.merge(opportunity, on=VRAM_KEY, validate='many_to_one',
                      how='left', indicator=True, suffixes=('', '_source'))
    load = load.merge(opportunity, on=LOAD_KEY, validate='many_to_one',
                      how='left', indicator=True, suffixes=('', '_source'))
    if not vram._merge.eq('both').all() or not load._merge.eq('both').all():
        raise ValueError('Frozen prediction failed source join')
    if not np.allclose(vram.parameter_gib, vram.parameter_gib_source, rtol=0, atol=1e-12):
        raise ValueError('VRAM parameter sizes disagree with source')
    if not np.allclose(load.parameter_gib, load.parameter_gib_source, rtol=0, atol=1e-12):
        raise ValueError('Load parameter sizes disagree with source')
    if not load.workload_task.eq(load.workload_task_source).all():
        raise ValueError('Load task labels disagree with source')
    source_targets = panel[['campaign', 'model_id', 'workload_group',
                            'gpu_memory_reserved_bytes', 'load_seconds']]
    vram = vram.merge(source_targets, on=['campaign', 'model_id', 'workload_group'],
                      validate='many_to_one')
    if not np.allclose(vram.observed_peak_gib,
                       vram.gpu_memory_reserved_bytes / (1024.0 ** 3), rtol=0, atol=1e-12):
        raise ValueError('Frozen VRAM outcomes disagree with source')
    load_targets = panel[['campaign', 'execution_id', 'load_seconds']]
    load = load.merge(load_targets, on=['campaign', 'execution_id'],
                      validate='many_to_one')
    if not np.allclose(load.actual_load_seconds, load.load_seconds, rtol=0, atol=1e-12):
        raise ValueError('Frozen load outcomes disagree with source')
    if not (load.answered == load.workload_task_source.eq('inference')).all():
        raise ValueError('Original load task support differs from reconstructed gate')
    if not (load.outside_same_task_size_range ==
            (load.load_gate == 'parameter_size_outside_same_task_training_range')).all():
        raise ValueError('Original fold-specific size-range flags differ from reconstructed gate')
    if len(vram) != 7242 or len(load) != 6380:
        raise ValueError('Frozen prediction row inventory changed')
    if not vram.groupby(VRAM_KEY).method.nunique().eq(6).all() or not load.groupby(
            LOAD_KEY).method.nunique().eq(5).all():
        raise ValueError('One or more frozen prediction cells lack a method')
    if not vram.loc[vram.method.eq(METHOD_VRAM), 'workload_task'].eq('inference').all():
        raise ValueError('Original VRAM supported another task')
    return vram, load


def _summarize_vram(rows: pd.DataFrame) -> dict:
    if rows.empty:
        return {'answered_models': 0, 'answered_rows': 0, 'coverage_macro': None,
                'pinball90_mib_macro': None, 'mean_excess_mib_macro': None,
                'worst_shortfall_mib': None}
    error = rows.observed_peak_gib - rows.forecast_peak_gib
    scored = rows.assign(covered=(error <= 0).astype(float),
                         pinball=np.where(error >= 0, 0.9 * error, -0.1 * error) * 1024,
                         excess=np.maximum(-error, 0) * 1024,
                         shortfall=np.maximum(error, 0) * 1024)
    model = scored.groupby('model_id').agg(
        coverage=('covered', 'mean'), pinball=('pinball', 'mean'),
        excess=('excess', 'mean'), worst_shortfall=('shortfall', 'max'))
    return {'answered_models': len(model), 'answered_rows': len(rows),
            'coverage_macro': float(model.coverage.mean()),
            'pinball90_mib_macro': float(model.pinball.mean()),
            'mean_excess_mib_macro': float(model.excess.mean()),
            'worst_shortfall_mib': float(model.worst_shortfall.max())}


def _summarize_load(rows: pd.DataFrame) -> dict:
    if rows.empty:
        return {'answered_models': 0, 'answered_rows': 0,
                'male_macro': None, 'mae_seconds_macro': None}
    if rows.predicted_load_seconds.isna().any() or (rows.predicted_load_seconds <= 0).any():
        raise ValueError('Answered load prediction is missing or nonpositive')
    scored = rows.assign(abs_log_error=np.abs(np.log(
        rows.predicted_load_seconds / rows.actual_load_seconds)),
        abs_error_seconds=np.abs(rows.predicted_load_seconds - rows.actual_load_seconds))
    model = scored.groupby('model_id').agg(
        male=('abs_log_error', 'mean'), mae=('abs_error_seconds', 'mean'))
    return {'answered_models': len(model), 'answered_rows': len(rows),
            'male_macro': float(model.male.mean()),
            'mae_seconds_macro': float(model.mae.mean())}


def summarize(opportunity: pd.DataFrame, vram: pd.DataFrame,
              load: pd.DataFrame) -> list[dict]:
    results = []
    for (group, campaign), opp in opportunity.groupby(['evaluation_group', 'campaign']):
        eligible_models = int(opp.model_id.nunique())
        eligible_rows = int(len(opp))
        for method, block in vram.loc[
                vram.evaluation_group.eq(group) & vram.campaign.eq(campaign)
        ].groupby('method'):
            for stage, gated in (
                    ('original_task_gate', block),
                    ('size_and_configuration_gate', block.loc[block.vram_gate.eq('ACCEPT')])):
                results.append({'signal': 'observed_cuda_reserved_peak_gib',
                                'evaluation_group': group, 'campaign': campaign,
                                'method': method, 'stage': stage,
                                'eligible_models': eligible_models,
                                'eligible_rows': eligible_rows,
                                **_summarize_vram(gated)})
        for method, block in load.loc[
                load.evaluation_group.eq(group) & load.campaign.eq(campaign)
        ].groupby('method'):
            for stage, gated in (
                    ('original_task_gate', block.loc[block.answered]),
                    ('size_range_gate', block.loc[block.load_gate.eq('ACCEPT')])):
                results.append({'signal': 'cached_local_load_seconds',
                                'evaluation_group': group, 'campaign': campaign,
                                'method': method, 'stage': stage,
                                'eligible_models': eligible_models,
                                'eligible_rows': eligible_rows,
                                **_summarize_load(gated)})
    if len(results) != 198:  # 3 campaigns x 3 evaluation groups x 11 methods x 2 stages
        raise ValueError('Unexpected summary inventory')
    return results


def compare_published(summary: list[dict]) -> None:
    """Check the reconstructed pre-gate scores against the published files."""
    vram = pd.read_csv(VRAM_PUBLISHED)
    vram_fold = pd.read_csv(VRAM_FOLD_AGGREGATE)
    load = pd.read_csv(LOAD_PUBLISHED)
    for row in summary:
        if row['stage'] != 'original_task_gate':
            continue
        if row['signal'] == 'observed_cuda_reserved_peak_gib':
            if row['evaluation_group'] == 'loomo':
                match = vram.loc[vram.campaign.eq(row['campaign']) &
                                 vram.method.eq(row['method'])]
            else:
                match = vram_fold.loc[
                    vram_fold.fold.eq(row['evaluation_group']) &
                    vram_fold.campaign.eq(row['campaign']) &
                    vram_fold.method.eq(row['method'])]
            fields = {'models': 'answered_models', 'observations': 'answered_rows',
                      'coverage_macro': 'coverage_macro',
                      'pinball90_mib_macro': 'pinball90_mib_macro',
                      'mean_excess_mib_macro': 'mean_excess_mib_macro',
                      'worst_shortfall_mib': 'worst_shortfall_mib'}
        else:
            match = load.loc[load.evaluation_group.eq(row['evaluation_group']) &
                             load.campaign.eq(row['campaign']) & load.method.eq(row['method'])]
            fields = {'answered_models': 'answered_models',
                      'answered_runs': 'answered_rows',
                      'male_macro': 'male_macro',
                      'mae_seconds_macro': 'mae_seconds_macro',
                      'eligible_models': 'eligible_models',
                      'eligible_runs': 'eligible_rows'}
        if len(match) != 1:
            raise ValueError('Published aggregate row missing or duplicated')
        old = match.iloc[0]
        for old_name, new_name in fields.items():
            if not math.isclose(float(old[old_name]), float(row[new_name]),
                                rel_tol=0, abs_tol=1e-10):
                raise ValueError('Pre-gate score differs from published aggregate: '
                                 + old_name)


def audit(source: Path, vram_path: Path, load_path: Path,
          splits_path: Path) -> tuple[pd.DataFrame, dict]:
    panel = source_panel(source)
    splits = split_map(splits_path, panel.loc[panel.campaign.eq(MAIN)])
    opportunity = opportunity_table(panel, splits)
    vram, load = _check_prediction_rows(
        panel, opportunity, pd.read_csv(vram_path), pd.read_csv(load_path))
    summary = summarize(opportunity, vram, load)
    compare_published(summary)
    metrics = {
        'scope': ('Retrospective static-support gate audit on frozen model-held-out '
                  'predictions; not an evaluation of the saved all-main bundles or '
                  'registration-time metadata availability.'),
        'input_sha256': {
            'source': sha256(source), 'vram_forecasts': sha256(vram_path),
            'load_predictions': sha256(load_path), 'load_splits': sha256(splits_path),
            'vram_published': sha256(VRAM_PUBLISHED),
            'vram_fold_aggregate': sha256(VRAM_FOLD_AGGREGATE),
            'load_published': sha256(LOAD_PUBLISHED),
        },
        'profile': PROFILE,
        'method_vram': METHOD_VRAM,
        'method_load': METHOD_LOAD,
        'summary': summary,
    }
    return opportunity, metrics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=SOURCE)
    parser.add_argument('--vram', type=Path, default=VRAM_FORECASTS)
    parser.add_argument('--load', type=Path, default=LOAD_PREDICTIONS)
    parser.add_argument('--splits', type=Path, default=LOAD_SPLITS)
    parser.add_argument('--output', type=Path, default=OUTPUT)
    args = parser.parse_args()
    opportunity, metrics = audit(args.source, args.vram, args.load, args.splits)
    args.output.mkdir(parents=True, exist_ok=True)
    opportunity.to_csv(args.output / 'gate_decisions.csv', index=False)
    (args.output / 'metrics.json').write_text(
        json.dumps(metrics, indent=2, allow_nan=False) + '\n')
    print('Wrote', args.output / 'metrics.json')
    print('Wrote', args.output / 'gate_decisions.csv')


if __name__ == '__main__':
    main()
