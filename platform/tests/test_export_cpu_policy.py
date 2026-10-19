import hashlib
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1] / 'export_cpu_policy.py'
if MODULE.exists():
    spec = importlib.util.spec_from_file_location('export_cpu_policy', MODULE)
    exporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(exporter)
else:
    exporter = None

RUNS = [
    ('khu-m6a-2xl-cpu-baseline-v10-20260922', 'm6a.2xlarge', 8),
    ('khu-m6a-2xl-cpu-baseline-v10b-20260922', 'm6a.2xlarge', 8),
    ('khu-m6a-2xl-cpu-baseline-v10c-20260922', 'm6a.2xlarge', 8),
    ('khu-m6a-large-cpu-baseline-v11-20260922', 'm6a.large', 2),
    ('khu-m6a-large-cpu-baseline-v11b-20260922', 'm6a.large', 2),
    ('khu-m6a-large-cpu-baseline-v11c-20260922', 'm6a.large', 2),
]


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sources = []
        self.original_hashes = {}
        for index, (run_id, instance, cores) in enumerate(RUNS):
            db = self.root / run_id / 'executions.sqlite3'
            db.parent.mkdir()
            con = sqlite3.connect(db)
            con.execute('CREATE TABLE collection_runs(run_id TEXT,status TEXT)')
            con.execute('INSERT INTO collection_runs VALUES (?,?)', (run_id, 'complete'))
            con.execute('''CREATE TABLE execution_measurements (
                run_id TEXT,profile_name TEXT,model_id TEXT,model_revision TEXT,
                batch_size INTEGER,max_length INTEGER,request_concurrency INTEGER,
                replica_count INTEGER,load_condition TEXT,target_throughput_rps REAL,
                latency_slo_ms REAL,dataset TEXT,repeats INTEGER,device TEXT,
                cpu_core_limit INTEGER,cpu_threads INTEGER,cpu_affinity_json TEXT,
                success INTEGER,examples_per_second REAL,latency_p95_ms REAL,result_json TEXT)''')
            con.execute('INSERT INTO execution_measurements VALUES (' + ','.join('?' for _ in range(21)) + ')', (
                run_id, 'bert-tiny-latency', 'model/tiny', 'revision1', 1, 64, 1, 1,
                'nominal', 0, 0, 'operations', 12, 'cpu', cores, cores, json.dumps(list(range(cores))),
                1, 800 if cores == 8 else 100, 5 if cores == 8 else 10,
                json.dumps({'seed': 42, 'model_parameter_count': 10, 'model_parameter_bytes': 40}),
            ))
            con.commit()
            con.close()
            provenance = db.with_name('provenance.json')
            provenance.write_text(json.dumps({
                'collection_status': 'complete', 'host': f'host-{index}', 'python': '3.10.12',
                'configuration': {'KHU_TB_SPEC_ID': 'aws+us-west-2+' + instance,
                                  'KHU_RUN_ID': run_id, 'KHU_TB_IMAGE_ID': 'image1',
                                  'KHU_SAMPLE_INTERVAL_SECONDS': '0.10'},
            }), encoding='utf-8')
            digest = hashlib.sha256(db.read_bytes()).hexdigest()
            self.original_hashes[db] = digest
            self.sources.append({'run_id': run_id, 'database': str(db), 'database_sha256': digest,
                                 'provenance_sha256': hashlib.sha256(provenance.read_bytes()).hexdigest()})
        self.report = self.root / 'report.json'
        self.report.write_text(json.dumps({'source_files': self.sources}), encoding='utf-8')

    def build(self):
        self.assertIsNotNone(exporter, 'CPU export is not implemented')
        return exporter.build_snapshot(self.report)

    def test_exports_independent_sessions_not_request_rows(self):
        data = self.build()
        self.assertEqual(data['verified_source_run_count'], 6)
        self.assertEqual(data['measurement_count'], 6)
        self.assertEqual(len(data['cohorts']), 1)
        opts = {o['cpu_core_limit']: o for o in data['cohorts'][0]['options']}
        self.assertEqual(opts[2]['independent_run_count'], 3)
        self.assertEqual(opts[8]['median_throughput'], 800)
        self.assertEqual(opts[2]['p95_max_ms'], 10)
        for db, digest in self.original_hashes.items():
            self.assertEqual(hashlib.sha256(db.read_bytes()).hexdigest(), digest)

    def test_rejects_changed_database_even_when_sqlite_is_healthy(self):
        db = Path(self.sources[0]['database'])
        with closing(sqlite3.connect(db)) as con, con:
            con.execute('UPDATE execution_measurements SET examples_per_second=9999')
        with self.assertRaisesRegex(ValueError, 'hash'):
            self.build()

    def test_rejects_incomplete_collection(self):
        db = Path(self.sources[0]['database'])
        with closing(sqlite3.connect(db)) as con, con:
            con.execute("UPDATE collection_runs SET status='running'")
        self.sources[0]['database_sha256'] = hashlib.sha256(db.read_bytes()).hexdigest()
        self.report.write_text(json.dumps({'source_files': self.sources}), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'complete'):
            self.build()

    def test_rejects_duplicate_hosts_despite_different_run_ids(self):
        for source in self.sources:
            p = Path(source['database']).with_name('provenance.json')
            obj = json.loads(p.read_text())
            obj['host'] = 'same-host'
            p.write_text(json.dumps(obj), encoding='utf-8')
            source['provenance_sha256'] = hashlib.sha256(p.read_bytes()).hexdigest()
        self.report.write_text(json.dumps({'source_files': self.sources}), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'host'):
            self.build()

    def test_never_uses_report_aggregates_as_substitute_for_real_measurements(self):
        self.report.write_text(json.dumps({'source_files': self.sources, 'cohorts': [
            {'median_throughput': 999999, 'p95_max_ms': 0},
        ]}), encoding='utf-8')
        data = self.build()
        self.assertEqual(max(o['median_throughput'] for o in data['cohorts'][0]['options']), 800)


if __name__ == '__main__':
    unittest.main()
