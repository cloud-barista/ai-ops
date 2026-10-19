import importlib.util
import socket
import threading
import unittest
import tempfile
import json
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / 'runtime-app' / 'bert_worker.py'


class WorkerContract(unittest.TestCase):
    def test_local_worker_retains_its_fifteen_minute_limit(self):
        self.assertEqual(self.worker.execution_remaining(None, started_at=100, now=500), 500)
        self.assertEqual(self.worker.execution_remaining(None, started_at=100, now=1001), 0)

    def test_vm_worker_follows_the_extended_guard_instead_of_the_local_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            guard = Path(folder) / 'guard.json'
            guard.write_text(json.dumps(dict(armed=True, intent_sha256='a' * 64, deadline=2200)))
            self.assertEqual(self.worker.execution_remaining(guard, started_at=100, now=1100), 1100)
            guard.write_text(json.dumps(dict(armed=True, intent_sha256='a' * 64, deadline=3400)))
            self.assertEqual(self.worker.execution_remaining(guard, started_at=100, now=1100), 2300)
            self.assertEqual(self.worker.execution_remaining(guard, started_at=100, now=3401), 0)

    def test_invalid_vm_guard_stops_the_worker(self):
        with tempfile.TemporaryDirectory() as folder:
            guard = Path(folder) / 'guard.json'
            for value in [dict(armed=False, deadline=2200), dict(armed=True, deadline=float('nan')), dict(armed=True, deadline=99999)]:
                guard.write_text(json.dumps(value))
                self.assertEqual(self.worker.execution_remaining(guard, started_at=100, now=1100), 0)
            guard.unlink()
            self.assertEqual(self.worker.execution_remaining(guard, started_at=100, now=1100), 0)

    def setUp(self):
        self.assertTrue(SOURCE.is_file(), 'The real model worker has not been implemented')
        spec = importlib.util.spec_from_file_location('bert_worker', SOURCE)
        self.worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.worker)

    def test_pending_inference_connections_survive_a_sixteen_request_burst(self):
        # A small listening backlog caused immediate connection failures in the
        # real 16-client pilot. Keep inference serial, but admit the pending TCP
        # connections until the worker can serve them.
        class Handler(self.worker.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Length', '2')
                self.end_headers()
                self.wfile.write(b'OK')

            def log_message(self, *_):
                pass

        server_type = getattr(self.worker, 'InferenceHTTPServer', self.worker.HTTPServer)
        server = server_type(('127.0.0.1', 0), Handler)
        connections = []
        rejected = []
        thread = None
        try:
            for _ in range(16):
                try:
                    connection = socket.create_connection(server.server_address, timeout=0.1)
                    connections.append(connection)
                except OSError as error:
                    rejected.append(type(error).__name__)
            self.assertEqual(len(connections), 16, f'Pending inference clients rejected: {rejected}')
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            for connection in connections:
                connection.settimeout(3)
                connection.sendall(b'GET /healthz HTTP/1.0\r\n\r\n')
            for connection in connections:
                response = b''
                while True:
                    block = connection.recv(4096)
                    if not block:
                        break
                    response += block
                self.assertTrue(response.startswith(b'HTTP/1.0 200'), response)
                self.assertTrue(response.endswith(b'OK'), response)
        finally:
            for connection in connections:
                connection.close()
            if thread:
                server.shutdown()
                thread.join(timeout=3)
            server.server_close()

    def test_only_approved_model_and_config_are_accepted(self):
        base = ['--cache-dir', str(SOURCE.parent), '--flow-id', 'flow-contract']
        args = self.worker.parse_args(base)
        self.assertEqual(args.revision, '30b0a37ccaaa32f332884b96992754e246e48c5f')
        for flags in [['--model-id', 'other/model'], ['--batch-size', '8'], ['--revision', 'main']]:
            with self.assertRaises(ValueError):
                self.worker.parse_args(base + flags)

    def test_batch_validation_never_rewrites_the_selected_condition(self):
        self.assertEqual(self.worker.validate_texts({'texts': ['an example']}, 1), ['an example'])
        for body in [{'texts': ['a', 'b']}, {'texts': []}, {'texts': [3]}, {'texts': ['x' * 2049]}]:
            with self.assertRaises(ValueError):
                self.worker.validate_texts(body, 1)

    def test_replica_identity_port_and_device_are_bounded(self):
        base=['--cache-dir',str(SOURCE.parent),'--flow-id','flow-test']
        args=self.worker.parse_args(base+['--port','18161','--group-id','group-test','--replica-id','replica-test','--device','cpu'])
        self.assertEqual(args.port,18161)
        for flags in [['--port','8200'],['--device','cuda:1'],['--group-id','foreign']]:
            with self.assertRaises(ValueError):self.worker.parse_args(base+flags)

    def test_prometheus_tracks_real_requests_and_keeps_exposition_safe(self):
        self.assertTrue(hasattr(self.worker, 'RequestMetrics'), 'Prometheus request instrumentation is missing')
        metrics = self.worker.RequestMetrics()
        metrics.observe(0.008, False)
        metrics.observe(0.020, True)
        text = metrics.render({'flow_id': 'flow-test', 'model_id': 'a"b', 'model_revision': 'rev',
                               'batch_size': 1, 'max_length': 64, 'pid': 123, 'launcher_pid': 122},
                              cpu_seconds=1.5, memory_bytes=1024, host_cores=12)
        self.assertIn('# TYPE khu_app_requests_total counter', text)
        self.assertIn('model_id="a\\"b"', text)
        self.assertIn('khu_app_request_duration_seconds_bucket', text)
        self.assertIn('le="+Inf"', text)
        self.assertRegex(text, r'khu_app_requests_total\{[^\n]+\} 2\n')
        self.assertRegex(text, r'khu_app_request_errors_total\{[^\n]+\} 1\n')
        self.assertRegex(text, r'khu_app_request_duration_seconds_count\{[^\n]+\} 2\n')
