package main

import (
	"os"

	"kyunghee-aiops/service-control-api/internal/api"
)

func main() {
	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}

	server := api.NewServer(api.NewServerConfig())
	server.Logger.Fatal(server.Start(":" + port))
}
