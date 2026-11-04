// Package docs embeds the Resource Ops API contract in the server binary.
package docs

import _ "embed"

//go:embed swagger/swagger.yaml
var OpenAPI []byte
