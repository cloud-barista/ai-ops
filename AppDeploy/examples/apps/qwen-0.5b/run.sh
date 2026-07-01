#!/usr/bin/env bash
set -euo pipefail

PORT="18081"
BIND_ADDR="0.0.0.0"
MODEL_ID="Qwen/Qwen2.5-0.5B-Instruct"
MODEL_CACHE=""

for arg in "$@"; do
  case "$arg" in
    --port=*)
      PORT="${arg#*=}"
      ;;
    --bind=*)
      BIND_ADDR="${arg#*=}"
      ;;
    --model-id=*)
      MODEL_ID="${arg#*=}"
      ;;
    --model-cache=*)
      MODEL_CACHE="${arg#*=}"
      ;;
  esac
done

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_FILE="${APP_DIR}/qwen_server.py"

if [ -z "${MODEL_CACHE}" ]; then
  MODEL_CACHE="${APP_DIR}/model-cache"
fi
mkdir -p "${MODEL_CACHE}"

cat > "${APP_FILE}" <<'PY'
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("QWEN_PORT", "18081"))
BIND_ADDR = os.environ.get("QWEN_BIND", "0.0.0.0")
MODEL_ID = os.environ.get("QWEN_MODEL_ID", "Qwen/Qwen2.5-0.5B-Instruct")

STATE = {
    "status": "loading",
    "model": MODEL_ID,
    "device": "unknown",
    "loaded_at": None,
    "error": None,
}
TOKENIZER = None
MODEL = None
TORCH = None


def load_model():
    global TOKENIZER, MODEL, TORCH
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        TORCH = torch
        TOKENIZER = AutoTokenizer.from_pretrained(MODEL_ID)
        kwargs = {}
        if torch.cuda.is_available():
            kwargs["device_map"] = "auto"
            kwargs["torch_dtype"] = torch.bfloat16
            STATE["device"] = torch.cuda.get_device_name(0)
        else:
            kwargs["torch_dtype"] = torch.float32
            STATE["device"] = "cpu"
        MODEL = AutoModelForCausalLM.from_pretrained(MODEL_ID, **kwargs)
        if not torch.cuda.is_available():
            MODEL.to("cpu")
        MODEL.eval()
        STATE["status"] = "ready"
        STATE["loaded_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    except Exception as exc:
        STATE["status"] = "error"
        STATE["error"] = str(exc)


def generate_text(prompt, max_new_tokens):
    if STATE["status"] != "ready":
        raise RuntimeError("model is not ready")
    messages = [{"role": "user", "content": prompt}]
    text = TOKENIZER.apply_chat_template(
        messages,
        tokenize=False,
        add_generation_prompt=True,
    )
    inputs = TOKENIZER([text], return_tensors="pt")
    inputs = inputs.to(MODEL.device)
    with TORCH.no_grad():
        output_ids = MODEL.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            do_sample=False,
            pad_token_id=TOKENIZER.eos_token_id,
        )
    generated_ids = output_ids[0][inputs.input_ids.shape[-1]:]
    return TOKENIZER.decode(generated_ids, skip_special_tokens=True).strip()


class Handler(BaseHTTPRequestHandler):
    server_version = "qwen-0.5b-server/0.1.0"

    def _json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._json(200, STATE)
            return
        self._json(404, {"error": "not_found"})

    def do_POST(self):
        if self.path != "/generate":
            self._json(404, {"error": "not_found"})
            return
        try:
            length = int(self.headers.get("content-length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            prompt = str(payload.get("prompt", "")).strip()
            if not prompt:
                raise ValueError("prompt is required")
            max_new_tokens = int(payload.get("max_new_tokens", 64))
            max_new_tokens = max(1, min(max_new_tokens, 256))
            output = generate_text(prompt, max_new_tokens)
            self._json(200, {
                "model": MODEL_ID,
                "output": output,
                "max_new_tokens": max_new_tokens,
            })
        except RuntimeError as exc:
            self._json(503, {"error": "not_ready", "message": str(exc), "state": STATE})
        except Exception as exc:
            self._json(400, {"error": "bad_request", "message": str(exc)})

    def log_message(self, fmt, *args):
        return


if __name__ == "__main__":
    threading.Thread(target=load_model, daemon=True).start()
    ThreadingHTTPServer((BIND_ADDR, PORT), Handler).serve_forever()
PY

export QWEN_PORT="${PORT}"
export QWEN_BIND="${BIND_ADDR}"
export QWEN_MODEL_ID="${MODEL_ID}"
export HF_HOME="${MODEL_CACHE}"
export TRANSFORMERS_CACHE="${MODEL_CACHE}/transformers"
exec python3 "${APP_FILE}"
