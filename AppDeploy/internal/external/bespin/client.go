package bespin

import (
	"github.com/khu/ai-app-deployer/internal/external"
)

func NewClient() external.Client {
	return external.NewNotConfiguredClient(external.ProviderBespin)
}
