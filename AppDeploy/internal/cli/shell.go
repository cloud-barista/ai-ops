package cli

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"text/tabwriter"

	"github.com/khu/ai-app-deployer/internal/model"
)

var errExit = errors.New("exit requested")

const (
	defaultCLIMaxPackageUpload = int64(50 << 20)
	maximumCLIMaxPackageUpload = int64(1 << 30)
)

type Shell struct {
	api              http.Handler
	input            io.Reader
	reader           *bufio.Reader
	out              io.Writer
	color            bool
	maxPackageUpload int64
	interactive      bool
	readSecret       func(string) ([]byte, error)
}

type response struct {
	status int
	body   []byte
}

type apiError struct {
	status    int
	requestID string
	code      string
	message   string
}

func (e *apiError) Error() string {
	if e.requestID != "" {
		return fmt.Sprintf("[%s] %s (request_id: %s)", e.code, e.message, e.requestID)
	}
	return fmt.Sprintf("[%s] %s", e.code, e.message)
}

func New(api http.Handler, input io.Reader, output io.Writer) *Shell {
	return NewWithPackageLimit(api, input, output, defaultCLIMaxPackageUpload)
}

func NewWithPackageLimit(api http.Handler, input io.Reader, output io.Writer, maxPackageUpload int64) *Shell {
	if maxPackageUpload <= 0 {
		maxPackageUpload = defaultCLIMaxPackageUpload
	}
	if maxPackageUpload > maximumCLIMaxPackageUpload {
		maxPackageUpload = maximumCLIMaxPackageUpload
	}
	shell := &Shell{
		api:              api,
		input:            input,
		reader:           bufio.NewReader(input),
		out:              output,
		color:            supportsColor(output),
		maxPackageUpload: maxPackageUpload,
	}
	shell.readSecret = shell.readTerminalSecret
	return shell
}

// Run starts the interactive shell when args is empty, or executes one command
// when args is supplied.
func (s *Shell) Run(ctx context.Context, args []string) error {
	s.interactive = len(args) == 0
	if len(args) > 0 {
		if err := s.execute(ctx, args); errors.Is(err, errExit) {
			return nil
		} else {
			return err
		}
	}

	s.printBanner()
	if err := s.overview(ctx); err != nil {
		s.warning("현재 상태를 불러오지 못했습니다: " + err.Error())
	}
	for {
		fmt.Fprintf(s.out, "\n%s ", s.paint(ansiBold+ansiBlue, "appdeployer ❯"))
		line, err := s.reader.ReadString('\n')
		if err != nil && !errors.Is(err, io.EOF) {
			return fmt.Errorf("read command: %w", err)
		}
		line = strings.TrimSpace(line)
		if line != "" {
			args, parseErr := tokenize(line)
			if parseErr != nil {
				s.printError(parseErr)
			} else if commandErr := s.execute(ctx, args); commandErr != nil {
				if errors.Is(commandErr, errExit) {
					s.success("CLI를 종료합니다.")
					return nil
				}
				s.printError(commandErr)
			}
		}
		if errors.Is(err, io.EOF) {
			fmt.Fprintln(s.out)
			return nil
		}
	}
}

func (s *Shell) printBanner() {
	fmt.Fprintln(s.out, s.paint(ansiBlue, "╭──────────────────────────────────────────────────────────╮"))
	fmt.Fprintf(s.out, "%s  %s\n", s.paint(ansiBlue, "│"), s.paint(ansiBold, "AI APP DEPLOYER"))
	fmt.Fprintf(s.out, "%s  %s\n", s.paint(ansiBlue, "│"), s.paint(ansiDim, "Register · Validate · Deploy · Observe"))
	fmt.Fprintln(s.out, s.paint(ansiBlue, "╰──────────────────────────────────────────────────────────╯"))
	fmt.Fprintf(s.out, "%s  %s  %s\n", s.paint(ansiGray, "빠른 명령"), s.paint(ansiCyan, "help"), s.paint(ansiDim, "전체 명령 · home 현재 상태 · exit 종료"))
}

