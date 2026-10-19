"""Portable CPU inference worker, launched and stopped by AppDeploy.

The encoder is the pinned pretrained BERT Tiny from the VM experiments. As in
those benchmarks, the two-label classification head is initialized with a fixed
seed; its outputs demonstrate inference, not sentiment classification accuracy.
"""
import argparse
import json
import math
import os
import re
import sys
import threading
import time
import subprocess
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path

MODEL = 'google/bert_uncased_L-2_H-128_A-2'
REVISION = '30b0a37ccaaa32f332884b96992754e246e48c5f'


def execution_remaining(guard_path, *, started_at, now):
    if guard_path is None:
        return max(0, 900 - (now - started_at))
    try:
        guard = json.loads(guard_path.read_text())
        remaining = float(guard['deadline']) - now
        if (guard.get('armed') is not True
                or not re.fullmatch(r'[a-f0-9]{64}', guard.get('intent_sha256', ''))
                or not math.isfinite(remaining) or remaining > 2700):
            return 0
        return max(0, remaining)
    except (OSError, ValueError, KeyError, TypeError):
        return 0


class InferenceHTTPServer(ThreadingHTTPServer):
    # Persistent connections need independent handlers; only model work is serial.
    request_queue_size = 64
    daemon_threads = True

    def __init__(self, *args, inference_initializer=None, **kwargs):
        self._connections = threading.BoundedSemaphore(64)
        self._inference = threading.Lock()
        super().__init__(*args, **kwargs)
        self._executor = ThreadPoolExecutor(max_workers=1, initializer=inference_initializer)

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(5)
        return request, address

    def process_request(self, request, client_address):
        if not self._connections.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._connections.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._connections.release()

    def run_inference(self, function):
        with self._inference:
            return self._executor.submit(function).result()

    def server_close(self):
        super().server_close()
        executor = getattr(self, '_executor', None)
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)


class InferenceRequestHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def parse_request(self):
        if not super().parse_request():
            return False
        # GET endpoints do not consume a request body. Do not reuse its socket
        # with unread bytes that could become the next HTTP request line.
        if self.command == 'GET' and (self.headers.get('Transfer-Encoding') is not None
                                     or self.headers.get('Content-Length') not in (None, '0')):
            self.close_connection = True
        return True

    def send_json(self, status, value):
        payload = json.dumps(value, allow_nan=False).encode()
        try:
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            if self.close_connection:
                self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(payload)
            return True
        except (ConnectionError, TimeoutError):
            self.close_connection = True
            return False

    def log_message(self, *_):
        pass


class RequestMetrics:
    """Counters and cumulative request histogram; no synthetic traffic."""
    bounds = (0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5)

    def __init__(self):
        self.requests = self.errors = 0
        self.duration_sum = 0.0
        self.buckets = [0] * len(self.bounds)
        self._lock = threading.Lock()

    def observe(self, seconds, failed):
        with self._lock:
            self.requests += 1
            self.errors += int(failed)
            self.duration_sum += seconds
            for i, bound in enumerate(self.bounds):
                self.buckets[i] += int(seconds <= bound)

    def render(self, identity, cpu_seconds, memory_bytes, host_cores, gpu=None):
        with self._lock:
            requests, errors, duration_sum = self.requests, self.errors, self.duration_sum
            buckets = list(self.buckets)
        fields = {'flow_id': identity['flow_id'], 'model_id': identity['model_id'],
                  'model_revision': identity['model_revision'], 'batch_size': identity['batch_size'],
                  'max_length': identity['max_length'], 'worker_pid': identity['pid'],
                  'launcher_pid': identity.get('launcher_pid') or identity['pid']}
        fields.update(group_id=identity.get('group_id',''),replica_id=identity.get('replica_id',''),device=identity.get('device','cpu'))
        if identity.get('worker_started_at') is not None:fields['worker_started_at']=identity['worker_started_at']
        labels = ','.join(k + '=' + json.dumps(str(v), ensure_ascii=False) for k, v in fields.items())
        lines = []

        def value(name, kind, number):
            lines.extend(['# TYPE ' + name + ' ' + kind, name + '{' + labels + '} ' + str(number)])

        value('khu_app_cpu_seconds_total', 'counter', cpu_seconds)
        value('khu_app_memory_rss_bytes', 'gauge', memory_bytes)
        value('khu_app_host_cpu_cores', 'gauge', host_cores)
        if gpu is not None:
            value('khu_app_gpu_utilization_percent','gauge',gpu['utilization_percent'])
            value('khu_app_gpu_memory_used_mib','gauge',gpu['memory_used_mib'])
        value('khu_app_requests_total', 'counter', requests)
        value('khu_app_request_errors_total', 'counter', errors)
        lines.append('# TYPE khu_app_request_duration_seconds histogram')
        for bound, count in zip(self.bounds, buckets):
            lines.append('khu_app_request_duration_seconds_bucket{' + labels + ',le="' + str(bound) + '"} ' + str(count))
        lines.extend(['khu_app_request_duration_seconds_bucket{' + labels + ',le="+Inf"} ' + str(requests),
                      'khu_app_request_duration_seconds_sum{' + labels + '} ' + str(duration_sum),
                      'khu_app_request_duration_seconds_count{' + labels + '} ' + str(requests)])
        return '\n'.join(lines) + '\n'


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--model-id', default=MODEL)
    parser.add_argument('--revision', default=REVISION)
    parser.add_argument('--batch-size', type=int, default=1)
    parser.add_argument('--max-length', type=int, default=64)
    parser.add_argument('--threads', type=int, default=2)
    parser.add_argument('--cache-dir', type=Path, required=True)
    parser.add_argument('--flow-id', default='flow-prefetch')
    parser.add_argument('--prefetch', action='store_true')
    parser.add_argument('--port',type=int,default=18160)
    parser.add_argument('--group-id',default='')
    parser.add_argument('--replica-id',default='')
    parser.add_argument('--device',default='cpu')
    args = parser.parse_args(argv)
    if (args.model_id != MODEL or args.revision != REVISION
            or (args.batch_size, args.max_length) not in [(1, 64), (8, 128)]
            or not 1 <= args.threads <= 4 or not re.fullmatch(r'flow-[\w-]+', args.flow_id)
            or not 18160 <= args.port <= 18169 or args.device not in ['cpu','cuda']
            or bool(args.group_id)!=bool(args.replica_id)
            or (args.group_id and (not re.fullmatch(r'group-[\w-]+',args.group_id) or not re.fullmatch(r'replica-[\w-]+',args.replica_id)))):
        raise ValueError('unsupported_local_model')
    args.cache_dir = args.cache_dir.resolve()
    return args


