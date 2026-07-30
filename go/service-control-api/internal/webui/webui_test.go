package webui

import (
	"net/http"
	"net/http/httptest"
	"regexp"
	"strings"
	"testing"

	"github.com/labstack/echo/v4"
)

func TestRegisterServesEmbeddedControlApp(t *testing.T) {
	server := echo.New()
	Register(server)

	tests := []struct {
		path        string
		contentType string
		contains    string
	}{
		{path: "/", contentType: "text/html", contains: "geon Agent Control"},
		{path: "/", contentType: "text/html", contains: `rel="icon" href="data:,"`},
		{path: "/assets/app.css", contentType: "text/css", contains: ":root"},
		{path: "/assets/manifest_stages.js", contentType: "text/javascript", contains: "buildManifestStageViewModel"},
		{path: "/assets/app.js", contentType: "text/javascript", contains: "submitAutomationRun"},
	}

	for _, test := range tests {
		t.Run(test.path+"/"+test.contains, func(t *testing.T) {
			request := httptest.NewRequest(http.MethodGet, test.path, nil)
			response := httptest.NewRecorder()
			server.ServeHTTP(response, request)

			if response.Code != http.StatusOK {
				t.Fatalf("expected status 200, got %d body=%s", response.Code, response.Body.String())
			}
			if !strings.Contains(response.Header().Get(echo.HeaderContentType), test.contentType) {
				t.Fatalf("expected content type %q, got %q", test.contentType, response.Header().Get(echo.HeaderContentType))
			}
			if !strings.Contains(response.Body.String(), test.contains) {
				t.Fatalf("expected body to contain %q", test.contains)
			}
		})
	}
}

func TestControlAppContainsExactlyThreeResearchViews(t *testing.T) {
	server := echo.New()
	Register(server)
	html := requestBody(t, server, "/")

	for _, expected := range []string{
		`data-view-target="agent-control"`,
		`data-view-target="agents"`,
		`data-view-target="results"`,
		`<span>자동화 에이전트</span>`,
		`<span>Agent 및 정책</span>`,
		`<span>실험 결과</span>`,
		`<section class="view is-active" data-view="agent-control">`,
		`<section class="view" data-view="agents" hidden>`,
		`<section class="view" data-view="results" hidden>`,
	} {
		if !strings.Contains(html, expected) {
			t.Fatalf("missing simplified web contract %q", expected)
		}
	}

	navTargets := regexp.MustCompile(`data-view-target="[^"]+"`).FindAllString(html, -1)
	if len(navTargets) != 3 {
		t.Fatalf("primary navigation targets = %v, want exactly 3", navTargets)
	}
	views := regexp.MustCompile(`data-view="[^"]+"`).FindAllString(html, -1)
	if len(views) != 3 {
		t.Fatalf("view containers = %v, want exactly 3", views)
	}

	for _, removed := range []string{
		`data-view="overview"`,
		`data-view="planner"`,
		`data-view="autonomy"`,
		`data-view="feedback"`,
		`id="planner-form"`,
		`id="action-form"`,
		`id="autonomy-form"`,
		`id="feedback-form"`,
		`id="agent-execution-dialog"`,
		`고급 PoC 도구`,
		`Submit to AppDeploy`,
	} {
		if strings.Contains(html, removed) {
			t.Fatalf("platform-oriented web element remains %q", removed)
		}
	}
}

func TestControlAppContainsSingleAutomationWorkflow(t *testing.T) {
	server := echo.New()
	Register(server)
	html := requestBody(t, server, "/")

	for _, expected := range []string{
		`id="automation-run-form"`,
		`id="automation-input-mode-natural"`,
		`id="automation-input-mode-structured"`,
		`id="automation-request"`,
		`id="automation-app-spec-json"`,
		`id="automation-run-submit"`,
		`자동 분석 및 판단`,
		`data-agent-control-stage="requirement"`,
		`data-agent-control-stage="recommendation"`,
		`data-agent-control-stage="decision"`,
		`data-agent-control-stage="adapter"`,
		`요구사항 분석`,
		`인프라 추천`,
		`Agent 배포 판단`,
		`Adapter 전달`,
		`id="automation-analysis-mode"`,
		`id="agent-control-adapter"`,
		`id="agent-control-adapter-status"`,
		`id="automation-evidence"`,
		`id="automation-application-profile-json"`,
		`id="automation-resource-recommendation-json"`,
		`id="advanced-protocol-inputs"`,
		`id="automation-flow-form"`,
		`id="application-context-json"`,
		`id="resource-recommendation-json"`,
		`id="load-agent-control-sample"`,
		`id="run-protocol-flow"`,
		`id="agent-control-result-json"`,
		`Desired Deployment Spec`,
		`Mock simulation`,
		`External handoff ready`,
	} {
		if !strings.Contains(html, expected) {
			t.Fatalf("missing automation workflow contract %q", expected)
		}
	}

	if strings.Contains(html, `id="application-context-form"`) ||
		strings.Contains(html, `id="resource-recommendation-form"`) {
		t.Fatal("core inputs must use one joined execution form")
	}
}

