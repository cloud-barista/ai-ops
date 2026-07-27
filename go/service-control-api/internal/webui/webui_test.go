package webui

import (
	"net/http"
	"net/http/httptest"
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
		{path: "/assets/app.css", contentType: "text/css", contains: ":root"},
		{path: "/assets/manifest_stages.js", contentType: "text/javascript", contains: "buildManifestStageViewModel"},
		{path: "/assets/app.js", contentType: "text/javascript", contains: "loadAgents"},
	}

	for _, test := range tests {
		t.Run(test.path, func(t *testing.T) {
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

func TestControlAppContainsOperationalViewsAndAPIContracts(t *testing.T) {
	server := echo.New()
	Register(server)

	html := requestBody(t, server, "/")
	for _, expected := range []string{
		`data-view="overview"`,
		`data-view="planner"`,
		`data-view="agents"`,
		`data-view="feedback"`,
		`id="planner-form"`,
		`id="agent-registration-form"`,
		`id="action-form"`,
		`id="feedback-form"`,
		`<option value="succeeded">succeeded</option>`,
		`aria-label="Agents &amp; Guard"`,
		`data-close-dialog`,
	} {
		if !strings.Contains(html, expected) {
			t.Fatalf("expected dashboard HTML to contain %q", expected)
		}
	}

	javascript := requestBody(t, server, "/assets/app.js")
	for _, expected := range []string{
		`/healthz`,
		`/api/v1/agents`,
		`/api/v1/planner/deployments`,
		`/api/v1/automation/action-proposals`,
		`/api/v1/automation/feedback`,
		`[data-close-dialog]`,
	} {
		if !strings.Contains(javascript, expected) {
			t.Fatalf("expected dashboard JavaScript to contain API route %q", expected)
		}
	}
}

func TestControlAppContainsAutonomyView(t *testing.T) {
	server := echo.New()
	Register(server)

	html := requestBody(t, server, "/")
	for _, expected := range []string{
		`data-view="autonomy"`, `id="autonomy-form"`, `name="mode"`,
		`id="autonomy-start"`, `id="autonomy-stop"`, `id="autonomy-emergency-stop"`, `id="autonomy-run-cycle"`,
		`id="autonomy-latency"`, `id="autonomy-throughput"`, `id="autonomy-error-rate"`,
		`id="autonomy-qwen-action"`, `id="autonomy-guard-status"`, `id="autonomy-execution-status"`,
		`id="autonomy-timeline"`,
	} {
		if !strings.Contains(html, expected) {
			t.Fatalf("expected autonomy HTML contract %q", expected)
		}
	}

	javascript := requestBody(t, server, "/assets/app.js")
	for _, expected := range []string{
		`/api/v1/autonomy/status`, `/api/v1/autonomy/config`, `/api/v1/autonomy/start`,
		`/api/v1/autonomy/stop`, `/api/v1/autonomy/emergency-stop`, `/api/v1/autonomy/cycles`, `/api/v1/autonomy/events`,
	} {
		if !strings.Contains(javascript, expected) {
			t.Fatalf("expected autonomy JavaScript route %q", expected)
		}
	}
}

func TestControlAppContainsControlRunManifestWorkflow(t *testing.T) {
	server := echo.New()
	Register(server)

	html := requestBody(t, server, "/")
	for _, expected := range []string{
		`Generate Manifest`,
		`Submit to AppDeploy`,
		`Request Guard`,
		`Agent Registry`,
		`Qwen Planner`,
		`Manifest Guard`,
		`배포 후 자율 운영 실험`,
		`id="control-run-list"`,
		`id="control-run-timeline"`,
		`id="planner-submit"`,
		`name="run_id"`,
		`id="selected-planner-agent"`,
	} {
		if !strings.Contains(html, expected) {
			t.Fatalf("expected ControlRun HTML contract %q", expected)
		}
	}

	javascript := requestBody(t, server, "/assets/app.js")
	for _, expected := range []string{
		`controlRuns: "/api/v1/control-runs"`,
		`loadControlRuns`,
		`renderControlRuns`,
		`data-run-id`,
		`/submit`,
		`method: "DELETE"`,
	} {
		if !strings.Contains(javascript, expected) {
			t.Fatalf("expected ControlRun JavaScript contract %q", expected)
		}
	}

	stylesheet := requestBody(t, server, "/assets/app.css")
	if !strings.Contains(stylesheet, `.result-empty[hidden]`) {
		t.Fatal("expected stylesheet to preserve the result placeholder hidden state")
	}
}

func TestControlAppContainsOrderedExperimentGuide(t *testing.T) {
	server := echo.New()
	Register(server)

	html := requestBody(t, server, "/")
	for _, expected := range []string{
		`id="experiment-guide-disclosure"`,
		`EXPERIMENT GUIDE`,
		`핵심 Manifest 실험`,
		`선택적 외부 배포`,
		`선택적 배포 후 실험`,
		`data-guide-step="registry"`,
		`data-guide-step="manifest"`,
		`data-guide-step="deploy"`,
		`data-guide-step="operate"`,
		`data-guide-step="feedback"`,
		`MANIFEST_APPROVED`,
		`DeploymentManifest`,
	} {
		if !strings.Contains(html, expected) {
			t.Fatalf("expected experiment guide HTML contract %q", expected)
		}
	}

}

func TestControlAppStartsWithManifestWorkflow(t *testing.T) {
	server := echo.New()
	Register(server)

	html := requestBody(t, server, "/")
	plannerNav := strings.Index(html, `data-view-target="planner"`)
	agentsNav := strings.Index(html, `data-view-target="agents"`)
	autonomyNav := strings.Index(html, `data-view-target="autonomy"`)
	feedbackNav := strings.Index(html, `data-view-target="feedback"`)
	guideNav := strings.Index(html, `data-view-target="overview"`)

	if !(plannerNav < agentsNav && agentsNav < autonomyNav &&
		autonomyNav < feedbackNav && feedbackNav < guideNav) {
		t.Fatalf("unexpected user workflow navigation order")
	}
	if !strings.Contains(html, `<section class="view is-active" data-view="planner">`) {
		t.Fatal("Manifest Workflow must be the default view")
	}
	if !strings.Contains(html, `<section class="view" data-view="overview" hidden>`) {
		t.Fatal("Overview must be a separate Guide view")
	}
	for _, expected := range []string{
		`<p class="eyebrow" id="view-eyebrow">USER REQUEST TO GUARDED MANIFEST</p>`,
		`<h1 id="view-title">Manifest Workflow</h1>`,
	} {
		if !strings.Contains(html, expected) {
			t.Fatalf("expected initial Manifest Workflow heading %q", expected)
		}
	}

	javascript := requestBody(t, server, "/assets/app.js")
	initializeStart := strings.Index(javascript, `async function initialize()`)
	if initializeStart == -1 {
		t.Fatal("expected Control App initializer")
	}
	initialize := javascript[initializeStart:]
	activateView := strings.Index(initialize, `switchView(state.activeView);`)
	refreshDashboard := strings.Index(initialize, `await refreshDashboard();`)
	if activateView == -1 || refreshDashboard == -1 || activateView > refreshDashboard {
		t.Fatal("initializer must activate Manifest Workflow before the first dashboard render")
	}
}

func TestControlAppShowsManifestStagesInExecutionOrder(t *testing.T) {
	server := echo.New()
	Register(server)

	html := requestBody(t, server, "/")
	if !strings.Contains(html, `id="manifest-stage-flow"`) {
		t.Fatal("expected Manifest stage flow container")
	}

	javascript := requestBody(t, server, "/assets/manifest_stages.js")
	lastIndex := -1
	for _, stage := range []string{
		"user_request",
		"request_guard",
		"agent_registry",
		"agent_dispatch",
		"qwen_planner",
		"manifest_guard",
	} {
		index := strings.Index(javascript, stage)
		if index == -1 {
			t.Fatalf("expected Manifest stage %q in JavaScript", stage)
		}
		if index <= lastIndex {
			t.Fatalf("expected Manifest stage %q after the previous execution stage", stage)
		}
		lastIndex = index
	}
}

func TestControlAppRendersManifestStagesFromSelectedControlRun(t *testing.T) {
	server := echo.New()
	Register(server)

	html := requestBody(t, server, "/")
	stageModule := strings.Index(html, `<script src="/assets/manifest_stages.js"></script>`)
	appModule := strings.Index(html, `<script src="/assets/app.js"></script>`)
	if stageModule == -1 || appModule == -1 || stageModule > appModule {
		t.Fatal("Manifest stage mapper must load before the Control App")
	}

	mapper := requestBody(t, server, "/assets/manifest_stages.js")
	if !strings.Contains(mapper, `buildManifestStageViewModel`) {
		t.Fatal("expected embedded Manifest stage mapper")
	}

	app := requestBody(t, server, "/assets/app.js")
	if !strings.Contains(app, `window.ManifestStages`) || !strings.Contains(app, `buildManifestStageViewModel(run)`) {
		t.Fatal("expected Control App renderer to use the embedded Manifest stage mapper")
	}
}

func TestControlAppContainsSimplifiedOverview(t *testing.T) {
	server := echo.New()
	Register(server)

	html := requestBody(t, server, "/")
	overviewStart := strings.Index(html, `<section class="view" data-view="overview" hidden>`)
	if overviewStart == -1 {
		t.Fatal("expected Overview view")
	}
	plannerStart := strings.Index(html, `<section class="view is-active" data-view="planner">`)
	if plannerStart == -1 || plannerStart >= overviewStart {
		t.Fatal("expected active Planner view before Overview")
	}
	overviewEnd := strings.Index(html[overviewStart+1:], `<section class="view"`)
	if overviewEnd == -1 {
		t.Fatal("expected view after Overview")
	}
	overview := html[overviewStart : overviewStart+1+overviewEnd]

	for _, expected := range []string{
		`id="overview-agent-action"`,
		`id="overview-manifest-action"`,
		`id="experiment-guide-disclosure"`,
		`<summary`,
		`id="control-run-list"`,
		`id="metric-api"`,
		`id="metric-agents"`,
		`Qwen3.5`,
	} {
		if !strings.Contains(overview, expected) {
			t.Fatalf("expected simplified Overview contract %q", expected)
		}
	}

	disclosureStart := strings.Index(overview, `<details`)
	if disclosureStart == -1 {
		t.Fatal("expected experiment guide disclosure")
	}
	disclosureEnd := strings.Index(overview[disclosureStart:], `>`)
	if disclosureEnd == -1 {
		t.Fatal("expected experiment guide disclosure opening tag")
	}
	openingTag := overview[disclosureStart : disclosureStart+disclosureEnd]
	if strings.Contains(openingTag, ` open`) {
		t.Fatal("expected experiment guide disclosure to be collapsed by default")
	}

	for _, removed := range []string{
		`id="workflow-title"`,
		`id="overview-agent-list"`,
		`id="control-run-timeline"`,
		`id="metric-guard"`,
	} {
		if strings.Contains(overview, removed) {
			t.Fatalf("expected duplicate Overview element %q to be removed", removed)
		}
	}
}

func TestControlAppContainsGeonDeletionControls(t *testing.T) {
	server := echo.New()
	Register(server)

	html := requestBody(t, server, "/")
	for _, expected := range []string{
		`id="clear-history"`,
		`id="clear-autonomy-events"`,
		`id="clear-feedback-records"`,
		`id="feedback-record-list"`,
	} {
		if !strings.Contains(html, expected) {
			t.Fatalf("expected deletion HTML contract %q", expected)
		}
	}

	javascript := requestBody(t, server, "/assets/app.js")
	for _, expected := range []string{
		`data-delete-agent`,
		`deleteRuntimeAgent`,
		`clearAutonomyEvents`,
		`data-delete-event`,
		`deleteAutonomyEvent`,
		`data-delete-run`,
		`deleteControlRun`,
		`clearControlRuns`,
		`data-delete-feedback`,
		`deleteAutomationFeedback`,
		`clearAutomationFeedback`,
		`loadAutomationFeedback`,
		`method: "DELETE"`,
		`agent.source === "runtime"`,
		`window.confirm`,
	} {
		if !strings.Contains(javascript, expected) {
			t.Fatalf("expected deletion JavaScript contract %q", expected)
		}
	}
	for _, forbidden := range []string{`설정 보호`, `protected-label`} {
		if strings.Contains(javascript, forbidden) {
			t.Fatalf("configuration Agents must not render protection label %q", forbidden)
		}
	}
}

func TestControlAppContainsGuardedAgentExecution(t *testing.T) {
	server := echo.New()
	Register(server)

	html := requestBody(t, server, "/")
	for _, expected := range []string{
		`id="agent-execution-dialog"`,
		`id="agent-execution-form"`,
		`id="agent-execution-agent"`,
		`id="agent-execution-capability"`,
		`id="agent-execution-action"`,
		`id="agent-execution-input"`,
		`id="agent-execution-result"`,
		`id="agent-execution-run-id"`,
		`name="auth_token_env"`,
		`>Source</th>`,
	} {
		if !strings.Contains(html, expected) {
			t.Fatalf("expected Agent execution HTML contract %q", expected)
		}
	}

	javascript := requestBody(t, server, "/assets/app.js")
	for _, expected := range []string{
		`data-execute-agent`,
		`openAgentExecution`,
		`submitAgentExecution`,
		`/execute`,
		`auth_token_env`,
		`loadControlRuns`,
	} {
		if !strings.Contains(javascript, expected) {
			t.Fatalf("expected Agent execution JavaScript contract %q", expected)
		}
	}

	stylesheet := requestBody(t, server, "/assets/app.css")
	buttonStart := strings.Index(stylesheet, ".button {")
	if buttonStart < 0 {
		t.Fatal("expected shared button style")
	}
	buttonEnd := strings.Index(stylesheet[buttonStart:], "}")
	if buttonEnd < 0 ||
		!strings.Contains(stylesheet[buttonStart:buttonStart+buttonEnd], "white-space: nowrap;") {
		t.Fatal("shared button labels must not wrap on mobile")
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
