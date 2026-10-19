import http.client
import importlib.util
import socket
import threading
import time
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / 'runtime-app' / 'bert_worker.py'


class WorkerConnections(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('connection_worker', SOURCE)
        self.worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.worker)

    def start(self, handler):
        server = self.worker.InferenceHTTPServer(('127.0.0.1', 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(lambda: thread.join(timeout=3))
        self.addCleanup(server.shutdown)
        return server

    def test_repeated_requests_reuse_the_same_worker_connection(self):
        addresses = []
        base = getattr(self.worker, 'InferenceRequestHandler', self.worker.BaseHTTPRequestHandler)

        class Handler(base):
            def do_GET(self):
                addresses.append(self.client_address)
                self.send_response(200)
                self.send_header('Content-Length', '2')
                self.end_headers()
                self.wfile.write(b'OK')

            def log_message(self, *_):
                pass

        server = self.start(Handler)
        connection = http.client.HTTPConnection(*server.server_address, timeout=1)
        self.addCleanup(connection.close)
        for _ in range(20):
            connection.request('GET', '/healthz')
            response = connection.getresponse()
            self.assertEqual(response.read(), b'OK')
        self.assertEqual(len(set(addresses)), 1, 'Worker closes TCP after each response')

    def test_idle_keep_alive_connection_does_not_block_another_client(self):
        class Handler(self.worker.BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Length', '2')
                self.end_headers()
                self.wfile.write(b'OK')

            def log_message(self, *_):
                pass

        server = self.start(Handler)
        first = http.client.HTTPConnection(*server.server_address, timeout=1)
        second = http.client.HTTPConnection(*server.server_address, timeout=0.3)
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        first.request('GET', '/healthz')
        self.assertEqual(first.getresponse().read(), b'OK')
        second.request('GET', '/healthz')
        try:
            self.assertEqual(second.getresponse().read(), b'OK')
        except (TimeoutError, OSError) as error:
            self.fail(f'Idle persistent connection blocked another client: {error}')

    def test_model_calls_remain_serial_with_multiple_http_clients(self):
        active = maximum = 0
        gate = threading.Lock()
        observed = []
        model_threads = set()

        def inference():
            nonlocal active, maximum
            with gate:
                active += 1
                maximum = max(maximum, active)
                model_threads.add(threading.get_ident())
            time.sleep(0.01)
            with gate:
                active -= 1
            return b'OK'

        worker = self.worker

        class Handler(worker.BaseHTTPRequestHandler):
            def do_GET(self):
                body = self.server.run_inference(inference)
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_):
                pass

        server = self.start(Handler)
        self.assertTrue(callable(getattr(server, 'run_inference', None)), 'Serial inference admission is missing')

        def client():
            try:
                connection = http.client.HTTPConnection(*server.server_address, timeout=2)
                connection.request('GET', '/infer-test')
                observed.append(connection.getresponse().read())
                connection.close()
            except OSError as error:
                observed.append(type(error).__name__)

        threads = [threading.Thread(target=client) for _ in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)
        self.assertEqual(observed, [b'OK'] * 12)
        self.assertEqual(maximum, 1, 'Persistent connections changed model concurrency')
        self.assertEqual(len(model_threads), 1, 'Model work moved between HTTP threads and lost per-thread runtime settings')

    def test_disconnected_json_response_is_not_retried_on_closed_socket(self):
        base = getattr(self.worker, 'InferenceRequestHandler', None)
        self.assertIsNotNone(base, 'Production JSON response handler is missing')
        handler = object.__new__(base)
        handler.send_response = lambda _: None
        handler.send_header = lambda *_: None
        handler.end_headers = lambda: None

        class ClosedWriter:
            def write(self, _):
                raise BrokenPipeError('peer closed')

        handler.wfile = ClosedWriter()
        handler.close_connection = False
        self.assertFalse(handler.send_json(200, {'ok': True}))
        self.assertTrue(handler.close_connection)

    def test_body_bearing_get_cannot_poison_next_persistent_request(self):
        base = self.worker.InferenceRequestHandler

        class Handler(base):
            def do_GET(self):
                self.send_json(200, {'ok': True})

        server = self.start(Handler)
        connection = http.client.HTTPConnection(*server.server_address, timeout=1)
        self.addCleanup(connection.close)
        connection.request('GET', '/metrics', body=b'{}')
        first = connection.getresponse()
        first.read()
        self.assertTrue(first.will_close, 'Unread GET body was retained on a reusable connection')
        connection.request('GET', '/healthz')
        second = connection.getresponse()
        self.assertEqual(second.status, 200)
        second.read()

    def test_invalid_binding_reports_original_error_without_partial_executor_failure(self):
        handler = self.worker.InferenceRequestHandler
        with self.assertRaises(OverflowError):
            self.worker.InferenceHTTPServer(('127.0.0.1', 70000), handler)


if __name__ == '__main__':
    unittest.main()
