package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"github.com/rs/zerolog/log"

	"kyunghee-aiops/llmop-api/internal/benchmark"
	"kyunghee-aiops/llmop-api/internal/llmselection"
)

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if err := run(ctx, os.Args[1:], os.Stdout); err != nil {
		log.Error().Err(err).Msg("Ops LLM benchmark command failed")
		os.Exit(1)
	}
}

func run(ctx context.Context, args []string, output io.Writer) error {
	if len(args) == 0 {
		return fmt.Errorf("command is required: select-ops-llm, run-ops-llm-benchmark, or evaluate-ops-llm-outputs")
	}
	repoRoot := findRepoRoot()
	switch args[0] {
	case "select-ops-llm":
		flags := flag.NewFlagSet(args[0], flag.ContinueOnError)
		configPath := flags.String("config", "config/ops_llm_benchmark.json", "Ops LLM ranking configuration")
		policy := flags.String("policy", llmselection.DefaultPolicy, "Selection policy")
		flags.SetOutput(io.Discard)
		if err := flags.Parse(args[1:]); err != nil {
			return fmt.Errorf("parse select-ops-llm flags: %w", err)
		}
		if flags.NArg() != 0 {
			return fmt.Errorf("select-ops-llm does not accept positional arguments")
		}
		resolvedConfig := resolvePath(repoRoot, *configPath)
		result, err := llmselection.NewSelector(resolvedConfig).Select(ctx, *policy)
		if err != nil {
			return fmt.Errorf("select Ops LLM: %w", err)
		}
		return emit(output, map[string]any{
			"command":   "select-ops-llm",
			"config":    resolvedConfig,
			"selection": result,
		})
	case "run-ops-llm-benchmark":
		flags := flag.NewFlagSet(args[0], flag.ContinueOnError)
		scenarios := flags.String("scenarios", "data/ops_llm_eval_scenarios.jsonl", "Evaluation scenario JSONL")
		candidates := flags.String("candidates", "config/ops_llm_eval_candidates.json", "Evaluation candidate JSON")
		outputDir := flags.String("output-dir", "", "Benchmark output directory")
		dryRun := flags.Bool("dry-run", false, "Generate prompts without calling providers")
		flags.SetOutput(io.Discard)
		if err := flags.Parse(args[1:]); err != nil {
			return fmt.Errorf("parse run-ops-llm-benchmark flags: %w", err)
		}
		if flags.NArg() != 0 {
			return fmt.Errorf("run-ops-llm-benchmark does not accept positional arguments")
		}
		resolvedOutputDir := strings.TrimSpace(*outputDir)
		if resolvedOutputDir == "" {
			resolvedOutputDir = filepath.Join(
				repoRoot,
				"runs",
				"ops-llm-evaluation-"+time.Now().Format("20060102-150405"),
			)
		} else {
			resolvedOutputDir = resolvePath(repoRoot, resolvedOutputDir)
		}
		result, err := benchmark.RunOpsLLMBenchmarkContext(ctx, benchmark.RunOptions{
			ScenariosPath:  resolvePath(repoRoot, *scenarios),
			CandidatesPath: resolvePath(repoRoot, *candidates),
			OutputDir:      resolvedOutputDir,
			DryRun:         *dryRun,
		})
		if err != nil {
			return fmt.Errorf("run Ops LLM benchmark: %w", err)
		}
		return emit(output, result)
	case "evaluate-ops-llm-outputs":
		flags := flag.NewFlagSet(args[0], flag.ContinueOnError)
		scenarios := flags.String("scenarios", "data/ops_llm_eval_scenarios.jsonl", "Evaluation scenario JSONL")
		outputs := flags.String("outputs", "", "Model output JSONL created by the benchmark runner")
		summary := flags.String("summary", "", "Evaluation summary JSON path")
		flags.SetOutput(io.Discard)
		if err := flags.Parse(args[1:]); err != nil {
			return fmt.Errorf("parse evaluate-ops-llm-outputs flags: %w", err)
		}
		if flags.NArg() != 0 {
			return fmt.Errorf("evaluate-ops-llm-outputs does not accept positional arguments")
		}
		if strings.TrimSpace(*outputs) == "" {
			return fmt.Errorf("--outputs is required")
		}
		summaryPath := strings.TrimSpace(*summary)
		if summaryPath != "" {
			summaryPath = resolvePath(repoRoot, summaryPath)
		}
		result, err := benchmark.EvaluateOpsLLMOutputs(benchmark.EvaluateOptions{
			ScenariosPath: resolvePath(repoRoot, *scenarios),
			OutputsPath:   resolvePath(repoRoot, *outputs),
			SummaryPath:   summaryPath,
		})
		if err != nil {
			return fmt.Errorf("evaluate Ops LLM outputs: %w", err)
		}
		return emit(output, result)
	default:
		return fmt.Errorf("unknown command %q", args[0])
	}
}

func emit(output io.Writer, value any) error {
	encoder := json.NewEncoder(output)
	encoder.SetIndent("", "  ")
	if err := encoder.Encode(value); err != nil {
		return fmt.Errorf("encode command result: %w", err)
	}
	return nil
}

func resolvePath(repoRoot string, value string) string {
	value = strings.TrimSpace(value)
	if value == "" || filepath.IsAbs(value) {
		return value
	}
	return filepath.Join(repoRoot, value)
}

func findRepoRoot() string {
	current, err := os.Getwd()
	if err != nil {
		return "."
	}
	for range 10 {
		marker := filepath.Join(current, "config", "ops_llm_benchmark.json")
		if _, err := os.Stat(marker); err == nil {
			return current
		}
		parent := filepath.Dir(current)
		if parent == current {
			break
		}
		current = parent
	}
	return "."
}
