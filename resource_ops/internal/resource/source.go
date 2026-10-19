package resource

import "context"

// Source collects source-specific resource data into the narrow RawFact
// boundary. Implementations may use Prometheus temporarily and another
// inventory or resource delivery API later.
type Source interface {
	Name() string
	Collect(context.Context) (RawSnapshot, error)
}
