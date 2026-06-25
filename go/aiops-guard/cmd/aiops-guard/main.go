package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"

	"github.com/gunsun2000/aiops_research/go/aiops-guard/internal/guard"
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
		fmt.Fprintf(os.Stderr, "failed to encode result: %v\n", err)
		return
	}
	fmt.Println(string(encoded))
}
