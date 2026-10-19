package api

import "github.com/labstack/echo/v4"

const pathDeploymentPlans = "/api/v1/deployment-plans"

func registerFocusedRoutes(server *echo.Echo, handler restHandler) {
	server.GET(pathHealthz, handler.RestGetFocusedHealthz)
	server.GET(pathReadyz, handler.RestGetReadyz)
	server.GET(pathOpenAPI, handler.RestGetFocusedOpenAPI)
	server.GET(pathAgents, handler.RestGetAgents)
	server.POST(pathAgents, handler.RestPostAgent)
	server.GET(pathAgents+"/:name", handler.RestGetAgent)
	server.DELETE(pathAgents+"/:name", handler.requireAutonomyAdmin(handler.RestDeleteAgent))
	server.POST(pathDeploymentPlans, handler.RestPostDeploymentPlanFromResults)
	server.POST(pathAgentControl+"/external-flows", handler.RestPostExternalFlow)
	server.GET(pathAgentControl+"/integration", handler.RestGetFlowIntegration)
	server.POST(pathAgentControl+"/flows/:correlation_id/appdeploy/:action", handler.RestPostFlowDelivery)
	server.GET(pathAgentControl+"/flows/:correlation_id/appdeploy", handler.RestGetFlowDelivery)
	server.POST(pathAgentControl+"/deployment-status", handler.RestPostDeploymentStatus)
	server.POST(pathAgentControl+"/optimization-feedback", handler.RestPostOptimizationFeedback)
	server.GET(pathAgentControl+"/flows", handler.RestGetAgentControlFlows)
	server.GET(pathAgentControl+"/flows/:correlation_id", handler.RestGetAgentControlFlow)
	server.DELETE(pathAgentControl+"/flows", handler.RestDeleteAgentControlFlows)
	server.DELETE(pathAgentControl+"/flows/:correlation_id", handler.RestDeleteAgentControlFlow)
	server.POST(pathAgentControl+"/flows/:correlation_id/reasoning-comparisons", handler.RestPostAgentControlReasoningComparison)
}
