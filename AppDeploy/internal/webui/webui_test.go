package webui

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/labstack/echo/v4"
)

func TestWebConsoleRoutes(t *testing.T) {
	e := echo.New()
	Register(e)

	tests := []struct {
		path        string
		contentType string
		contains    string
	}{
		{path: "/", contentType: "text/html", contains: "AI App Deployer"},
		{path: "/console", contentType: "text/html", contains: "view-dashboard"},
		{path: "/web/app.css", contentType: "text/css", contains: ".app-shell"},
		{path: "/web/app.js", contentType: "application/javascript", contains: "refreshAll"},
	}

	for _, test := range tests {
		t.Run(test.path, func(t *testing.T) {
			req := httptest.NewRequest(http.MethodGet, test.path, nil)
			rec := httptest.NewRecorder()
			e.ServeHTTP(rec, req)
			if rec.Code != http.StatusOK {
				t.Fatalf("status = %d, want %d", rec.Code, http.StatusOK)
			}
			if !strings.HasPrefix(rec.Header().Get("Content-Type"), test.contentType) {
				t.Fatalf("content type = %q, want prefix %q", rec.Header().Get("Content-Type"), test.contentType)
			}
			if !strings.Contains(rec.Body.String(), test.contains) {
				t.Fatalf("body does not contain %q", test.contains)
			}
			if rec.Header().Get("Content-Security-Policy") == "" {
				t.Fatal("Content-Security-Policy header is missing")
			}
			if rec.Header().Get("Cache-Control") != "no-cache" {
				t.Fatalf("cache control = %q, want no-cache", rec.Header().Get("Cache-Control"))
			}
		})
	}
}

func TestWebFormRetainsCurrentTargetBeforeAsyncRequest(t *testing.T) {
	raw, err := assets.ReadFile("static/app.js")
	if err != nil {
		t.Fatal(err)
	}
	script := string(raw)
	if !strings.Contains(script, "const formElement = event.currentTarget") {
		t.Fatal("submitForm must retain the form before awaiting the API request")
	}
	if strings.Contains(script, "closeDialog(event.currentTarget") || strings.Contains(script, "event.currentTarget.reset()") {
		t.Fatal("submitForm uses an expired event.currentTarget after awaiting the API request")
	}
	if !strings.Contains(script, "void refreshAll()") {
		t.Fatal("post-create refresh should not block successful form completion")
	}
}

func TestWebConsoleKeepsInteractionResponsiveDuringBackgroundRefresh(t *testing.T) {
	scriptRaw, err := assets.ReadFile("static/app.js")
	if err != nil {
		t.Fatal(err)
	}
	styleRaw, err := assets.ReadFile("static/app.css")
	if err != nil {
		t.Fatal(err)
	}
	script := string(scriptRaw)
	style := string(styleRaw)

	for _, expected := range []string{
		"let refreshInFlight = null",
		"if (refreshInFlight) return refreshInFlight",
		"loadCredentials(),",
		"await yieldForPaint()",
		`return !document.hidden && !$("dialog[open]") && !editing`,
		"window.setInterval(autoRefresh, 30000)",
		`document.addEventListener("visibilitychange", autoRefresh)`,
	} {
		if !strings.Contains(script, expected) {
			t.Fatalf("responsive web refresh does not contain %q", expected)
		}
	}
	if strings.Contains(style, "backdrop-filter") {
		t.Fatal("full-screen backdrop blur must not delay modal input rendering")
	}

	start := strings.Index(script, "async function runFullRefresh")
	end := strings.Index(script, "function refreshAll")
	if start < 0 || end <= start {
		t.Fatal("full refresh function bounds not found")
	}
	if strings.Contains(script[start:end], "refreshCredentials()") {
		t.Fatal("full refresh must not render credentials a second time")
	}
}

func TestWebFormsIncludeInlineFieldGuidance(t *testing.T) {
	raw, err := assets.ReadFile("static/app.js")
	if err != nil {
		t.Fatal(err)
	}
	script := string(raw)
	for _, expected := range []string{
		"fieldHelpDefinitions",
		"installFieldHelp()",
		"비밀번호나 키 자체가 아닌 자격증명 참조값",
		"Runtime이 gpu이면 반드시 1 이상",
		"반드시 /로 시작",
	} {
		if !strings.Contains(script, expected) {
			t.Fatalf("web field guidance does not contain %q", expected)
		}
	}
}

