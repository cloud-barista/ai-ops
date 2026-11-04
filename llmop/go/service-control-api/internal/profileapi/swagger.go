package profileapi

import (
	"net/http"

	"github.com/labstack/echo/v4"
)

const swaggerHTML = `<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>LLM_Op API · Swagger UI</title>
  <!-- shortcut: UI assets require CDN access; vendor the pinned assets when offline support is needed. -->
  <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5.32.0/swagger-ui.css">
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="https://unpkg.com/swagger-ui-dist@5.32.0/swagger-ui-bundle.js" crossorigin="anonymous"></script>
  <script>
    SwaggerUIBundle({
      url: '/openapi.yaml',
      dom_id: '#swagger-ui',
      deepLinking: true,
      filter: true,
      validatorUrl: null
    });
  </script>
</body>
</html>`

func serveSwagger(context echo.Context) error {
	return context.HTML(http.StatusOK, swaggerHTML)
}