func (s *Shell) execute(ctx context.Context, args []string) error {
	if len(args) == 0 {
		return nil
	}
	switch strings.ToLower(args[0]) {
	case "help", "?", "--help", "-h":
		s.printHelp()
		return nil
	case "exit", "quit", "q":
		return errExit
	case "clear", "cls":
		if s.color {
			fmt.Fprint(s.out, "\033[H\033[2J")
		} else {
			fmt.Fprintln(s.out, strings.Repeat("\n", 3))
		}
		return nil
	case "status", "home", "overview":
		return s.overview(ctx)
	case "apps", "app":
		return s.apps(ctx, args[1:])
	case "packages", "package", "pkg":
		return s.packages(ctx, args[1:])
	case "runtimes", "runtime":
		return s.runtimes(ctx, args[1:])
	case "targets", "target":
		return s.targets(ctx, args[1:])
	case "credentials", "credential", "creds", "cred":
		return s.credentials(ctx, args[1:])
	case "resources", "resource":
		return s.resources(ctx, args[1:])
	case "deployments", "deployment", "deploy":
		return s.deployments(ctx, args[1:])
	case "monitoring", "monitor":
		return s.monitoring(ctx, args[1:])
	case "inference", "infer":
		return s.inference(ctx, args[1:])
	case "metrics", "metric":
		return s.metrics(ctx, args[1:])
	case "raw":
		return s.raw(ctx, args[1:])
	default:
		return fmt.Errorf("알 수 없는 명령 %q입니다. help로 명령 목록을 확인하세요", args[0])
	}
}

func (s *Shell) call(ctx context.Context, method, path string, payload any) (response, error) {
	var body io.Reader
	if payload != nil {
		var raw []byte
		var err error
		switch value := payload.(type) {
		case []byte:
			raw = value
		case json.RawMessage:
			raw = value
		default:
			raw, err = json.Marshal(payload)
			if err != nil {
				return response{}, fmt.Errorf("encode request: %w", err)
			}
		}
		body = bytes.NewReader(raw)
	}
	return s.callBody(ctx, method, path, body, "application/json")
}

func (s *Shell) callBody(ctx context.Context, method, path string, body io.Reader, contentType string) (response, error) {
	req := httptest.NewRequest(method, path, body).WithContext(ctx)
	// Credential APIs are loopback-only by default. CLI requests are in-process,
	// so make their trusted local origin explicit instead of relying on httptest's
	// synthetic example.com address.
	req.RemoteAddr = "127.0.0.1:0"
	req.Host = "127.0.0.1"
	if contentType != "" {
		req.Header.Set("Content-Type", contentType)
	}
	rec := httptest.NewRecorder()
	s.api.ServeHTTP(rec, req)
	result := response{status: rec.Code, body: rec.Body.Bytes()}
	if rec.Code < http.StatusOK || rec.Code >= http.StatusMultipleChoices {
		var apiResponse model.ErrorResponse
		if err := json.Unmarshal(result.body, &apiResponse); err == nil && apiResponse.Error.Code != "" {
			return result, &apiError{
				status:    rec.Code,
				requestID: apiResponse.RequestID,
				code:      apiResponse.Error.Code,
				message:   apiResponse.Error.Message,
			}
		}
		return result, fmt.Errorf("API 요청 실패: HTTP %d", rec.Code)
	}
	return result, nil
}

func (s *Shell) showJSON(ctx context.Context, method, path string, payload any) error {
	resp, err := s.call(ctx, method, path, payload)
	if err != nil {
		return err
	}
	return s.printJSON(resp.body)
}

func (s *Shell) printJSON(raw []byte) error {
	var value any
	if err := json.Unmarshal(raw, &value); err != nil {
		return fmt.Errorf("decode response: %w", err)
	}
	formatted, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return fmt.Errorf("format response: %w", err)
	}
	fmt.Fprintln(s.out, s.colorizeJSON(string(formatted)))
	return nil
}

