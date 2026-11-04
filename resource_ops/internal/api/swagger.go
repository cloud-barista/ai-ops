package api

import (
	"net/http"

	"github.com/gin-gonic/gin"

	"ai-mcmp/docs"
)

const swaggerHTML = `<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Resource Ops API · Swagger UI</title>
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

func serveSwagger(context *gin.Context) {
	context.Data(http.StatusOK, "text/html; charset=utf-8", []byte(swaggerHTML))
}

// getOpenAPI godoc
// @ID GetResourceOpsOpenAPI
// @Summary Download the Resource Ops API specification
// @Tags Service
// @Produce text/yaml
// @Success 200 {file} file
// @Router /openapi.yaml [get]
func getOpenAPI(context *gin.Context) {
	context.Data(http.StatusOK, "application/yaml; charset=utf-8", docs.OpenAPI)
}
