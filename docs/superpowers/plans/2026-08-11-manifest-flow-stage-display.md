# Manifest Flow Stage Display Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Revision 1과 Revision 2를 같은 Flow의 독립된 Manifest 단계 패널로 표시한다.

**Architecture:** 기존 `manifest_revisions` 데이터와 `renderManifestOutputs` 렌더러를 유지하고, 각 Revision 패널에 단계명과 동일한 `correlation_id`를 표시한다. 자동화 실행 화면과 실험 결과 화면은 동일한 prefix 기반 렌더링 규칙을 사용한다.

**Tech Stack:** Go 1.25+, embedded HTML/CSS/JavaScript, Echo, Go test, in-app Browser

## Global Constraints

- Revision 1과 Revision 2는 동일한 Flow의 단계이며 별도 Flow로 생성하지 않는다.
- AppDeploy 또는 백엔드 통신 계약은 변경하지 않는다.
- 실행 전에는 Flow와 Revision이 생성되지 않았음을 명시한다.

---

### Task 1: Manifest Revision 패널에 Flow 단계 표시

**Files:**
- Modify: `go/service-control-api/internal/webui/webui_test.go`
- Modify: `go/service-control-api/internal/webui/static/index.html`
- Modify: `go/service-control-api/internal/webui/static/app.js`
- Modify: `go/service-control-api/internal/webui/static/app.css`

**Interfaces:**
- Consumes: `flow.correlation_id`, `flow.manifest_revisions`
- Produces: `manifest-initial-flow-id`, `manifest-optimized-flow-id` 및 `experiment-` prefix 대응 요소

- [ ] **Step 1: 실패하는 화면 계약 테스트 작성**

`TestControlAppSeparatesManifestFlowStages`에서 자동화 실행 화면과 실험 결과 화면에 Revision별 Flow ID 요소와 단계 설명이 존재하는지 검사한다.

- [ ] **Step 2: 테스트 실패 확인**

Run: `go test ./internal/webui -run TestControlAppSeparatesManifestFlowStages -count=1`

Expected: 새 Flow ID 요소가 없어 FAIL.

- [ ] **Step 3: HTML과 렌더러 구현**

두 화면의 Revision 패널에 아래 정보를 독립적으로 배치한다.

```text
Revision 1 | 배포 판단 | Flow ID
Revision 2 | 운영 최적화 | Flow ID
```

`renderManifestSet(prefix, flow)`는 두 Flow ID 요소에 동일한 `flow.correlation_id`를 기록하고, Flow가 없으면 `Flow 미생성`을 기록한다.

- [ ] **Step 4: 패널 시각 구조 정리**

Revision 패널을 개별 경계와 헤더를 가진 두 블록으로 표시하고 긴 Flow ID가 줄바꿈되도록 CSS를 적용한다.

- [ ] **Step 5: 자동 테스트 실행**

Run: `go test ./internal/webui -count=1`

Expected: PASS.

- [ ] **Step 6: 전체 서비스 테스트 실행**

Run: `go test ./... -count=1`

Expected: PASS.

- [ ] **Step 7: 브라우저 검증**

`http://127.0.0.1:18080/`에서 실행 전 `Flow 미생성`을 확인하고, Revision 1 실행 후 두 패널이 동일한 Flow ID를 표시하는지 확인한다.

- [ ] **Step 8: 커밋 및 geon 브랜치 푸시**

```bash
git add go/service-control-api/internal/webui/webui_test.go \
  go/service-control-api/internal/webui/static/index.html \
  go/service-control-api/internal/webui/static/app.js \
  go/service-control-api/internal/webui/static/app.css
git commit -m "fix: separate manifest flow stages"
git push origin geon
```