func (s *Shell) printHelp() {
	s.section("기본")
	s.helpLine("home · status", "등록 수와 실행 중인 배포 요약")
	s.helpLine("help", "이 도움말 표시")
	s.helpLine("clear", "화면 정리")
	s.helpLine("exit", "CLI 종료")

	s.section("등록 및 실행 환경")
	s.helpLine("packages build [options]", "유형을 선택해 배포 package 생성")
	s.helpLine("packages deploy [options]", "package 생성·App 등록·자원 점검·배포")
	s.helpLine("apps list | get <app-id> | add [json]", "App 조회·등록")
	s.helpLine("apps delete <app-id> --yes", "참조 배포가 없거나 모두 STOPPED인 App 등록 삭제")
	s.helpLine("runtimes list | add [json]", "Runtime Profile 조회·등록")
	s.helpLine("runtimes delete <runtime-id> --yes", "참조 배포가 없거나 모두 STOPPED인 Runtime Profile 삭제")
	s.helpLine("targets list | add [json]", "Target Profile 조회·등록")
	s.helpLine("targets delete <target-id> --yes", "참조 배포가 없거나 모두 STOPPED인 Target Profile과 readiness inventory 삭제")
	s.helpLine("credentials list | add [options]", "host key fingerprint를 고정한 메모리 전용 SSH Credential 조회·등록")
	s.helpLine("credentials delete <id> --yes", "메모리 전용 SSH Credential 삭제")
	s.helpLine("resources list | check <target-id> [runtime-id]", "자원 준비 상태 확인")

	s.section("배포")
	s.helpLine("deployments list | get <id> | create [...]", "배포 생성·상태 조회")
	s.helpLine("deploy package [options]", "packages deploy와 동일한 안내형 배포")
	s.helpLine("deployments logs <id> [stage]", "배포 이벤트 로그")
	s.helpLine("deployments stop <id>", "실행 중인 배포 중지")

	s.section("관측 및 추론")
	s.helpLine("monitoring summary | health | alarms | metrics", "운영 상태 조회")
	s.helpLine("inference health <id> | invoke <id> [json]", "배포 앱 호출")
	s.helpLine("metrics list [id] | add <id> [json]", "추론 메트릭 조회·기록")
	s.helpLine("raw <GET|POST> </api/v1/path> [...]", "API 직접 호출")

	fmt.Fprintf(s.out, "\n%s\n", s.paint(ansiDim, "JSON 파일을 생략하면 안내형 입력이 시작됩니다."))
	fmt.Fprintf(s.out, "%s\n", s.paint(ansiDim, "Credential은 프로세스 메모리에만 존재합니다. 단발 add는 명령 종료와 함께 사라지며, 후속 배포에는 대화형 CLI를 사용하세요."))
	fmt.Fprintf(s.out, "%s %s\n", s.paint(ansiGray, "예시"), s.paint(ansiCyan, "apps add examples/requests/app-cpu-script.json"))
	fmt.Fprintf(s.out, "     %s\n", s.paint(ansiCyan, "credentials add --id cpu-vm-001 --user ubuntu --host-key-fingerprint SHA256:<base64> --auth private_key --private-key-file ./cpu-vm.pem"))
	fmt.Fprintf(s.out, "     %s\n", s.paint(ansiCyan, "packages deploy --type script --source ./run.sh --runtime-id rt-cpu-001 --target-id target-cpu-001"))
	fmt.Fprintf(s.out, "     %s\n", s.paint(ansiCyan, "deployments create appver-... rt-cpu-001 target-cpu-001"))
}

func (s *Shell) helpLine(command, description string) {
	fmt.Fprintf(s.out, "  %s\n", s.paint(ansiCyan, command))
	fmt.Fprintf(s.out, "    %s\n", s.paint(ansiDim, description))
}

