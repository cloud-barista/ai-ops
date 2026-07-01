package requestid

import (
	"context"

	"github.com/google/uuid"
	"github.com/labstack/echo/v4"
)

type contextKey string

const (
	HeaderName = "X-Request-ID"
	key        = contextKey("request_id")
)

func Middleware(next echo.HandlerFunc) echo.HandlerFunc {
	return func(c echo.Context) error {
		id := c.Request().Header.Get(HeaderName)
		if id == "" {
			id = "req-" + uuid.NewString()
		}
		req := c.Request().WithContext(context.WithValue(c.Request().Context(), key, id))
		c.SetRequest(req)
		c.Response().Header().Set(HeaderName, id)
		return next(c)
	}
}

func FromContext(ctx context.Context) string {
	if id, ok := ctx.Value(key).(string); ok && id != "" {
		return id
	}
	return "req-" + uuid.NewString()
}
