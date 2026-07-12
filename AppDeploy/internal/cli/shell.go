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

	"github.com/khu/ai-app-deployer/internal/model"
)

var errExit = errors.New("exit requested")

type Shell struct {
	api    http.Handler
	reader *bufio.Reader
	out    io.Writer
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
	return &Shell{api: api, reader: bufio.NewReader(input), out: output}
}

// Run starts the interactive shell when args is empty, or executes one command
// when args is supplied.
func (s *Shell) Run(ctx context.Context, args []string) error {
	if len(args) > 0 {
		if err := s.execute(ctx, args); errors.Is(err, errExit) {
			return nil
		} else {
			return err
		}
	}

	s.printBanner()
	for {
		fmt.Fprint(s.out, "appdeployer> ")
		line, err := s.reader.ReadString('\n')
		if err != nil && !errors.Is(err, io.EOF) {
			return fmt.Errorf("read command: %w", err)
		}
		line = strings.TrimSpace(line)
		if line != "" {
			args, parseErr := tokenize(line)
			if parseErr != nil {
				fmt.Fprintf(s.out, "오류: %v\n", parseErr)
			} else if commandErr := s.execute(ctx, args); commandErr != nil {
				if errors.Is(commandErr, errExit) {
					fmt.Fprintln(s.out, "종료합니다.")
					return nil
				}
				fmt.Fprintf(s.out, "오류: %v\n", commandErr)
			}
		}
		if errors.Is(err, io.EOF) {
			fmt.Fprintln(s.out)
			return nil
		}
	}
}

func (s *Shell) printBanner() {
	fmt.Fprintln(s.out, "AI App Deployer CLI")
	fmt.Fprintln(s.out, "앱 등록부터 배포·모니터링·추론까지 이 터미널에서 실행할 수 있습니다.")
	fmt.Fprintln(s.out, "명령 목록은 help, 종료는 exit를 입력하세요.")
	fmt.Fprintln(s.out)
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
		fmt.Fprint(s.out, "\033[H\033[2J")
		return nil
	case "status":
		return s.showJSON(ctx, http.MethodGet, "/api/v1/readiness", nil)
	case "apps", "app":
		return s.apps(ctx, args[1:])
	case "runtimes", "runtime":
		return s.runtimes(ctx, args[1:])
	case "targets", "target":
		return s.targets(ctx, args[1:])
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
	req := httptest.NewRequest(method, path, body).WithContext(ctx)
	req.Header.Set("Content-Type", "application/json")
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
	fmt.Fprintln(s.out, string(formatted))
	return nil
}

func (s *Shell) printHelp() {
	fmt.Fprintln(s.out, `사용법:
  status                                      서버 준비 상태
  apps list | get <app-id> | add [json-file]  앱 조회/등록
  runtimes list | add [json-file]              런타임 프로필 조회/등록
  targets list | add [json-file]               대상 프로필 조회/등록
  resources list | check <target-id> [runtime-id]
  deployments list | get <id> | create [app-version-id runtime-id target-id]
  deployments logs <id> [stage] | stop <id>
  monitoring summary | health | alarms | metrics
  inference health <deployment-id> | invoke <deployment-id> [json-file]
  metrics list [deployment-id] | add <deployment-id> [json-file]
  raw <GET|POST> </api/v1/path> [@json-file|inline-json]
  clear | help | exit

등록 명령에서 JSON 파일을 생략하면 안내형 입력이 시작됩니다.
실행 예:
  apps add examples/requests/app-cpu-script.json
  deployments create appver-... rt-cpu-001 target-cpu-001
  deployments logs dep-... RUNNING`)
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