func (s *Shell) printError(err error) {
	var apiErr *apiError
	if errors.As(err, &apiErr) {
		fmt.Fprintf(s.out, "\n%s %s\n", s.paint(ansiRed, "✕"), s.paint(ansiBold+ansiRed, apiErr.code))
		if full, leaf := err.Error(), apiErr.Error(); strings.HasSuffix(full, ": "+leaf) {
			if context := strings.TrimSpace(strings.TrimSuffix(full, ": "+leaf)); context != "" {
				fmt.Fprintf(s.out, "  %s\n", context)
			}
		}
		fmt.Fprintf(s.out, "  %s\n", apiErr.message)
		if apiErr.requestID != "" {
			fmt.Fprintf(s.out, "  %s %s\n", s.paint(ansiGray, "request_id"), s.paint(ansiDim, apiErr.requestID))
		}
		return
	}
	fmt.Fprintf(s.out, "\n%s %s\n", s.paint(ansiRed, "✕"), err.Error())
}

func (s *Shell) overview(ctx context.Context) error {
	type listEnvelope struct {
		Items []json.RawMessage `json:"items"`
	}
	var readiness model.ReadinessResponse
	var apps, runtimes, targets listEnvelope
	var deployments struct {
		Items []model.DeploymentResponse `json:"items"`
	}
	requests := []struct {
		path string
		out  any
	}{
		{path: "/api/v1/readiness", out: &readiness},
		{path: "/api/v1/apps", out: &apps},
		{path: "/api/v1/runtime-profiles", out: &runtimes},
		{path: "/api/v1/target-profiles", out: &targets},
		{path: "/api/v1/deployments", out: &deployments},
	}
	for _, request := range requests {
		resp, err := s.call(ctx, http.MethodGet, request.path, nil)
		if err != nil {
			return err
		}
		if err := json.Unmarshal(resp.body, request.out); err != nil {
			return fmt.Errorf("decode overview response: %w", err)
		}
	}
	var running, failed int
	for _, deployment := range deployments.Items {
		if deployment.Status == model.StatusRunning {
			running++
		}
		if strings.Contains(deployment.Status, "FAILED") {
			failed++
		}
	}
	s.section("현재 상태")
	var output bytes.Buffer
	w := tabwriter.NewWriter(&output, 0, 4, 2, ' ', 0)
	fmt.Fprintf(w, "  API\t%s\t  등록 앱\t%d\t  실행 중\t%d\n", strings.ToUpper(readiness.Status), len(apps.Items), running)
	fmt.Fprintf(w, "  Runtime\t%d\t  Target\t%d\t  실패\t%d\n", len(runtimes.Items), len(targets.Items), failed)
	if err := w.Flush(); err != nil {
		return fmt.Errorf("write overview: %w", err)
	}
	formatted := output.String()
	if s.color {
		formatted = strings.Replace(formatted, strings.ToUpper(readiness.Status), s.paint(ansiGreen, strings.ToUpper(readiness.Status)), 1)
	}
	fmt.Fprint(s.out, formatted)
	return nil
}

func tokenize(line string) ([]string, error) {
	var args []string
	var current strings.Builder
	var quote rune
	runes := []rune(line)
	for i := 0; i < len(runes); i++ {
		r := runes[i]
		if quote != 0 {
			if r == quote {
				quote = 0
				continue
			}
			if r == '\\' && i+1 < len(runes) && runes[i+1] == quote {
				current.WriteRune(quote)
				i++
				continue
			}
			current.WriteRune(r)
			continue
		}
		switch r {
		case '\'', '"':
			quote = r
		case ' ', '\t':
			if current.Len() > 0 {
				args = append(args, current.String())
				current.Reset()
			}
		default:
			current.WriteRune(r)
		}
	}
	if quote != 0 {
		return nil, errors.New("따옴표가 닫히지 않았습니다")
	}
	if current.Len() > 0 {
		args = append(args, current.String())
	}
	return args, nil
}
