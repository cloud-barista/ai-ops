package webui

import (
	"embed"
	"net/http"

	"github.com/labstack/echo/v4"
)

//go:embed static/index.html static/app.css static/app.js
var assets embed.FS

func Register(e *echo.Echo) {
	e.GET("/", index)
	e.GET("/console", index)
	e.GET("/web/app.css", asset("static/app.css", "text/css; charset=utf-8"))
	e.GET("/web/app.js", asset("static/app.js", "application/javascript; charset=utf-8"))
}

func index(c echo.Context) error {
	setSecurityHeaders(c)
	raw, err := assets.ReadFile("static/index.html")
	if err != nil {
		return echo.NewHTTPError(http.StatusInternalServerError, "web console unavailable")
	}
	return c.HTMLBlob(http.StatusOK, raw)
}

func asset(name, contentType string) echo.HandlerFunc {
	return func(c echo.Context) error {
		setSecurityHeaders(c)
		raw, err := assets.ReadFile(name)
		if err != nil {
			return echo.NewHTTPError(http.StatusNotFound, "asset not found")
		}
		return c.Blob(http.StatusOK, contentType, raw)
	}
}

func setSecurityHeaders(c echo.Context) {
	c.Response().Header().Set("Cache-Control", "no-cache")
	c.Response().Header().Set("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'")
	c.Response().Header().Set("Referrer-Policy", "no-referrer")
	c.Response().Header().Set("X-Content-Type-Options", "nosniff")
	c.Response().Header().Set("X-Frame-Options", "DENY")
}
