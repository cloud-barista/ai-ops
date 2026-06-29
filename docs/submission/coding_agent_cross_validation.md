# LLM/Coding Agent Cross-Validation Document

## 1. Purpose

This document records the development-process validation approach for using at
least two LLM or coding-agent roles during the 1st-year Go-based
service-control prototype work. It is a process validation record, not a
performance benchmark.

The document intentionally uses neutral role labels when actual product names
are not confirmed. It does not invent specific LLM vendor names or coding-agent
product names.

## 2. Coding Agent Usage Policy

The repository uses a two-role review policy for changes that affect the
submission package:

- A primary coding agent role drafts or revises implementation and documents.
- A secondary review agent role checks consistency, boundary statements,
  README links, test commands, and deliverable completeness.
- A human reviewer performs final acceptance.

The process is intended to reduce documentation drift and prevent unsupported
claims such as production readiness, final benchmark completion, or actual GPU
VM provisioning.

## 3. Used LLM/Coding Agents

| Role Label | Purpose | Naming Boundary |
| --- | --- | --- |
| Agent A / primary coding agent | Drafts Go/API documentation, deliverable mapping, and validation instructions | Neutral role label |
| Agent B / secondary review agent | Cross-checks generated documents, README links, prototype boundary wording, and test evidence | Neutral role label |
| `code-cross-check-agent` | Prototype policy role label in `config/ops_llm_benchmark.json` for code/documentation cross-checking | Repository role label, not a vendor claim |

If a future report uses concrete model or tool names, the reviewer must verify
that those names reflect actual tools used during the work.

## 4. Role Assignment

| Work Item | Primary Role | Review Role | Human Review |
| --- | --- | --- | --- |
| README organization | Agent A | Agent B | Confirms official deliverable tone and link correctness |
| Requirements definition | Agent A | Agent B | Confirms scope and artifact requirements |
| Design deliverables | Agent A | Agent B | Confirms one-to-one mapping with official deliverable names |
| Prompt usage log | Agent A | Agent B | Confirms no private conversation or credential leakage |
| Test and validation log | Agent A | Agent B | Confirms commands, expected outputs, and boundary statements |
| DOCX conversion | Agent A | Agent B | Confirms files exist before claiming availability |

## 5. Cross-Validation Method

The cross-validation method is:

1. Generate or revise a document or code-adjacent artifact.
2. Compare the result against the requested deliverable list.
3. Check that required links resolve to repository paths.
4. Check that prototype boundaries are stated clearly.
5. Check that no final benchmark or production-readiness claim was introduced.
6. Check that DOCX files are only described as available if the files exist.
7. Run applicable Go tests and team-validation when the change could affect
   execution instructions.
8. Record remaining limitations.

## 6. Prompt Examples

Representative prompts should be cleaned before sharing. They should not include
private credentials, tokens, personal data, or full private conversations.

Example prompt categories:

- "Revise README as an official deliverable management document."
- "Create a requirements definition document for a 1st-year Go-based functional prototype."
- "Create three design deliverable Markdown documents mapped one-to-one to the official deliverable names."
- "Review command outputs and summarize validation evidence without claiming production readiness."
- "Generate DOCX conversion copies only if conversion tooling is available."

## 7. Human Review Checklist

Human reviewers should check:

- README title and section headings are official and not personal-project-like.
- Required submission artifacts are listed.
- Design deliverables map one-to-one to official names.
- Markdown source files exist.
- DOCX files exist before being marked as generated or available.
- OpenAPI YAML is linked from README and functional/API guide.
- Go tests and team-validation results are recorded accurately.
- Prototype boundary and benchmark boundary statements are present.

## 8. Validation Evidence

Validation evidence may include:

- `go test ./...` output from `go/aiops-guard`.
- `go test ./...` output from `go/service-control-api`.
- `team-validation` JSON outputs under `runs/<output-dir>/`.
- README link checks.
- DOCX file existence checks.
- Human review notes.

## 9. Boundary

This document describes the development and review process. It does not claim
that any LLM or coding agent has been benchmarked for model quality. It also
does not replace human review, repository tests, or controlled evaluation
protocols.
