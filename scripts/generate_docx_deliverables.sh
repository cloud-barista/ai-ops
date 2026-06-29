#!/usr/bin/env bash
set -u

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR" || exit 1

SOURCES=(
  "docs/submission/requirements_definition.md"
  "docs/deliverables/01_llm_operation_management_design.md"
  "docs/deliverables/02_agent_registration_management_prototype.md"
  "docs/deliverables/03_ai_application_deployment_control_optimization_strategy.md"
)

TARGETS=(
  "docs/submission/requirements_definition.docx"
  "docs/deliverables/docx/01_LLM_Operation_Management_Design.docx"
  "docs/deliverables/docx/02_Agent_Registration_Management_Prototype.docx"
  "docs/deliverables/docx/03_AI_Application_Deployment_Control_Optimization_Strategy.docx"
)

mkdir -p "docs/deliverables/docx"

check_sources() {
  local missing=0
  for src in "${SOURCES[@]}"; do
    if [[ ! -f "$src" ]]; then
      echo "ERROR: source file not found: $src" >&2
      missing=1
    fi
  done
  return "$missing"
}

verify_target() {
  local target="$1"
  if [[ ! -s "$target" ]]; then
    echo "ERROR: DOCX was not generated or is empty: $target" >&2
    return 1
  fi
  echo "generated: $target"
}

convert_with_pandoc() {
  local failed=0
  for i in "${!SOURCES[@]}"; do
    local src="${SOURCES[$i]}"
    local target="${TARGETS[$i]}"
    echo "converting with pandoc: $src -> $target"
    if ! "$PANDOC_BIN" "$src" -o "$target"; then
      echo "ERROR: pandoc conversion failed: $src" >&2
      failed=1
      continue
    fi
    verify_target "$target" || failed=1
  done
  return "$failed"
}

convert_with_python_docx() {
  python3 - "$ROOT_DIR" <<'PY'
import re
import sys
from pathlib import Path

try:
    from docx import Document
except Exception as exc:
    print(f"ERROR: python-docx is unavailable: {exc}", file=sys.stderr)
    sys.exit(2)

root = Path(sys.argv[1])
sources = [
    "docs/submission/requirements_definition.md",
    "docs/deliverables/01_llm_operation_management_design.md",
    "docs/deliverables/02_agent_registration_management_prototype.md",
    "docs/deliverables/03_ai_application_deployment_control_optimization_strategy.md",
]
targets = [
    "docs/submission/requirements_definition.docx",
    "docs/deliverables/docx/01_LLM_Operation_Management_Design.docx",
    "docs/deliverables/docx/02_Agent_Registration_Management_Prototype.docx",
    "docs/deliverables/docx/03_AI_Application_Deployment_Control_Optimization_Strategy.docx",
]

def add_markdown_line(doc, line):
    stripped = line.strip()
    if not stripped:
        return
    if stripped.startswith("# "):
        doc.add_heading(stripped[2:], level=0)
    elif stripped.startswith("## "):
        doc.add_heading(stripped[3:], level=1)
    elif stripped.startswith("### "):
        doc.add_heading(stripped[4:], level=2)
    elif stripped.startswith("- "):
        doc.add_paragraph(stripped[2:], style="List Bullet")
    elif re.match(r"^[0-9]+\. ", stripped):
        doc.add_paragraph(re.sub(r"^[0-9]+\. ", "", stripped), style="List Number")
    elif stripped.startswith("|"):
        doc.add_paragraph(stripped)
    elif stripped.startswith("```"):
        return
    else:
        doc.add_paragraph(stripped)

for src_name, target_name in zip(sources, targets):
    src = root / src_name
    target = root / target_name
    if not src.exists():
        print(f"ERROR: source file not found: {src_name}", file=sys.stderr)
        sys.exit(1)
    target.parent.mkdir(parents=True, exist_ok=True)
    doc = Document()
    in_code = False
    code_lines = []
    for line in src.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("```"):
            if in_code and code_lines:
                doc.add_paragraph("\n".join(code_lines))
                code_lines = []
            in_code = not in_code
            continue
        if in_code:
            code_lines.append(line)
            continue
        add_markdown_line(doc, line)
    doc.save(target)
    if not target.exists() or target.stat().st_size == 0:
        print(f"ERROR: DOCX was not generated or is empty: {target_name}", file=sys.stderr)
        sys.exit(1)
    print(f"generated: {target_name}")
PY
}

if ! check_sources; then
  exit 1
fi

PANDOC_BIN=""
if command -v pandoc >/dev/null 2>&1; then
  PANDOC_BIN="$(command -v pandoc)"
elif command -v pandoc.exe >/dev/null 2>&1; then
  PANDOC_BIN="$(command -v pandoc.exe)"
elif command -v where.exe >/dev/null 2>&1; then
  PANDOC_BIN="$(where.exe pandoc 2>/dev/null | tr -d '\r' | head -n 1 || true)"
  if [[ -n "$PANDOC_BIN" ]] && command -v cygpath >/dev/null 2>&1; then
    PANDOC_BIN="$(cygpath -u "$PANDOC_BIN")"
  fi
fi

if [[ -n "$PANDOC_BIN" ]]; then
  if "$PANDOC_BIN" --version >/dev/null 2>&1; then
    echo "using pandoc: $PANDOC_BIN"
    convert_with_pandoc
    exit $?
  fi
  echo "pandoc was found but could not be executed from this Bash environment: $PANDOC_BIN" >&2
  PANDOC_BIN=""
fi

echo "pandoc is unavailable; trying python-docx fallback." >&2

if command -v python3 >/dev/null 2>&1; then
  convert_with_python_docx
  exit $?
fi

echo "ERROR: DOCX conversion tools are unavailable." >&2
echo "Install pandoc or python3 with python-docx, then rerun scripts/generate_docx_deliverables.sh." >&2
exit 1
