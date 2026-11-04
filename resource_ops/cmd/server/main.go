// Command server runs the AI-MCMP resource discovery and recommendation API.
package main

import (
	"log"
	"os"

	"ai-mcmp/internal/api"
)

// @title AI-MCMP Resource Ops API
// @version 0.2.0
// @description Discover, normalize, filter, and rank infrastructure resources from an ApplicationProfile and resource observations.
// @license.name Apache 2.0
// @license.url https://www.apache.org/licenses/LICENSE-2.0.html
// @BasePath /
func main() {
	host := envDefault("HOST", "0.0.0.0")
	port := envDefault("PORT", "8000")
	addr := host + ":" + port

	router, err := api.NewRouter()
	if err != nil {
		log.Fatal(err)
	}
	log.Printf("Starting AI-MCMP resource-ops on http://%s", addr)
	if err := router.Run(addr); err != nil {
		log.Fatal(err)
	}
}

func envDefault(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}
