# Third-Party Go Package License Report

English title: Third-Party Go Package License Report

## Purpose

This report records the third-party Go modules used by the `geon` branch service-control prototype and checks whether the licenses are compatible with the repository's Apache License 2.0 submission policy.

The inventory is based on:

```bash
cd go/service-control-api
go list -m all

cd ../aiops-guard
go list -m all
```

## Summary

| Area | Status |
| --- | --- |
| Repository license | Apache License 2.0 |
| Primary implementation language | Go |
| Copyleft dependency found | No known GPL/LGPL/AGPL/SSPL dependency in the Go module inventory |
| Commercial-use restriction found | No known Commons Clause or non-commercial dependency in the Go module inventory |
| Review scope | Go modules used by `go/service-control-api` and `go/aiops-guard` |

## Direct Dependencies

| Module | Usage | License family | Decision |
| --- | --- | --- | --- |
| `github.com/labstack/echo/v4` | Echo HTTP API framework | MIT | Allowed |
| `github.com/rs/zerolog` | Structured logging | MIT | Allowed |
| `github.com/spf13/viper` | Environment/config loading | MIT | Allowed |
| `github.com/go-playground/validator/v10` | Request struct validation | MIT | Allowed |

`go/aiops-guard` currently uses the Go standard library only and has no third-party runtime module dependency.

## Transitive Dependency Groups

| Group | Representative modules | License family | Decision |
| --- | --- | --- | --- |
| Echo/Gommon stack | `github.com/labstack/gommon`, `github.com/valyala/fasttemplate`, `github.com/valyala/bytebufferpool` | MIT | Allowed |
| Viper configuration stack | `github.com/fsnotify/fsnotify`, `github.com/spf13/afero`, `github.com/spf13/cast`, `github.com/spf13/pflag`, `github.com/subosito/gotenv`, `github.com/pelletier/go-toml/v2`, `go.yaml.in/yaml/v3` | BSD/MIT/Apache-compatible | Allowed |
| Validator stack | `github.com/go-playground/locales`, `github.com/go-playground/universal-translator`, `github.com/gabriel-vasile/mimetype`, `github.com/leodido/go-urn` | MIT/Apache-compatible | Allowed |
| Go extended libraries | `golang.org/x/crypto`, `golang.org/x/net`, `golang.org/x/sys`, `golang.org/x/text`, `golang.org/x/time` | BSD-3-Clause | Allowed |
| Test-only dependencies | `github.com/stretchr/testify`, `github.com/google/go-cmp`, `github.com/davecgh/go-spew`, `github.com/pmezard/go-difflib`, `gopkg.in/check.v1` | MIT/BSD/Apache-compatible | Allowed |

## Policy

New Go packages should be added only after checking:

- whether the Go standard library or an existing project dependency can satisfy the requirement,
- whether the license is Apache 2.0 compatible,
- whether the module is actively maintained,
- whether the module introduces unnecessary runtime, cloud credential, or security risk.

Packages with GPL, LGPL, AGPL, SSPL, Commons Clause, non-commercial, or unclear redistribution terms should not be added to the submission package without separate approval.

## Current Conclusion

The current Go module dependency set is compatible with the Apache License 2.0 submission policy at the prototype level. No known copyleft or commercial-use-restricted Go dependency is included in the reviewed module inventory.
