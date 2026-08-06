package local

import "testing"

func BenchmarkFaultInjectionHook(b *testing.B) {
	for name, config := range map[string]FaultInjectionConfig{
		"disabled": {Rate: 0, Seed: 42},
		"enabled":  {Rate: 0.5, Seed: 42, Codes: []string{FaultCodeTransientDeployment}},
	} {
		b.Run(name, func(b *testing.B) {
			injector := newFaultInjector(config)
			b.ReportAllocs()
			b.ResetTimer()
			for i := 0; i < b.N; i++ {
				_, _ = injector.next()
			}
		})
	}
}
