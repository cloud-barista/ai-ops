package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"strings"
	"time"

	"kyunghee-aiops/service-control-api/internal/llmclient"
	"kyunghee-aiops/service-control-api/internal/llmop"
	"kyunghee-aiops/service-control-api/internal/plannerguard"
)

type fixtureCompletionClient struct {
	content string
}

func (client fixtureCompletionClient) Complete(
	_ context.Context,
	candidate llmclient.Candidate,
	_ string,
	_ string,
) (llmclient.Completion, error) {
	return llmclient.Completion{
		Status:      "executed",
		Content:     client.content,
		Provider:    candidate.Provider,
		ActualModel: candidate.ActualModel,
		CandidateID: candidate.CandidateID,
	}, nil
}

func main() {
	if err := run(); err != nil {
		_, _ = fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func run() error {
	var requestPath string
	var safeguardOutputPath string
	var modelOutputPath string
	var policyPath string
	var nowValue string
	flag.StringVar(&requestPath, "request", "", "path to an LLMOperationRequest JSON fixture")
	flag.StringVar(&safeguardOutputPath, "safeguard-output", "", "path to a bounded safeguard review JSON fixture")
	flag.StringVar(&modelOutputPath, "model-output", "", "path to a bounded Qwen proposal JSON fixture")
	flag.StringVar(&policyPath, "policy", "", "path to planner_guard_policy.json")
	flag.StringVar(&nowValue, "now", "", "fixed RFC3339 time for deterministic freshness checks")
	flag.Parse()

	if strings.TrimSpace(requestPath) == "" ||
		strings.TrimSpace(safeguardOutputPath) == "" ||
		strings.TrimSpace(modelOutputPath) == "" ||
		strings.TrimSpace(policyPath) == "" ||
		strings.TrimSpace(nowValue) == "" {
		return errors.New("-request, -safeguard-output, -model-output, -policy, and -now are required")
	}

	var request llmop.Request
	if err := decodeJSONFile(requestPath, &request); err != nil {
		return err
	}
	modelOutput, err := os.ReadFile(modelOutputPath)
	if err != nil {
		return fmt.Errorf("read model output fixture: %w", err)
	}
	safeguardOutput, err := os.ReadFile(safeguardOutputPath)
	if err != nil {
		return fmt.Errorf("read safeguard output fixture: %w", err)
	}
	policy, err := plannerguard.LoadPolicy(policyPath)
	if err != nil {
		return err
	}
	now, err := time.Parse(time.RFC3339, nowValue)
	if err != nil {
		return fmt.Errorf("parse -now: %w", err)
	}

	normalizer := llmop.NewNormalizer()
	normalizer.Now = func() time.Time { return now }
	planner := llmop.NewSafeguardedPlanner(
		fixtureCompletionClient{content: string(safeguardOutput)},
		fixtureCompletionClient{content: string(modelOutput)},
		normalizer,
	)
	result, prepareErr := planner.Prepare(
		context.Background(),
		llmclient.Candidate{
			CandidateID: request.CandidateID,
			Provider:    "fixture-openai-compatible",
			ActualModel: "fixture-qwen",
			Enabled:     true,
			JSONMode:    true,
		},
		policy,
		request,
	)
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetIndent("", "  ")
	if err := encoder.Encode(result); err != nil {
		return fmt.Errorf("encode demo result: %w", err)
	}
	return prepareErr
}

func decodeJSONFile(path string, output any) error {
	content, err := os.ReadFile(path)
	if err != nil {
		return fmt.Errorf("read JSON fixture: %w", err)
	}
	decoder := json.NewDecoder(bytes.NewReader(content))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(output); err != nil {
		return fmt.Errorf("decode JSON fixture: %w", err)
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		if err == nil {
			return errors.New("JSON fixture contains multiple values")
		}
		return fmt.Errorf("decode trailing JSON fixture data: %w", err)
	}
	return nil
}