func TestWebConsoleIncludesOperationalScenarios(t *testing.T) {
	raw, err := assets.ReadFile("static/index.html")
	if err != nil {
		t.Fatal(err)
	}
	page := string(raw)
	for _, expected := range []string{
		`data-view="guide"`,
		`id="view-guide"`,
		"Mock으로 전체 기능 빠르게 시험",
		"CPU VM에 애플리케이션 배포",
		"GPU VM에 모델 서버 배포 및 추론",
		"AIAPP_CREDENTIAL_CRED_LOCAL_CPU_VM_001_SSH_KEY_PATH",
		"AIAPP_CREDENTIAL_CRED_LOCAL_GPU_VM_001_SSH_KEY_PATH",
		"실패했을 때 확인할 곳",
		"RESOURCE_INSUFFICIENT",
		"정상 배포 상태 흐름",
	} {
		if !strings.Contains(page, expected) {
			t.Fatalf("web operation guide does not contain %q", expected)
		}
	}
}

func TestWebConsoleCanSelectAndBuildPackageTypes(t *testing.T) {
	pageRaw, err := assets.ReadFile("static/index.html")
	if err != nil {
		t.Fatal(err)
	}
	scriptRaw, err := assets.ReadFile("static/app.js")
	if err != nil {
		t.Fatal(err)
	}
	page := string(pageRaw)
	script := string(scriptRaw)
	for _, expected := range []string{
		`id="package-build-button"`,
		`id="package-type"`,
		`id="package-source"`,
		`value="go"`,
		`value="python"`,
		`value="node"`,
		`value="binary"`,
		`value="script"`,
		`name="healthcheck_path"`,
		"패키지 생성 및 입력",
	} {
		if !strings.Contains(page, expected) {
			t.Fatalf("package builder UI does not contain %q", expected)
		}
	}
	for _, expected := range []string{
		`api("/api/v1/artifacts/packages"`,
		"new FormData()",
		`request.append("source", source)`,
		"updatePackageBuilder()",
		"populateAppForm(result.app_spec)",
		`form.get("healthcheck_path")`,
	} {
		if !strings.Contains(script, expected) {
			t.Fatalf("package builder script does not contain %q", expected)
		}
	}
}

func TestWebPackageBuilderDoesNotBlockDirectAppRegistration(t *testing.T) {
	pageRaw, err := assets.ReadFile("static/index.html")
	if err != nil {
		t.Fatal(err)
	}
	scriptRaw, err := assets.ReadFile("static/app.js")
	if err != nil {
		t.Fatal(err)
	}
	page := string(pageRaw)
	script := string(scriptRaw)

	for _, expected := range []string{
		`id="package-build-form" hidden`,
		`id="package-type" form="package-build-form"`,
		`id="package-source" type="file" form="package-build-form"`,
		`id="package-entrypoint" form="package-build-form"`,
		"Mock 등은 아래 등록값을 직접 입력할 수 있습니다.",
	} {
		if !strings.Contains(page, expected) {
			t.Fatalf("package builder isolation does not contain %q", expected)
		}
	}
	if !strings.Contains(script, `$("#package-build-form").reset()`) {
		t.Fatal("package builder form must reset independently after App registration")
	}
	for _, expected := range []string{
		"source.required = usesUpload",
		"entrypoint.required = usesUpload",
		`$("#package-source").reportValidity()`,
		`$("#package-entrypoint").reportValidity()`,
	} {
		if !strings.Contains(script, expected) {
			t.Fatalf("package build-only validation does not contain %q", expected)
		}
	}
}

func TestWebConsoleCanDeleteAppRegistrationWithConfirmation(t *testing.T) {
	raw, err := assets.ReadFile("static/app.js")
	if err != nil {
		t.Fatal(err)
	}
	script := string(raw)
	for _, expected := range []string{
		`"row-button danger", "등록 삭제"`,
		`remove.dataset.action = "app-delete"`,
		`remove.dataset.name = item.name`,
		`remove.dataset.version = item.version`,
		`button.dataset.action === "app-delete"`,
		"window.confirm",
		"등록 정보만 삭제됩니다. STOPPED 배포 이력과 Artifact·배포 파일은 유지되며, 그 외 상태의 배포가 참조하면 삭제가 거부됩니다.",
		"api(`/api/v1/apps/${encodeURIComponent(id)}`, { method: \"DELETE\" })",
		`toast("App 등록 삭제 완료"`,
		"void refreshAll()",
	} {
		if !strings.Contains(script, expected) {
			t.Fatalf("App delete UI does not contain %q", expected)
		}
	}
}

func TestWebDeploymentUsesTargetProfileOnly(t *testing.T) {
	pageRaw, err := assets.ReadFile("static/index.html")
	if err != nil {
		t.Fatal(err)
	}
	scriptRaw, err := assets.ReadFile("static/app.js")
	if err != nil {
		t.Fatal(err)
	}
	page, script := string(pageRaw), string(scriptRaw)
	if strings.Contains(page, `id="deployment-runtime"`) || strings.Contains(page, `id="runtime-dialog"`) {
		t.Fatal("separate runtime controls should not be exposed")
	}
	if !strings.Contains(script, `target_profile_id: form.get("target_profile_id")`) || strings.Contains(script, "runtimeProfileID") {
		t.Fatal("deployment script must submit the Target Profile without separate runtime settings")
	}
}

