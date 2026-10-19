// Command server runs the AI-MCMP resource discovery and recommendation API.
package main

import (
	"log"
	"os"

	"ai-mcmp/internal/api"
)

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
