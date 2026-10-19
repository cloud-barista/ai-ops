"""Audit adopted inference route against frozen experiments and original model."""
import hashlib
import json
from pathlib import Path
import re

import assess_gpu_shape as shape
from assess_gpu_shape_microbatch_v7 import assess_gpu_shape_microbatch_v7
from benchmark_gpu_shape_microbatch_v4 import (MODEL, SOURCE, canonical, ordered_requests,
    request_blocks, verify_frozen_inputs)
from train_performance import ROOT


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    artifact = ROOT / 'artifacts/row_partition_forest_confirmation_v1'
    confirm = json.loads((artifact / 'results.json').read_text())
    parent = json.loads((ROOT / 'artifacts/row_partition_forest_v1/results.json').read_text())
    assert confirm['gates']['row2']['status'] == parent['gates']['row2']['status'] == 'GO'
    assert all(confirm['gates']['row2']['checks'].values())
    assert parent['gates']['row4']['status'] == 'NO_GO'
    ordered = json.loads((ROOT / 'artifacts/ordered_forest_v1/results.json').read_text())
    assert all(g['status'] == 'NO_GO' for g in ordered['gates'].values())
    for p, expected in confirm['protocol']['hashes'].items():
        path = Path(p) if p.startswith('/') else ROOT / p
        assert sha(path) == expected, p
    assert sha(artifact / 'confirmation_wrapper_executed.py') == confirm['provenance']['confirmation_wrapper_sha256']
    assert sha(ROOT / 'scripts/row_partition_forest_predict.py') == confirm['provenance']['algorithm_sha256']
    assert sha(ROOT / 'scripts/assess_gpu_shape_row_partition.py') == confirm['provenance']['assessor_sha256']
    assert sha(MODEL) == confirm['provenance']['model']
    assert sha(SOURCE) == confirm['provenance']['source']
    adoption = json.loads((artifact / 'ADOPTION.json').read_text())
    assert sha(ROOT / 'scripts/assess_gpu_shape_microbatch_v7.py') == adoption['v7_sha256']
    manifest = verify_frozen_inputs()
    bundle = shape.load_verified_bundle(MODEL, SOURCE)
    worker_before = bundle['model'].named_steps['regressor'].n_jobs
    routes = {}
    for scenario in ['shared_slo', 'copied_slo', 'unique_grids']:
        requests = ordered_requests(request_blocks(scenario, manifest), None)
        outputs = assess_gpu_shape_microbatch_v7(bundle, requests)
        digest = hashlib.sha256(canonical(outputs)).hexdigest()
        assert digest == confirm['scenarios'][scenario]['canonical_sha256']
        routes[scenario] = {'outputs': len(outputs), 'exact_JSON_sha256': digest}
    assert worker_before == bundle['model'].named_steps['regressor'].n_jobs
    assert 'predict' not in bundle['model'].named_steps['regressor'].__dict__
    tests = {}
    expected = {'ordered_forest_tests.log': 11, 'row_partition_tests.log': 11,
                'shape_microbatch_regression_v7.log': 24, 'row_partition_output_tests.log': 2}
    for filename, count in expected.items():
        text = (artifact / filename).read_text()
        match = re.search(r'Ran (\d+) tests', text)
        assert match and int(match.group(1)) == count and text.rstrip().endswith('OK')
        tests[filename] = count
    report = ROOT / 'reports/decision_inference_row_partition_findings_2026_10_05.md'
    text = report.read_text()
    assert 'CPU' in text and '1.136' in text and 'v7' in text
    for name in ['ordered_forest_v1', 'row_partition_forest_v1', 'row_partition_forest_confirmation_v1']:
        directory = ROOT / 'artifacts' / name
        entries = [{'path': str(p.relative_to(directory)), 'bytes': p.stat().st_size, 'sha256': sha(p)}
                   for p in sorted(directory.rglob('*')) if p.is_file() and p.name != 'ARTIFACT_MANIFEST.json']
        (directory / 'ARTIFACT_MANIFEST.json').write_text(json.dumps({'files': entries}, indent=2)+'\n')
        for item in entries:
            p = directory / item['path']
            assert p.stat().st_size == item['bytes'] and sha(p) == item['sha256']
    evidence = {'status': 'VERIFIED_RESEARCH_RESULT', 'adopted_route': 'v7', 'workers': 2,
                'models_sources_frozen_code_unchanged': True,
                'exploratory_and_confirmation_gates': 'GO', 'executed_wrapper_snapshot_verified': True,
                'v7_real_model_routes_equal_confirmed_JSON': routes,
                'tests': tests, 'total_tests': sum(tests.values()),
                'model_sha256': sha(MODEL), 'source_sha256': sha(SOURCE), 'report_sha256': sha(report),
                'verifier_sha256': sha(Path(__file__)),
                'limitation': 'same-host warm offline function; extra CPU time measured; no VM/SLO effect claim'}
    out = ROOT / 'reports/decision_inference_row_partition_completion_audit_2026_10_05.json'
    out.write_text(json.dumps(evidence, indent=2)+'\n')
    print(json.dumps({'status': evidence['status'], 'tests': evidence['total_tests'], 'audit': str(out)}))


if __name__ == '__main__':
    main()
