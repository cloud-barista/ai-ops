package cli

import (
	"fmt"
	"io"
	"os"
	"regexp"
	"strings"
)

const (
	ansiReset   = "\033[0m"
	ansiBold    = "\033[1m"
	ansiDim     = "\033[2m"
	ansiRed     = "\033[31m"
	ansiGreen   = "\033[32m"
	ansiYellow  = "\033[33m"
	ansiBlue    = "\033[34m"
	ansiMagenta = "\033[35m"
	ansiCyan    = "\033[36m"
	ansiGray    = "\033[90m"
)

var jsonPropertyPattern = regexp.MustCompile(`^(\s*)("[^"]+")(:)(.*)$`)

func supportsColor(output io.Writer) bool {
	if os.Getenv("NO_COLOR") != "" || strings.EqualFold(os.Getenv("TERM"), "dumb") {
		return false
	}
	file, ok := output.(*os.File)
	if !ok {
		return false
	}
	info, err := file.Stat()
	return err == nil && info.Mode()&os.ModeCharDevice != 0
}

func (s *Shell) paint(code, value string) string {
	if !s.color || value == "" {
		return value
	}
	return code + value + ansiReset
}

func (s *Shell) section(title string) {
	fmt.Fprintf(s.out, "\n%s\n", s.paint(ansiBold+ansiCyan, title))
}

func (s *Shell) success(message string) {
	fmt.Fprintf(s.out, "%s %s\n", s.paint(ansiGreen, "✓"), message)
}

func (s *Shell) warning(message string) {
	fmt.Fprintf(s.out, "%s %s\n", s.paint(ansiYellow, "!"), message)
}

func (s *Shell) colorizeJSON(value string) string {
	if !s.color {
		return value
	}
	lines := strings.Split(value, "\n")
	for index, line := range lines {
		match := jsonPropertyPattern.FindStringSubmatch(line)
		if len(match) != 5 {
			trimmed := strings.TrimSpace(line)
			if trimmed == "{" || trimmed == "}" || trimmed == "[" || trimmed == "]" || trimmed == "}," || trimmed == "]," {
				lines[index] = s.paint(ansiGray, line)
			}
			continue
		}
		lines[index] = match[1] + s.paint(ansiCyan, match[2]) + match[3] + s.colorizeJSONValue(match[4])
	}
	return strings.Join(lines, "\n")
}

func (s *Shell) colorizeJSONValue(value string) string {
	trimmed := strings.TrimSpace(strings.TrimSuffix(value, ","))
	color := ansiYellow
	switch {
	case strings.HasPrefix(trimmed, `"`):
		color = ansiGreen
	case trimmed == "true" || trimmed == "false":
		color = ansiMagenta
	case trimmed == "null":
		color = ansiGray
	case strings.HasPrefix(trimmed, "{") || strings.HasPrefix(trimmed, "["):
		color = ansiGray
	}
	return s.paint(color, value)
}

func (s *Shell) colorizeTable(value string) string {
	if !s.color {
		return value
	}
	for _, status := range []string{"RUNNING", "AVAILABLE", "READY"} {
		value = strings.ReplaceAll(value, status, s.paint(ansiGreen, status))
	}
	for _, status := range []string{"VALIDATION_FAILED", "SCHEDULING_FAILED", "DEPLOYMENT_FAILED", "RUNTIME_FAILED", "EXTERNAL_API_FAILED", "UNAVAILABLE"} {
		value = strings.ReplaceAll(value, status, s.paint(ansiRed, status))
	}
	value = strings.ReplaceAll(value, "STOPPED", s.paint(ansiGray, "STOPPED"))
	value = strings.ReplaceAll(value, "yes", s.paint(ansiGreen, "yes"))
	value = strings.ReplaceAll(value, "no", s.paint(ansiRed, "no"))
	return value
}
