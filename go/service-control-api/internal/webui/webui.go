package webui

import (
	"embed"
	"net/http"

	"github.com/labstack/echo/v4"
)

//go:embed static/index.html static/app.css static/app.js
var assets embed.FS

func Register(server *echo.Echo) {
	server.GET("/", serveEmbedded("static/index.html", "text/html; charset=utf-8"))
	server.GET("/assets/app.css", serveEmbedded("static/app.css", "text/css; charset=utf-8"))
	server.GET("/assets/app.js", serveEmbedded("static/app.js", "text/javascript; charset=utf-8"))
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
