# Revision 1 Run Navigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Revision 1 생성 후 현재 화면을 유지하고 완료 상태와 Flow ID를 표시한다.

**Architecture:** `submitAutomationRun`의 성공 경로에서 강제 `switchView("results")`를 제거한다. 기존 결과 렌더링은 유지하고 전용 상태 요소를 갱신한다.

**Tech Stack:** Go embedded web UI, JavaScript, CSS, Go test, in-app Browser

## Global Constraints

- Flow 생성과 저장 동작은 변경하지 않는다.
- 실험 결과 화면은 수동 탐색으로만 연다.

---

### Task 1: 현재 화면 유지와 완료 표시

**Files:**
- Modify: `go/service-control-api/internal/webui/webui_test.go`
- Modify: `go/service-control-api/internal/webui/static/index.html`
- Modify: `go/service-control-api/internal/webui/static/app.js`
- Modify: `go/service-control-api/internal/webui/static/app.css`

**Interfaces:**
- Consumes: `run.flow.correlation_id`
- Produces: `#automation-run-completion` 상태 표시

- [ ] 실패하는 화면 계약 테스트를 작성한다.
- [ ] `go test ./internal/webui -run TestAutomationRunStaysOnCurrentView -count=1`로 실패를 확인한다.
- [ ] 강제 화면 전환을 제거하고 완료 상태 표시를 구현한다.
- [ ] 웹 UI 및 전체 Go 테스트를 실행한다.
- [ ] 브라우저에서 버튼 실행 후 현재 화면 유지와 Flow ID 표시를 확인한다.
- [ ] 변경 파일만 커밋하고 `geon` 브랜치에 푸시한다.
