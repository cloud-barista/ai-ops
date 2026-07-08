package main

import (
	"encoding/json"
	"flag"
	"io"
	"os"

	"github.com/cloud-barista/ai-ops/go/aiops-guard/internal/guard"
)

func main() {
	inputPath := flag.String("input", "-", "JSON request file path, or '-' for stdin")
	flag.Parse()

	reqBytes, err := readInput(*inputPath)
	if err != nil {
		writeResult(guard.Result{Valid: false, Stderr: err.Error()})
		os.Exit(1)
	}

	var req guard.Request
	if err := json.Unmarshal(reqBytes, &req); err != nil {
		writeResult(guard.Result{Mode: req.Mode, Valid: false, Stderr: "invalid JSON request: " + err.Error()})
		os.Exit(1)
	}

	result := guard.Execute(req, nil)
	writeResult(result)
	if !result.Valid {
		os.Exit(1)
	}
}

func readInput(path string) ([]byte, error) {
	if path == "-" {
		return io.ReadAll(os.Stdin)
	}
	return os.ReadFile(path)
}

func writeResult(result guard.Result) {
	encoded, err := json.MarshalIndent(result, "", "  ")
	if err != nil {
		_, _ = os.Stderr.WriteString("failed to encode result: " + err.Error() + "\n")
		return
	}
	_, _ = os.Stdout.Write(append(encoded, '\n'))
}
