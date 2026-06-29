# Prompt Usage and Sharing Log

## 1. Purpose

This document records representative prompt categories and prompt-sharing rules
for the 1st-year Go-based service-control prototype. It is intended to support
development transparency without exposing private conversations, credentials,
tokens, or personal data.

## 2. Prompt Management Policy

Prompt records should follow these rules:

- Store only cleaned representative prompt templates.
- Do not store private conversation transcripts.
- Do not include cloud credentials, API keys, tokens, personal data, or private
  account information.
- Keep prompts aligned with the prototype boundary.
- Do not ask prompts to produce unsupported claims such as final benchmarks,
  production readiness, or actual GPU VM provisioning unless supported by
  verified evidence.

## 3. Main Prompt Categories

| Category | Purpose |
| --- | --- |
| README organization prompt | Organize the repository as an official deliverable management document |
| Requirements definition prompt | Define project scope, functional requirements, non-functional requirements, and validation method |
| Design deliverable writing prompt | Create official design deliverable Markdown sources |
| Go test and validation prompt | Run and summarize Go tests and team-validation evidence |
| Error fixing and log analysis prompt | Analyze command failures and preserve error messages |
| DOCX generation prompt | Convert Markdown deliverables into DOCX submission copies when tooling is available |

## 4. Example Prompt Templates

### README Organization Prompt

```text
Revise README.md as an official deliverable management document for a
1st-year Go-based functional prototype. Separate submission artifacts, design
deliverables, and validation evidence. Do not claim production readiness or
standardized LLM evaluation completion.
```

### Requirements Definition Prompt

```text
Revise docs/submission/requirements_definition.md with sections for document
overview, project scope, functional requirements, non-functional requirements,
submission artifact requirements, development guide requirements, validation
method, prototype boundary, and related artifacts.
```

### Design Deliverable Writing Prompt

```text
Create official design deliverable Markdown files for LLM operation management,
agent registration management, and AI application deployment/control inference
optimization. Keep docs/design as supporting documents and do not delete them.
```

### Go Test and Validation Prompt

```text
Run go test ./... in go/aiops-guard and go/service-control-api, then run
team-validation. Record only the actual command outputs and expected prototype
signals.
```

### Error Fixing and Log Analysis Prompt

```text
Analyze the failed command output, identify whether the issue is environment,
configuration, code, or external infrastructure, and preserve the exact error
message in the validation log.
```

### DOCX Generation Prompt

```text
Generate DOCX submission copies from Markdown sources using pandoc if
available. If conversion fails, do not claim DOCX generation. Record the source
file and target file mapping.
```

## 5. Shared Prompt Usage

Shared prompts should be stored as templates rather than complete private
conversation logs. When shared among project members, each prompt should include:

- Purpose.
- Input files.
- Expected output files.
- Boundary statements.
- Sensitive-data exclusion rule.
- Human review requirement.

## 6. Human Review

A human reviewer should check whether generated text:

- Matches the assigned research scope.
- Uses careful prototype-level wording.
- Avoids final benchmark claims.
- Avoids production-ready claims.
- Avoids unverified cloud provisioning claims.
- Links to existing repository paths.
- Does not expose private or sensitive information.

## 7. Boundary

This log is a documentation aid. It does not prove model performance, coding
agent performance, or production readiness. It records how prompts should be
shared and reviewed for this repository.
