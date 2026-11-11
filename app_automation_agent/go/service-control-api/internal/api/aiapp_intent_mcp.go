package api

import (
	"context"
	"net/http"

	"github.com/labstack/echo/v4"
	"github.com/modelcontextprotocol/go-sdk/mcp"
	"github.com/rs/zerolog/log"
)

const aiAppIntentMCPPath = aiAppIntentPrefix + "/mcp"

type aiAppIntentRunLookup struct {
	RunID string `json:"runId" jsonschema:"Run ID returned by run_application_intent or the REST agent."`
}

type aiAppIntentMCPPrepare struct {
	RunID   string                   `json:"runId"`
	Request AIAppIntentChangeRequest `json:"request"`
}

type aiAppIntentMCPApprove struct {
	RunID   string                     `json:"runId"`
	Request AIAppIntentApprovalRequest `json:"request"`
}

// newAIAppIntentMCPHandler godoc
// @ID AIAppIntentMCP
// @Summary Call the AI-Ops natural-language agent over MCP
// @Description Stateless Streamable HTTP endpoint. Use tools/list to discover the intent and run lookup tools.
// @Tags AI-App Integration
// @Accept json
// @Produce json
// @Security AIAppBearer
// @Security FrameworkPrincipalBearer
// @Param X-AIMCMP-Namespace header string true "Configured CB-Tumblebug namespace"
// @Param message body map[string]interface{} true "MCP JSON-RPC message"
// @Success 200 {object} map[string]interface{}
// @Success 202 "Notification accepted"
// @Failure 400 {object} map[string]interface{}
// @Failure 401 {object} map[string]interface{}
// @Failure 403 {object} map[string]interface{}
// @Failure 413 {object} map[string]interface{}
// @Failure 415 "Content-Type must be application/json"
// @Failure 503 {object} map[string]interface{}
// @Router /aiapp/partner/nl/mcp [post]
func (h restHandler) newAIAppIntentMCPHandler() echo.HandlerFunc {
	server := mcp.NewServer(&mcp.Implementation{Name: aiAppIntentSource, Version: aiAppIntentVersion}, nil)
	destructive := false
	mcp.AddTool(server, &mcp.Tool{
		Name:        "run_application_intent",
		Description: "Interpret an AI-App instruction, run an authorized catalog or status lookup, and save the run. Deployment and control instructions are recorded as proposals.",
		Annotations: &mcp.ToolAnnotations{DestructiveHint: &destructive},
		Meta:        mcp.Meta{"ai-mcmp/grade": "review"},
	}, func(ctx context.Context, _ *mcp.CallToolRequest, request AIAppIntentRequest) (*mcp.CallToolResult, any, error) {
		run, failure := h.runAIAppIntent(ctx, request, "ai-app-mcp")
		return nil, run, mcpIntentError(failure)
	})
	if h.service.frameworkAccess != nil {
		h.registerAIAppIntentChangeTools(server)
	}
	mcp.AddTool(server, &mcp.Tool{
		Name:        "get_application_intent_run",
		Description: "Read a saved AI-Ops natural-language run in the authorized namespace.",
		Annotations: &mcp.ToolAnnotations{ReadOnlyHint: true},
		Meta:        mcp.Meta{"ai-mcmp/grade": "read"},
	}, func(ctx context.Context, _ *mcp.CallToolRequest, lookup aiAppIntentRunLookup) (*mcp.CallToolResult, any, error) {
		run, failure := h.readAIAppIntentRun(ctx, lookup.RunID)
		return nil, run, mcpIntentError(failure)
	})

	handler := mcp.NewStreamableHTTPHandler(func(*http.Request) *mcp.Server { return server }, &mcp.StreamableHTTPOptions{
		Stateless:                    true,
		JSONResponse:                 true,
		MaxRequestBodyBytes:          focusedMaxRequestBytes,
		PropagateRequestCancellation: true,
	})
	serve := echo.WrapHandler(http.NewCrossOriginProtection().Handler(handler))
	return func(c echo.Context) error {
		if c.Request().Header.Get(aiAppNamespaceHeader) != h.config.AIAppNamespace {
			return focusedError(c, http.StatusForbidden, "NAMESPACE_FORBIDDEN", "The namespace is outside this agent's scope.", nil)
		}
		return serve(c)
	}
}

func (h restHandler) registerAIAppIntentChangeTools(server *mcp.Server) {
	destructive := true
	mcp.AddTool(server, &mcp.Tool{
		Name: "prepare_application_intent_change", Description: "Bind a saved write proposal to an exact deployment or revision for review.",
		Meta: mcp.Meta{"ai-mcmp/grade": "review"},
	}, func(ctx context.Context, _ *mcp.CallToolRequest, input aiAppIntentMCPPrepare) (*mcp.CallToolResult, any, error) {
		principal := aiAppIntentPrincipal(ctx)
		if principal == nil {
			return nil, nil, deniedFrameworkAction()
		}
		ctx, cancel := context.WithTimeout(ctx, h.config.AgentExecutionTimeout)
		defer cancel()
		plan, failure := h.prepareAIAppIntentChange(ctx, principal, input.RunID, input.Request)
		return nil, plan, mcpIntentError(failure)
	})
	mcp.AddTool(server, &mcp.Tool{
		Name: "approve_application_intent_change", Description: "Approve the reviewed digest or resume its confirmed pending revision as the original approver.",
		Annotations: &mcp.ToolAnnotations{DestructiveHint: &destructive}, Meta: mcp.Meta{"ai-mcmp/grade": "write"},
	}, func(ctx context.Context, _ *mcp.CallToolRequest, input aiAppIntentMCPApprove) (*mcp.CallToolResult, any, error) {
		principal := aiAppIntentPrincipal(ctx)
		if principal == nil {
			return nil, nil, deniedFrameworkAction()
		}
		ctx, cancel := context.WithTimeout(ctx, h.config.AgentExecutionTimeout)
		defer cancel()
		approval, failure := h.approveAIAppIntentChange(ctx, principal, input.RunID, input.Request)
		return nil, approval, mcpIntentError(failure)
	})
	mcp.AddTool(server, &mcp.Tool{
		Name: "get_application_intent_change", Description: "Read a reviewed change and its approval receipt.",
		Annotations: &mcp.ToolAnnotations{ReadOnlyHint: true}, Meta: mcp.Meta{"ai-mcmp/grade": "read"},
	}, func(ctx context.Context, _ *mcp.CallToolRequest, input aiAppIntentRunLookup) (*mcp.CallToolResult, any, error) {
		principal := aiAppIntentPrincipal(ctx)
		if principal == nil {
			return nil, nil, deniedFrameworkAction()
		}
		ctx, cancel := context.WithTimeout(ctx, h.config.AgentExecutionTimeout)
		defer cancel()
		change, failure := h.getAIAppIntentChange(ctx, principal, input.RunID)
		return nil, change, mcpIntentError(failure)
	})
}

func mcpIntentError(failure *aiAppIntegrationFailure) error {
	if failure == nil {
		return nil
	}
	if failure.cause != nil {
		log.Error().Err(failure.cause).Str("code", failure.code).Msg("AI-Ops MCP tool failed")
	}
	return failure
}
