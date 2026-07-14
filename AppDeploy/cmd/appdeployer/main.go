package main

import (
	"context"
	"fmt"
	"os"
	"path/filepath"

	"github.com/khu/ai-app-deployer/internal/cli"
	"github.com/khu/ai-app-deployer/internal/config"
	"github.com/khu/ai-app-deployer/internal/server"
	"github.com/rs/zerolog/log"
)

func main() {
	os.Exit(run())
}

func run() int {
	settings, err := config.Load()
	if err != nil {
		log.Error().Err(err).Str("component", "cli").Msg("configuration loading failed")
		return 1
	}
	if settings.StorePath == "" {
		settings.StorePath = filepath.Join("data", "appdeployer-store.json")
	}

	api, err := server.NewCLIWithConfig(settings)
	if err != nil {
		log.Error().Err(err).Str("component", "cli").Msg("cli initialization failed")
		return 1
	}
	shell := cli.NewWithPackageLimit(api, os.Stdin, os.Stdout, settings.PackageMaxUpload)
	if err := shell.Run(context.Background(), os.Args[1:]); err != nil {
		fmt.Fprintf(os.Stderr, "오류: %v\n", err)
		return 1
	}
	return 0
}
