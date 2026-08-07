package webui

import (
	"embed"
	"net/http"

	"github.com/labstack/echo/v4"
)

//go:embed static/index.html static/app.css static/app.js static/manifest_stages.js static/llm_op_demo.html static/llm_op_demo.css static/llm_op_demo_contract.js static/llm_op_demo.js
var assets embed.FS

func Register(server *echo.Echo) {
	server.GET("/", serveEmbedded("static/index.html", "text/html; charset=utf-8"))
	server.GET("/llm-op-demo", serveEmbedded("static/llm_op_demo.html", "text/html; charset=utf-8"))
	server.GET("/llm-op-demo/", serveEmbedded("static/llm_op_demo.html", "text/html; charset=utf-8"))
	server.GET("/assets/app.css", serveEmbedded("static/app.css", "text/css; charset=utf-8"))
	server.GET("/assets/manifest_stages.js", serveEmbedded("static/manifest_stages.js", "text/javascript; charset=utf-8"))
	server.GET("/assets/app.js", serveEmbedded("static/app.js", "text/javascript; charset=utf-8"))
	server.GET("/assets/llm-op-demo.css", serveEmbedded("static/llm_op_demo.css", "text/css; charset=utf-8"))
	server.GET("/assets/llm-op-demo-contract.js", serveEmbedded("static/llm_op_demo_contract.js", "text/javascript; charset=utf-8"))
	server.GET("/assets/llm-op-demo.js", serveEmbedded("static/llm_op_demo.js", "text/javascript; charset=utf-8"))
}

func serveEmbedded(name string, contentType string) echo.HandlerFunc {
	return func(context echo.Context) error {
		content, err := assets.ReadFile(name)
		if err != nil {
			return echo.NewHTTPError(http.StatusInternalServerError, "embedded web asset is unavailable")
		}
		return context.Blob(http.StatusOK, contentType, content)
	}
}
