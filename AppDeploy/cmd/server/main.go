package main

import (
	"log"
	"os"

	"github.com/khu/ai-app-deployer/internal/server"
)

func main() {
	port := os.Getenv("AIAPP_SERVER_PORT")
	if port == "" {
		port = "8080"
	}

	srv := server.New()
	if err := srv.Start(":" + port); err != nil {
		log.Fatal(err)
	}
}