def validate_texts(body, batch_size):
    texts = body.get('texts') if isinstance(body, dict) else None
    if (not isinstance(texts, list) or not 1 <= len(texts) <= batch_size
            or any(not isinstance(t, str) or not t.strip() or len(t) > 2048 for t in texts)):
        raise ValueError('invalid_inference_batch')
    return texts


def main(argv=None):
    args = parse_args(argv)
    os.environ['TOKENIZERS_PARALLELISM'] = 'false'
    os.environ['HF_HUB_DISABLE_SYMLINKS_WARNING'] = '1'
    os.environ['HF_HOME'] = str(args.cache_dir)
    import psutil
    import torch
    import transformers
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    torch.set_num_threads(args.threads)
    torch.set_num_interop_threads(1)
    torch.manual_seed(42)
    options = dict(revision=args.revision, cache_dir=str(args.cache_dir),
                   local_files_only=not args.prefetch)
    tokenizer = AutoTokenizer.from_pretrained(args.model_id, **options)
    model = AutoModelForSequenceClassification.from_pretrained(args.model_id, num_labels=2, **options)
    if args.device=='cuda' and not torch.cuda.is_available():
        raise RuntimeError('cuda_device_unavailable')
    model.to(args.device)
    model.eval()
    versions = {'python': sys.version.split()[0], 'torch': torch.__version__, 'transformers': transformers.__version__}
    identity = dict(app='khu-bert-tiny', pid=os.getpid(), model_inference=True,
                    model_id=args.model_id, model_revision=args.revision,
                    batch_size=args.batch_size, max_length=args.max_length,
                    flow_id=args.flow_id, runtime_engine='PYTORCH', precision='FP32',
                    threads=args.threads, seed=42, head_kind='benchmark_initialized', versions=versions,
                    group_id=args.group_id,replica_id=args.replica_id,port=args.port,
                    device=args.device,model_device=str(next(model.parameters()).device))
    if args.prefetch:
        args.cache_dir.mkdir(parents=True, exist_ok=True)
        (args.cache_dir / 'ready.json').write_text(json.dumps(identity, indent=2), encoding='utf-8')
        print(json.dumps(dict(event='model_cached', **identity)), flush=True)
        return

    proc = psutil.Process()
    identity['worker_started_at']=proc.create_time()
    parent = proc.parent()
    identity['launcher_pid'] = parent.pid if parent else None
    identity['launcher_executable'] = parent.exe() if parent else None
    completed = 0
    memory_peak = proc.memory_info().rss
    request_metrics = RequestMetrics()
    def gpu_sample():
        if args.device!='cuda':return None
        try:
            out=subprocess.run(['nvidia-smi','--id=0','--query-gpu=utilization.gpu,memory.used','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=2,check=True)
            utilization,memory=map(float,out.stdout.strip().split(','))
            if not 0<=utilization<=100 or memory<0:return None
            return {'utilization_percent':utilization,'memory_used_mib':memory,'source':'nvml-device-0'}
        except (OSError,ValueError,subprocess.SubprocessError):return None

    def infer(texts):
        nonlocal completed, memory_peak
        encoded = tokenizer(texts, padding='max_length', truncation=True,
                            max_length=args.max_length, return_tensors='pt')
        encoded={key:value.to(args.device) for key,value in encoded.items()}
        if args.device=='cuda':torch.cuda.synchronize()
        started = time.perf_counter()
        with torch.inference_mode():
            output = model(**encoded)
        if args.device=='cuda':torch.cuda.synchronize()
        duration_ms = (time.perf_counter() - started) * 1000
        logits = output.logits.detach().cpu()
        probabilities = torch.softmax(logits, dim=-1)
        memory_peak = max(memory_peak, proc.memory_info().rss)
        completed += 1
        return dict(**identity, input_count=len(texts), inference_ms=duration_ms,
                    inference_thread_id=threading.get_ident(), runtime_cpu_threads=torch.get_num_threads(),
                    input_device=str(encoded['input_ids'].device),
                    logits=logits.tolist(), probabilities=probabilities.tolist(),
                    predictions=logits.argmax(dim=-1).tolist(),
                    token_counts=encoded['attention_mask'].sum(dim=1).tolist())

    class Handler(InferenceRequestHandler):
        def do_GET(self):
            if self.path == '/healthz':
                self.send_json(200, dict(status='ready', **identity))
            elif self.path == '/metrics':
                cpu = proc.cpu_times()
                self.send_json(200, dict(**identity, completed=completed,
                                        cpu_seconds=cpu.user + cpu.system,
                                        memory_rss_mib=proc.memory_info().rss / 1048576,
                                        memory_peak_mib=memory_peak / 1048576,
                                        host_cpu_cores=psutil.cpu_count() or 1,gpu=gpu_sample()))
            elif self.path == '/metrics/prometheus':
                cpu = proc.cpu_times()
                payload = request_metrics.render(identity, cpu.user + cpu.system,
                                                 proc.memory_info().rss, psutil.cpu_count() or 1,gpu_sample()).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain; version=0.0.4; charset=utf-8')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            else:
                self.send_json(404, {'error': 'not_found'})

        def do_POST(self):
            if self.path != '/infer':
                self.close_connection = True
                self.send_json(404, {'error': 'not_found'})
                return
            started = time.perf_counter()
            failed = True
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 65536:
                    raise ValueError('invalid_request_size')
                body = json.loads(self.rfile.read(length))
                texts = validate_texts(body, args.batch_size)
                result = self.server.run_inference(lambda: infer(texts))
                failed = not self.send_json(200, result)
            except (ValueError, UnicodeDecodeError):
                self.close_connection = True
                self.send_json(400, {'error': 'invalid_inference_batch'})
            except Exception as error:
                print(json.dumps({'event': 'inference_failed', 'type': type(error).__name__}), flush=True)
                self.send_json(500, {'error': 'inference_failed'})
            finally:
                request_metrics.observe(time.perf_counter() - started, failed)

    server = InferenceHTTPServer(('127.0.0.1', args.port), Handler,
                                 inference_initializer=lambda: torch.set_num_threads(args.threads))

    # An owned VM has its own audited, extendable shutdown deadline. Poll the
    # guard so a recording extension also keeps its application alive. A local
    # process retains the original 15-minute bound.
    guest_root = Path(__file__).resolve().parent
    guard_path = guest_root / 'guard.json' if guest_root.parent == Path('/var/tmp') and guest_root.name.startswith('khu-poc-') else None
    lifetime_cancel = threading.Event()
    lifetime_started = time.time()

    def limit():
        while not lifetime_cancel.wait(1):
            if execution_remaining(guard_path, started_at=lifetime_started, now=time.time()) <= 0:
                print(json.dumps({'event': 'execution_limit', 'completed': completed,
                                  'scope': 'owned_vm' if guard_path else 'local'}), flush=True)
                server.shutdown()
                return

    timer = threading.Thread(target=limit, daemon=True)
    try:
        server.run_inference(lambda: infer(['The service is running.'] * args.batch_size))
        completed = 0
        timer.start()
        print(json.dumps(dict(event='model_ready', status='ready', **identity)), flush=True)
        server.serve_forever()
    finally:
        lifetime_cancel.set()
        server.server_close()


if __name__ == '__main__':
    main()
