"""Run a reproducible local traffic/cost experiment against real local processes."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from threading import Event, Lock


ROOT = Path(__file__).resolve().parents[2]
APPDEPLOY = ROOT / "AppDeploy"
CONTROL = ROOT / "ai-ops-geon" / "go" / "service-control-api"
APP_DIR = APPDEPLOY / "examples" / "local-app"
APP_PORT = 18090
CONTROL_PORT = 18190
APP_BASE = f"http://127.0.0.1:{APP_PORT}/api/v1"
CONTROL_BASE = f"http://127.0.0.1:{CONTROL_PORT}/api/v1"
PRIMARY = "traffic-primary"
ALTERNATIVE = "traffic-alternative"
STOP_LOCK = Lock()
DEFAULT_FAULT_CODES = "GPU_OOM,CUDA_MISMATCH,RESOURCE_UNAVAILABLE,RESOURCE_INSUFFICIENT,TRANSIENT_DEPLOYMENT_FAILURE"


class HTTP:
    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def request(self, path: str, method: str = "GET", payload=None):
        body = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            self.base + path,
            data=body,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read().decode()
                return response.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as error:
            raw = error.read().decode()
            try:
                payload = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                payload = {"raw": raw}
            return error.code, payload

    def require(self, path: str, method: str = "GET", payload=None, statuses=(200, 201, 202)):
        status, response = self.request(path, method, payload)
        if status not in statuses:
            raise RuntimeError(f"{method} {path} returned {status}: {response}")
        return response


def wait_ready(client: HTTP, path: str, process: subprocess.Popen):
    last = None
    for _ in range(60):
        if process.poll() is not None:
            raise RuntimeError(f"service exited before readiness: {path}")
        try:
            status, response = client.request(path)
            last = (status, response)
            if status == 200 and response and response.get("status") == "ok":
                return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.25)
    raise RuntimeError(f"service did not become ready: {path}, last_response={last}")


def build(binary: Path, cwd: Path, cache: Path, package: str):
    env = os.environ.copy()
    env["GOTELEMETRY"] = "off"
    env["GOCACHE"] = str(cache)
    subprocess.run(["go", "build", "-o", str(binary), package], cwd=cwd, env=env, check=True)


def start_process(binary: Path, cwd: Path, env: dict[str, str]):
    process_env = os.environ.copy()
    process_env.update(env)
    return subprocess.Popen(
        [str(binary)],
        cwd=cwd,
        env=process_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )


def stop_process(process):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def envelope(message_id, message_type, correlation_id, trace_id, data):
    return {
        "contract_version": "1.0",
        "message_id": message_id,
        "message_type": message_type,
        "occurred_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "correlation_id": correlation_id,
        "trace_id": trace_id,
        "source": {"system": "appdeploy", "component": "local-traffic-runner"},
        "target": {"system": "khu-geon", "component": "agent-control"},
        "data": data,
    }


def cost_per_attempt(target: str, memory: str, args) -> float:
    if target == PRIMARY:
        hourly = args.primary_rate
    elif memory == "512Mi":
        hourly = args.adjusted_rate
    else:
        hourly = args.alternative_rate
    return hourly * args.billing_seconds / 3600.0


def start_flow(control: HTTP, memory: str, expected_rps: float):
    result = control.require(
        "/agent-control/automation-runs",
        "POST",
        {
            "input_type": "structured",
            "requested_by": "local-traffic-cost-experiment",
            "app_spec": {
                "app_id": "traffic-experiment-app",
                "app_version": "0.1.0",
                "workload_type": "INFERENCE",
                "cpu_cores": 1,
                "memory_mib": 512 if memory == "512Mi" else 256,
                "storage_gib": 1,
                "replicas_min": 1,
                "replicas_max": 1,
                "expected_rps": expected_rps,
            },
        },
    )
    flow = result.get("flow") or {}
    decision = flow.get("decision") or {}
    if not result.get("correlation_id") or not decision.get("decision_id"):
        raise RuntimeError(f"automation flow did not contain a decision: {result}")
    return {
        "correlation_id": result["correlation_id"],
        "trace_id": result["trace_id"],
        "decision_id": decision["decision_id"],
        "candidate_id": decision.get("selected_candidate_id", ""),
    }


def send_failure_feedback(control: HTTP, mode: str, case_id: str, attempt: int, flow, deployment_id: str, code: str, message: str, attempt_cost: float):
    correlation_id = flow["correlation_id"]
    trace_id = flow["trace_id"]
    decision_id = flow["decision_id"]
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    control.require(
        "/agent-control/deployment-status",
        "POST",
        envelope(
            f"{mode}-{case_id}-status-{attempt}",
            "deployment.status.changed",
            correlation_id,
            trace_id,
            {
                "deployment_status": {
                    "deployment_id": deployment_id,
                    "decision_id": decision_id,
                    "state": "FAILED",
                    "error_code": code,
                    "message": message,
                    "updated_at": now,
                }
            },
        ),
    )
    result = control.require(
        "/agent-control/optimization-feedback",
        "POST",
        envelope(
            f"{mode}-{case_id}-feedback-{attempt}",
            "optimization.feedback.created",
            correlation_id,
            trace_id,
            {
                "optimization_feedback": {
                    "feedback_id": f"{mode}-{case_id}-feedback-{attempt}",
                    "decision_id": decision_id,
                    "deployment_id": deployment_id,
                    "outcome": "FAILED",
                    "error_code": code,
                    "observation_window": {"started_at": now, "ended_at": now},
                    "metrics": {
                        "resource": {"cpu_average_percent": 0, "memory_peak_mib": 0, "accelerator_average_percent": 0, "accelerator_memory_peak_mib": 0},
                        "inference": {"latency_p95_ms": 0, "throughput_rps": 0, "error_rate_percent": 100},
                        "cost": {"currency": "LOCAL", "estimated_cost": attempt_cost},
                    },
                    "slo_violations": ["deployment_failed"],
                    "created_at": now,
                }
            },
        ),
    )
    repair = result.get("repair_decision")
    if not repair:
        raise RuntimeError(f"optimizer returned no repair decision: {result}")
    return repair


def send_success_feedback(control: HTTP, mode: str, case_id: str, attempt: int, flow, deployment_id: str, latency_ms: float, attempt_cost: float):
    correlation_id = flow["correlation_id"]
    trace_id = flow["trace_id"]
    decision_id = flow["decision_id"]
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    control.require(
        "/agent-control/deployment-status",
        "POST",
        envelope(
            f"{mode}-{case_id}-success-status-{attempt}",
            "deployment.status.changed",
            correlation_id,
            trace_id,
            {"deployment_status": {"deployment_id": deployment_id, "decision_id": decision_id, "state": "RUNNING", "updated_at": now}},
        ),
    )
    control.require(
        "/agent-control/optimization-feedback",
        "POST",
        envelope(
            f"{mode}-{case_id}-success-feedback-{attempt}",
            "optimization.feedback.created",
            correlation_id,
            trace_id,
            {
                "optimization_feedback": {
                    "feedback_id": f"{mode}-{case_id}-success-feedback-{attempt}",
                    "decision_id": decision_id,
                    "deployment_id": deployment_id,
                    "outcome": "SUCCEEDED",
                    "observation_window": {"started_at": now, "ended_at": now},
                    "metrics": {
                        "resource": {"cpu_average_percent": 10, "memory_peak_mib": 512 if attempt > 1 else 256, "accelerator_average_percent": 0, "accelerator_memory_peak_mib": 0},
                        "inference": {"latency_p95_ms": latency_ms, "throughput_rps": 1, "error_rate_percent": 0},
                        "cost": {"currency": "LOCAL", "estimated_cost": attempt_cost},
                    },
                    "created_at": now,
                }
            },
        ),
    )


def write_resource_catalog(path: Path, args):
    catalog = {
        "version": "local-traffic-risk-v1",
        "candidates": [
            {
                "candidate_id": PRIMARY,
                "cpu_cores": 4,
                "memory_mib": 8192,
                "storage_gib": 100,
                "cost_per_hour": args.catalog_primary_cost,
                "availability_score": 0.99,
                "failure_risk_score": args.primary_risk_score if args.primary_risk_score is not None else args.primary_fault_rate,
            },
            {
                "candidate_id": ALTERNATIVE,
                "cpu_cores": 8,
                "memory_mib": 16384,
                "storage_gib": 200,
                "cost_per_hour": args.catalog_alternative_cost,
                "availability_score": 0.99,
                "failure_risk_score": args.alternative_risk_score if args.alternative_risk_score is not None else args.alternative_fault_rate,
            },
        ],
    }
    path.write_text(json.dumps(catalog, indent=2), encoding="utf-8")


def run_case(case_id: str, mode: str, app_version_id: str, app: HTTP, control: HTTP | None, args):
    started = time.perf_counter()
    target = PRIMARY
    memory = "256Mi"
    attempts = 0
    faults = 0
    target_changes = 0
    actions = Counter()
    fault_codes = Counter()
    cost = 0.0
    success_feedback_sent = False
    flow = None
    if control:
        flow = start_flow(control, memory, args.rps or args.concurrency)
        if flow["candidate_id"] == ALTERNATIVE:
            target = ALTERNATIVE
    initial_target = target
    success = False
    for attempt in range(1, args.max_attempts + 1):
        attempts = attempt
        attempt_cost = cost_per_attempt(target, memory, args)
        cost += attempt_cost
        result_status, result = app.request(
            "/deployments",
            "POST",
            {
                "app_version_id": app_version_id,
                "target_profile_id": target,
                "parameters": {"experiment_case_id": case_id, "experiment_attempt": attempt},
                "requirements": {"runtime": "cpu", "resources": {"cpu": "1", "memory": memory, "storage": "1Gi"}},
            },
        )
        if 200 <= result_status < 300:
            deployment = result
            if deployment.get("status") != "RUNNING":
                raise RuntimeError(f"deployment did not run: {deployment}")
            if control and (faults > 0 or args.disable_fast_path):
                send_success_feedback(control, mode, case_id, attempt, flow, deployment["deployment_id"], (time.perf_counter() - started) * 1000, attempt_cost)
                success_feedback_sent = True
            with STOP_LOCK:
                stop_status, stop_result = app.request(f"/deployments/{deployment['deployment_id']}/stop", "POST")
            if not 200 <= stop_status < 300:
                raise RuntimeError(f"deployment stop failed: {stop_status} {stop_result}")
            success = True
            break

        faults += 1
        error = (result or {}).get("error") or {}
        code = error.get("code", "UNKNOWN")
        fault_codes[code] += 1
        deployment_id = ((error.get("details") or {}).get("deployment_id"))
        if not deployment_id:
            raise RuntimeError(f"fault response did not contain deployment_id: {result}")
        if not control:
            continue
        repair = send_failure_feedback(control, mode, case_id, attempt, flow, deployment_id, code, error.get("message", code), attempt_cost)
        action = repair.get("action", "")
        actions[action] += 1
        if action in ("REQUEST_ALTERNATIVE_RESOURCE", "ADJUST_RESOURCE_REQUIREMENT"):
            if target != ALTERNATIVE:
                target_changes += 1
            target = ALTERNATIVE
        if action == "ADJUST_RESOURCE_REQUIREMENT":
            memory = "512Mi"
        elif action not in ("RETRY_DEPLOYMENT", "REQUEST_ALTERNATIVE_RESOURCE"):
            raise RuntimeError(f"unsupported repair action: {action} for fault={code} response={result}")
        flow = start_flow(control, memory, args.rps or args.concurrency)

    latency_ms = (time.perf_counter() - started) * 1000
    latency_slo_violation = not success or latency_ms > args.slo_ms
    recovery_slo_violation = not success or attempts > args.slo_attempts
    return {
        "case_id": case_id,
        "success": success,
        "attempts": attempts,
        "faults": faults,
        "retries": max(0, attempts - 1),
        "latency_ms": latency_ms,
        "slo_violation": latency_slo_violation or recovery_slo_violation,
        "latency_slo_violation": latency_slo_violation,
        "recovery_slo_violation": recovery_slo_violation,
        "estimated_cost": cost,
        "success_feedback_sent": success_feedback_sent,
        "fast_path_skipped": bool(control and success and not success_feedback_sent),
        "initial_target": initial_target,
        "target_changes": target_changes,
        "actions": dict(actions),
        "fault_codes": dict(fault_codes),
    }


def register_environment(app: HTTP, args):
    artifact_uri = APP_DIR.as_uri()
    response = app.require(
        "/apps",
        "POST",
        {
            "app_spec": {
                "schema_version": "appspec.khu.ai/v1alpha1",
                "kind": "AIApp",
                "metadata": {"name": "traffic-experiment-app", "version": "0.1.0"},
                "artifact": {"type": "script", "uri": artifact_uri},
                "entrypoint": {"command": "powershell.exe", "args": ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "run.ps1"]},
                "runtime": {"type": "cpu"},
                "resources": {"cpu": "1", "memory": "256Mi", "storage": "1Gi"},
            }
        },
    )
    for target_id in (PRIMARY, ALTERNATIVE):
        app.require(
            "/target-profiles",
            "POST",
            {
                "target_profile_id": target_id,
                "vm_type": "Local",
                "csp": "local",
                "status": "READY",
                "runtime": {"runtime_type": "local", "operating_mode": "local_process"},
                "supported_runtimes": ["cpu"],
                "capacity": {
                    "cpu_cores": args.primary_capacity_cpu if target_id == PRIMARY else args.alternative_capacity_cpu,
                    "memory_bytes": 68719476736,
                    "storage_bytes": 107374182400,
                },
                "cost_weight": 1 if target_id == ALTERNATIVE else 2,
            },
        )
    return response["app_version_id"]


def aggregate(results, args):
    latencies = sorted(item["latency_ms"] for item in results)
    actions = Counter()
    fault_codes = Counter()
    for item in results:
        actions.update(item["actions"])
        fault_codes.update(item["fault_codes"])

    def percentile(percent):
        if not latencies:
            return 0.0
        index = min(len(latencies) - 1, max(0, round((len(latencies) - 1) * percent / 100)))
        return round(latencies[index], 3)

    successes = sum(item["success"] for item in results)
    faults = sum(item["faults"] for item in results)
    retries = sum(item["retries"] for item in results)
    requests_with_retry = sum(item["retries"] > 0 for item in results)
    cost = sum(item["estimated_cost"] for item in results)
    return {
        "requests": len(results),
        "successes": successes,
        "success_rate_percent": round(100 * successes / len(results), 2) if results else 0,
        "faults": faults,
        "retries": retries,
        "requests_with_retry": requests_with_retry,
        "retry_rate_percent": round(100 * requests_with_retry / len(results), 2) if results else 0,
        "slo_violations": sum(item["slo_violation"] for item in results),
        "slo_violation_rate_percent": round(100 * sum(item["slo_violation"] for item in results) / len(results), 2) if results else 0,
        "latency_slo_violations": sum(item["latency_slo_violation"] for item in results),
        "recovery_slo_violations": sum(item["recovery_slo_violation"] for item in results),
        "latency_ms": {"p50": percentile(50), "p95": percentile(95), "p99": percentile(99), "max": round(max(latencies), 3) if latencies else 0},
        "estimated_cost_local": round(cost, 6),
        "estimated_cost_per_success_local": round(cost / successes, 6) if successes else None,
        "target_changes": sum(item["target_changes"] for item in results),
        "initial_alternative_selections": sum(item["initial_target"] == ALTERNATIVE for item in results),
        "success_feedback_sent": sum(item["success_feedback_sent"] for item in results),
        "fast_path_skips": sum(item["fast_path_skipped"] for item in results),
        "actions": dict(actions),
        "fault_codes": dict(fault_codes),
    }


def reduction(baseline, optimized, field):
    before = baseline[field]
    after = optimized[field]
    return round(100 * (before - after) / before, 2) if before else 0.0


def run_mode(mode: str, args, binaries, catalog_path: Path):
    mode_root = ROOT / "ai-ops-geon" / "tmp"
    mode_root.mkdir(parents=True, exist_ok=True)
    store = mode_root / f"local-traffic-{mode}.json"
    runtime = mode_root / f"local-traffic-{mode}-runtime"
    store.unlink(missing_ok=True)
    if runtime.exists():
        import shutil
        shutil.rmtree(runtime)
    app_process = control_process = None
    try:
        app_process = start_process(
            binaries["app"],
            APPDEPLOY,
            {
                "RESOURCE_PROVIDER": "local",
                "PLACEMENT_PROVIDER": "local",
                "AIAPP_SERVER_PORT": str(APP_PORT),
                "AIAPP_STORE_PATH": str(store),
                "AIAPP_LOCAL_RUNTIME_WORK_DIR": str(runtime),
                "AIAPP_LOCAL_FAULT_RATE": "0",
                "AIAPP_LOCAL_FAULT_TARGET_RATES": f"{PRIMARY}={args.primary_fault_rate},{ALTERNATIVE}={args.alternative_fault_rate}",
                "AIAPP_LOCAL_FAULT_SEED": str(args.seed),
                "AIAPP_LOCAL_FAULT_DETERMINISTIC": "true",
                "AIAPP_LOCAL_FAULT_CODES": args.fault_codes,
            },
        )
        app = HTTP(APP_BASE)
        wait_ready(app, "/healthz", app_process)
        control = None
        if mode == "with_optimizer":
            control_process = start_process(
                binaries["control"],
                CONTROL,
                {"PORT": str(CONTROL_PORT), "AIOPS_RESOURCE_CATALOG_PATH": str(catalog_path)},
            )
            control = HTTP(CONTROL_BASE)
            wait_ready(HTTP(f"http://127.0.0.1:{CONTROL_PORT}"), "/healthz", control_process)
        app_version_id = register_environment(app, args)

        start_event = Event()

        def worker(case_number):
            start_event.wait()
            return run_case(f"case-{case_number:04d}", mode, app_version_id, app, control, args)

        with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            futures = []
            if args.rps > 0:
                for case_number in range(1, args.requests + 1):
                    futures.append(executor.submit(run_case, f"case-{case_number:04d}", mode, app_version_id, app, control, args))
                    time.sleep(1.0 / args.rps)
            else:
                futures = [executor.submit(worker, case_number) for case_number in range(1, args.requests + 1)]
                start_event.set()
            results = [future.result() for future in futures]
        return aggregate(results, args)
    finally:
        stop_process(control_process)
        stop_process(app_process)
        store.unlink(missing_ok=True)
        if runtime.exists():
            import shutil
            shutil.rmtree(runtime)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requests", type=int, default=32)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--rps", type=float, default=0, help="steady arrival rate; 0 means burst")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--primary-fault-rate", type=float, default=0.65)
    parser.add_argument("--alternative-fault-rate", type=float, default=0.05)
    parser.add_argument("--fault-codes", default=DEFAULT_FAULT_CODES)
    parser.add_argument("--primary-risk-score", type=float)
    parser.add_argument("--alternative-risk-score", type=float)
    parser.add_argument("--max-attempts", type=int, default=8)
    parser.add_argument("--slo-ms", type=float, default=500)
    parser.add_argument("--slo-attempts", type=int, default=3)
    parser.add_argument("--billing-seconds", type=float, default=60)
    parser.add_argument("--primary-rate", type=float, default=2.0, help="local currency/hour")
    parser.add_argument("--alternative-rate", type=float, default=0.8, help="local currency/hour")
    parser.add_argument("--adjusted-rate", type=float, default=1.1, help="local currency/hour")
    parser.add_argument("--catalog-primary-cost", type=float, default=1.0)
    parser.add_argument("--catalog-alternative-cost", type=float, default=1.0)
    parser.add_argument("--primary-capacity-cpu", type=int, default=64)
    parser.add_argument("--alternative-capacity-cpu", type=int, default=64)
    parser.add_argument("--disable-fast-path", action="store_true")
    parser.add_argument("--report-path", type=Path, default=ROOT / "ai-ops-geon" / "tmp" / "local-traffic-cost-report.json")
    args = parser.parse_args()
    if args.requests < 1 or args.concurrency < 1 or args.max_attempts < 1 or args.slo_attempts < 1 or args.primary_capacity_cpu < 1 or args.alternative_capacity_cpu < 1:
        parser.error("requests, concurrency, max-attempts, slo-attempts, and capacities must be positive")
    if any(score is not None and not 0 <= score <= 1 for score in (args.primary_risk_score, args.alternative_risk_score)):
        parser.error("risk scores must be between 0 and 1")
    return args


def main():
    args = parse_args()
    tmp = ROOT / "ai-ops-geon" / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    catalog_path = tmp / "local-traffic-resource-catalog.json"
    binaries = {"app": APPDEPLOY / "tmp" / "local-traffic-appdeploy.exe", "control": CONTROL / "tmp" / "local-traffic-control.exe"}
    for path in binaries.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    try:
        build(binaries["app"], APPDEPLOY, APPDEPLOY / "tmp" / "local-traffic-gocache", "./cmd/server")
        build(binaries["control"], CONTROL, CONTROL / "tmp" / "local-traffic-gocache", "./cmd/service-control-api")
        write_resource_catalog(catalog_path, args)
        baseline = run_mode("without_optimizer", args, binaries, catalog_path)
        optimized = run_mode("with_optimizer", args, binaries, catalog_path)
        report = {
            "experiment": {
                "environment": "real local AppDeploy and service-control-api processes",
                "traffic": {"requests": args.requests, "concurrency": args.concurrency, "rps": args.rps, "pattern": "burst" if args.rps <= 0 else "steady"},
                "fault_model": {"seed": args.seed, "primary_rate": args.primary_fault_rate, "alternative_rate": args.alternative_fault_rate, "codes": [code.strip() for code in args.fault_codes.split(",") if code.strip()], "deterministic_replay": True},
                "risk_model": {"primary_score": args.primary_risk_score if args.primary_risk_score is not None else args.primary_fault_rate, "alternative_score": args.alternative_risk_score if args.alternative_risk_score is not None else args.alternative_fault_rate},
                "cost_model": {"currency": "LOCAL", "billing_seconds": args.billing_seconds, "primary_rate_per_hour": args.primary_rate, "alternative_rate_per_hour": args.alternative_rate, "adjusted_rate_per_hour": args.adjusted_rate},
                "capacity_cpu": {"primary": args.primary_capacity_cpu, "alternative": args.alternative_capacity_cpu},
                "pre_fault_risk_selection": True,
                "fast_path": not args.disable_fast_path,
                "slo_ms": args.slo_ms,
                "recovery_slo_attempts": args.slo_attempts,
            },
            "without_optimizer": baseline,
            "with_optimizer": optimized,
            "comparison": {
                "estimated_cost_reduction_percent": reduction(baseline, optimized, "estimated_cost_local"),
                "retry_reduction_percent": reduction(baseline, optimized, "retries"),
                "slo_violation_reduction_percent": reduction(baseline, optimized, "slo_violations"),
                "latency_slo_violation_reduction_percent": reduction(baseline, optimized, "latency_slo_violations"),
                "recovery_slo_violation_reduction_percent": reduction(baseline, optimized, "recovery_slo_violations"),
                "p95_latency_delta_ms": round(optimized["latency_ms"]["p95"] - baseline["latency_ms"]["p95"], 3),
            },
        }
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("local_traffic_cost_experiment: PASS " + json.dumps(report, separators=(",", ":")))
    finally:
        catalog_path.unlink(missing_ok=True)
        for path in binaries.values():
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"local_traffic_cost_experiment: FAIL {error}", file=sys.stderr)
        sys.exit(1)
