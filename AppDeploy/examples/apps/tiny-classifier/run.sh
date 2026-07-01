#!/usr/bin/env bash
set -euo pipefail

PORT="18080"
BIND_ADDR="0.0.0.0"

for arg in "$@"; do
  case "$arg" in
    --port=*)
      PORT="${arg#*=}"
      ;;
    --bind=*)
      BIND_ADDR="${arg#*=}"
      ;;
  esac
done

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_FILE="${APP_DIR}/tiny_classifier_app.py"

cat > "${APP_FILE}" <<'PY'
import json
import math
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("TINY_CLASSIFIER_PORT", "18080"))
BIND_ADDR = os.environ.get("TINY_CLASSIFIER_BIND", "0.0.0.0")

CENTROIDS = {
    "setosa": [5.0, 3.4, 1.5, 0.2],
    "versicolor": [5.9, 2.8, 4.3, 1.3],
    "virginica": [6.6, 3.0, 5.6, 2.0],
}


def predict(features):
    distances = {
        label: math.sqrt(sum((float(a) - b) ** 2 for a, b in zip(features, centroid)))
        for label, centroid in CENTROIDS.items()
    }
    inverted = {label: 1.0 / (distance + 1e-6) for label, distance in distances.items()}
    total = sum(inverted.values())
    scores = {label: value / total for label, value in inverted.items()}
    label = max(scores, key=scores.get)
    return label, scores


class Handler(BaseHTTPRequestHandler):
    server_version = "tiny-classifier/0.1.0"

    def _json(self, status, payload):
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._json(200, {"status": "ok", "model": "tiny-centroid-classifier"})
            return
        self._json(404, {"error": "not_found"})

    def do_POST(self):
        if self.path != "/predict":
            self._json(404, {"error": "not_found"})
            return
        try:
            length = int(self.headers.get("content-length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            features = payload.get("features", [])
            if len(features) != 4:
                raise ValueError("features must contain four numeric values")
            label, scores = predict(features)
            self._json(200, {
                "label": label,
                "scores": {key: round(value, 6) for key, value in scores.items()},
                "model": "tiny-centroid-classifier",
            })
        except Exception as exc:
            self._json(400, {"error": "bad_request", "message": str(exc)})

    def log_message(self, fmt, *args):
        return


if __name__ == "__main__":
    ThreadingHTTPServer((BIND_ADDR, PORT), Handler).serve_forever()
PY

export TINY_CLASSIFIER_PORT="${PORT}"
export TINY_CLASSIFIER_BIND="${BIND_ADDR}"
exec python3 "${APP_FILE}"
