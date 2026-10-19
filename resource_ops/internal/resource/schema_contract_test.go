package resource

import (
	"encoding/json"
	"os"
	"reflect"
	"sort"
	"testing"
)

func TestResourceSchemasHaveStrictTopLevelContracts(t *testing.T) {
	tests := []struct {
		path     string
		required []string
	}{
		{"../../schemas/application_profile_recommendation_request.schema.json", []string{"application_profile"}},
		{"../../schemas/resource_query.schema.json", []string{"constraints"}},
		{"../../schemas/resource_recommendation.schema.json", []string{
			"schema_version", "request_id", "source", "observed_at", "query",
			"recommendations", "excluded_resources", "summary", "warnings",
		}},
	}
	for _, test := range tests {
		t.Run(test.path, func(t *testing.T) {
			data, err := os.ReadFile(test.path)
			if err != nil {
				t.Fatal(err)
			}
			var schema map[string]any
			if err := json.Unmarshal(data, &schema); err != nil {
				t.Fatal(err)
			}
			if schema["additionalProperties"] != false {
				t.Fatal("top-level schema must reject additional properties")
			}
			properties := schema["properties"].(map[string]any)
			version := properties["schema_version"].(map[string]any)
			if version["const"] != SchemaVersion {
				t.Fatalf("schema version = %v, want %s", version["const"], SchemaVersion)
			}
			got := toStrings(schema["required"])
			want := append([]string(nil), test.required...)
			sort.Strings(got)
			sort.Strings(want)
			if !reflect.DeepEqual(got, want) {
				t.Fatalf("required = %v, want %v", got, want)
			}
		})
	}
}

func toStrings(value any) []string {
	raw := value.([]any)
	result := make([]string, len(raw))
	for index, item := range raw {
		result[index] = item.(string)
	}
	return result
}