func TestWebTargetFormRestoresTypeStateAndShowsNativeValidationMessage(t *testing.T) {
	raw, err := assets.ReadFile("static/app.js")
	if err != nil {
		t.Fatal(err)
	}
	script := string(raw)
	for _, expected := range []string{
		`if (formElement.id === "target-form") updateTargetFormForType()`,
		`function updateTargetFormForType()`,
		`credentialRef.disabled = false`,
		`credentialRef.disabled = true`,
		`targetForm.addEventListener("invalid", event =>`,
		`event.target.validationMessage`,
		`toast("Target 입력 확인"`,
	} {
		if !strings.Contains(script, expected) {
			t.Fatalf("Target form state/validation handling does not contain %q", expected)
		}
	}
	if strings.Contains(script, `targetForm.addEventListener("invalid", event => { event.preventDefault()`) {
		t.Fatal("Target invalid handler must preserve the browser's native validation UI")
	}
}

func TestWebConsoleIncludesProcessMemoryCredentialControls(t *testing.T) {
	pageRaw, err := assets.ReadFile("static/index.html")
	if err != nil {
		t.Fatal(err)
	}
	page := string(pageRaw)
	for _, expected := range []string{
		`id="credentials-table-body"`,
		`id="credential-list-status"`,
		`data-open-dialog="credential-dialog"`,
		`id="credential-form"`,
		`name="credential_type"`,
		`id="credential-auth-type"`,
		`name="host_key_fingerprint" minlength="50" maxlength="50" pattern="SHA256:[A-Za-z0-9+/]{43}"`,
		`id="credential-private-key-file" type="file"`,
		`id="credential-private-key-passphrase" type="password" maxlength="4096" autocomplete="new-password"`,
		`id="credential-password" type="password" maxlength="4096" autocomplete="new-password"`,
		`list="credential-ref-options"`,
		`id="credential-ref-options"`,
		"현재 서버 프로세스 메모리에만 보관",
		"cred://runtime/cpu-vm-001",
		"ENV fallback",
	} {
		if !strings.Contains(page, expected) {
			t.Fatalf("Credential UI does not contain %q", expected)
		}
	}
}

func TestWebCredentialSecretsAreClearedAndNeverRendered(t *testing.T) {
	raw, err := assets.ReadFile("static/app.js")
	if err != nil {
		t.Fatal(err)
	}
	script := string(raw)
	for _, expected := range []string{
		"function clearCredentialSecrets()",
		`privateKey.value = ""`,
		`passphrase.value = ""`,
		`password.value = ""`,
		"function clearCredentialPayload(payload)",
		`payload.private_key = await file.text()`,
		`file.size > 65536`,
		`host_key_fingerprint: hostKeyFingerprint`,
		`function isValidHostKeyFingerprint(value)`,
		`/^SHA256:[A-Za-z0-9+/]{43}$/`,
		"atob(`${encoded}=`)",
		`btoa(decoded).replace(/=+$/, "") === encoded`,
		"clearCredentialSecrets();\n    const request = api(\"/api/v1/credentials\"",
		`addEventListener("change", updateCredentialAuthFields)`,
		`addEventListener("cancel", clearCredentialSecrets)`,
		`addEventListener("close", clearCredentialSecrets)`,
	} {
		if !strings.Contains(script, expected) {
			t.Fatalf("Credential Secret handling does not contain %q", expected)
		}
	}

	start := strings.Index(script, "function renderCredentials()")
	end := strings.Index(script, "function availability(value)")
	if start < 0 || end <= start {
		t.Fatal("renderCredentials function bounds not found")
	}
	renderer := script[start:end]
	for _, forbidden := range []string{"private_key", "private_key_passphrase", "password", "JSON.stringify"} {
		if strings.Contains(renderer, forbidden) {
			t.Fatalf("Credential renderer contains non-allowlisted field or generic serializer %q", forbidden)
		}
	}
}

func TestWebCredentialRefreshFailureIsIsolated(t *testing.T) {
	raw, err := assets.ReadFile("static/app.js")
	if err != nil {
		t.Fatal(err)
	}
	script := string(raw)
	for _, expected := range []string{
		"async function refreshCredentials()",
		"async function loadCredentials()",
		`credentialsError: error.message`,
		`Object.assign(state, await loadCredentials())`,
		"loadCredentials(),",
		`Credential 목록만 불러오지 못했습니다`,
		"function safeCredentialRecord(item = {})",
		`host_key_fingerprint: typeof item.host_key_fingerprint === "string"`,
		"function populateCredentialRefs()",
		`targetCredentialRef.value = result.credential_ref`,
		`credentialRef.disabled = true`,
		`credentialRef.required = true`,
	} {
		if !strings.Contains(script, expected) {
			t.Fatalf("Credential refresh/Target integration does not contain %q", expected)
		}
	}
}