func TestControlAppContainsCollapsibleExperimentGuide(t *testing.T) {
	server := echo.New()
	Register(server)
	html := requestBody(t, server, "/")

	for _, expected := range []string{
		`<details class="experiment-guide" id="experiment-guide">`,
		`실험 진행 방법`,
		`입력 방식 선택`,
		`자동 분석 및 판단 실행`,
		`판단 결과 확인`,
		`선택 실험`,
		`DesiredDeploymentSpec`,
		`Feedback`,
	} {
		if !strings.Contains(html, expected) {
			t.Fatalf("missing collapsible experiment guide contract %q", expected)
		}
	}

	if strings.Contains(html, `<details class="experiment-guide" id="experiment-guide" open>`) {
		t.Fatal("experiment guide must be collapsed by default")
	}
}

func TestControlAppContainsPolicyRegistryAndExperimentResults(t *testing.T) {
	server := echo.New()
	Register(server)
	html := requestBody(t, server, "/")

	for _, expected := range []string{
		`id="core-policy-summary"`,
		`AIApplicationAutomationAgent`,
		`ai_application_automation`,
		`generate_deployment_decision`,
		`id="agent-table-body"`,
		`id="open-agent-dialog"`,
		`id="agent-registration-form"`,
		`id="experiment-flow-list"`,
		`id="experiment-decision-summary"`,
		`id="experiment-scaling-summary"`,
		`id="experiment-flow-json"`,
		`id="clear-experiment-flows"`,
		`id="reasoning-comparison-form"`,
		`id="deployment-status-form"`,
		`id="optimization-feedback-form"`,
		`application.analysis.request`,
		`/api/v1/agent-control/application-analysis-requests`,
	} {
		if !strings.Contains(html, expected) {
			t.Fatalf("missing policy or experiment contract %q", expected)
		}
	}
}

func TestControlAppJavaScriptUsesOnlyFocusedWebAPIs(t *testing.T) {
	server := echo.New()
	Register(server)
	javascript := requestBody(t, server, "/assets/app.js")

	for _, expected := range []string{
		`agents: "/api/v1/agents"`,
		`automationRuns: "/api/v1/agent-control/automation-runs"`,
		`applicationContexts: "/api/v1/agent-control/application-contexts"`,
		`resourceRecommendations: "/api/v1/agent-control/resource-recommendations"`,
		`deploymentStatus: "/api/v1/agent-control/deployment-status"`,
		`optimizationFeedback: "/api/v1/agent-control/optimization-feedback"`,
		`agentControlFlows: "/api/v1/agent-control/flows"`,
		`buildAutomationRunPayload`,
		`submitAutomationRun`,
		`submitProtocolFlow`,
		`renderAutomationRun`,
		`renderAgentControlFlow`,
		`renderExperimentFlows`,
		`deleteAgentControlFlow`,
		`clearAgentControlFlows`,
	} {
		if !strings.Contains(javascript, expected) {
			t.Fatalf("missing focused JavaScript contract %q", expected)
		}
	}

	for _, removed := range []string{
		`/api/v1/control-runs`,
		`/api/v1/planner/deployments`,
		`/api/v1/automation/action-proposals`,
		`/api/v1/automation/feedback`,
		`/api/v1/autonomy/`,
		`submitPlanner`,
		`submitAction`,
		`startAutonomyPolling`,
		`loadFeedbackView`,
	} {
		if strings.Contains(javascript, removed) {
			t.Fatalf("legacy browser API or function remains %q", removed)
		}
	}
}

func TestControlAppUsesReadableKorean(t *testing.T) {
	server := echo.New()
	Register(server)
	html := requestBody(t, server, "/")

	for _, expected := range []string{
		"자동 분석 및 판단",
		"요구사항 분석",
		"인프라 추천",
		"Agent 배포 판단",
		"분석 방식",
		"Registry 권한",
		"Go Guard",
		"Desired Deployment Spec",
		"고급 프로토콜 검증",
		"전체 기록 삭제",
		"스케일링 판단",
	} {
		if !strings.Contains(html, expected) {
			t.Fatalf("missing readable Korean text %q", expected)
		}
	}

	for _, corrupted := range []string{
		"?ъ슜???붿껌",
		"泥섎━ ?④퀎",
		"?먮룞???먯씠?꾪듃",
		"諛고룷",
	} {
		if strings.Contains(html, corrupted) {
			t.Fatalf("unexpected corrupted text %q", corrupted)
		}
	}
}

func TestControlAppResponsiveStylesProtectFixedWorkflowElements(t *testing.T) {
	server := echo.New()
	Register(server)
	stylesheet := requestBody(t, server, "/assets/app.css")

	for _, expected := range []string{
		`.input-mode-switch`,
		`.automation-primary-input`,
		`.automation-evidence-grid`,
		`.automation-input-grid`,
		`.results-layout`,
		`.agent-control-stage-flow`,
		`.experiment-guide`,
		`.experiment-guide-steps`,
		`.table-wrap`,
		`grid-template-columns: repeat(3, minmax(0, 1fr));`,
		`grid-template-columns: repeat(4, minmax(0, 1fr));`,
		`@media (max-width: 900px)`,
		`grid-template-columns: minmax(0, 1fr);`,
		`min-width: 0;`,
	} {
		if !strings.Contains(stylesheet, expected) {
			t.Fatalf("missing responsive style contract %q", expected)
		}
	}
}

func requestBody(t *testing.T, server *echo.Echo, path string) string {
	t.Helper()
	request := httptest.NewRequest(http.MethodGet, path, nil)
	response := httptest.NewRecorder()
	server.ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("expected status 200 for %s, got %d body=%s", path, response.Code, response.Body.String())
	}
	return response.Body.String()
}
