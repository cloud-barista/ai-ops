"""Fresh paired repetition of the fixed2-worker row partition candidate."""
import argparse
import json
from pathlib import Path
from unittest.mock import patch

import benchmark_row_partition_forest as study
from train_performance import ROOT


def output_root(argv=None):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'artifacts/row_partition_forest_confirmation_v1')
    args, _ = parser.parse_known_args(argv)
    return args.output


def main():
    parent = json.loads((ROOT / 'artifacts/row_partition_forest_v1/results.json').read_text())
    if parent['gates']['row2']['status'] != 'GO':
        raise ValueError('Do not confirm an ineligible exploratory candidate')
    study.PROTOCOL = ROOT / 'reports/decision_inference_row_partition_confirmation_protocol_v1.json'
    study.OUT = ROOT / 'artifacts/row_partition_forest_confirmation_v1'
    study.MODES = ['v6', 'row2']
    original_ordering = study.ordered_requests

    def fresh_ordering(blocks, repetition):
        return original_ordering(blocks, repetition + 100 if repetition is not None else None)

    study.ordered_requests = fresh_ordering
    def retry_fixed_candidate(bundle, requests):
        expected = study.canonical(study.assess('v6', bundle, requests))
        original = study.ordered.RowPartitionForestPredictor.predict_records
        for kind in ['exception', 'bad_count']:
            def fail(engine, active, candidates):
                if len(candidates) > 8:
                    if kind == 'exception':
                        raise MemoryError('injected combined batch limit')
                    return []
                return original(engine, active, candidates)
            with patch.object(study.ordered.RowPartitionForestPredictor, 'predict_records', new=fail):
                if study.canonical(study.assess('row2', bundle, requests)) != expected:
                    raise AssertionError('row2 confirmation retry mismatch')
        return {'combined_exception_retry_exact': True, 'malformed_count_retry_exact': True}
    study.verify_retry = retry_fixed_candidate
    # Child memory workers still use the same fixed2-worker algorithm and raw
    # request shapes; their loading/RSS measurement is unchanged by row order.
    study.main()
    path = output_root() / 'results.json'
    result = json.loads(path.read_text())
    result['provenance']['confirmation_wrapper_sha256'] = study.sha(Path(__file__))
    result['request_shuffle_indices'] = [100, 108]
    result['parent_selection_results_sha256'] = study.sha(ROOT / 'artifacts/row_partition_forest_v1/results.json')
    path.write_text(json.dumps(result, indent=2)+'\n')


if __name__ == '__main__':
    main()
