package llmop

import (
	"fmt"
	"math"
	"regexp"
	"sort"
	"strings"
	"time"
	"unicode"

	"kyunghee-aiops/service-control-api/internal/appdeploy"
)

const (
	defaultMaxObservationAge = 10 * time.Minute
	defaultMaxFutureSkew     = time.Minute
	defaultMaxLogs           = 50
	defaultMaxLogInputs      = 500
	defaultMaxLogRunes       = 2000
	defaultMaxTargets        = 100
	defaultMaxRuntimeHealth  = 100
	defaultMaxAlarms         = 100
	defaultMaxStatusBuckets  = 50
	defaultMaxFieldRunes     = 128
)

var (
	sensitiveAssignment = regexp.MustCompile(`(?i)(["']?(?:authorization|api[_ -]?key|apikey|access[_ -]?key|secret[_ -]?key|private[_ -]?key|token|password|credential)["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|(?:bearer\s+)?[^\s,;}\]]+)`)
	bearerValue         = regexp.MustCompile(`(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+`)
	privateKeyMarker    = regexp.MustCompile(`(?i)-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----`)
	sourceIdentifier    = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`)
)

type Normalizer struct {
	Now               func() time.Time
	MaxObservationAge time.Duration
	MaxFutureSkew     time.Duration
	MaxLogs           int
	MaxLogInputs      int
	MaxLogRunes       int
	MaxTargets        int
	MaxRuntimeHealth  int
	MaxAlarms         int
	MaxStatusBuckets  int
	MaxFieldRunes     int
}

func NewNormalizer() Normalizer {
	return Normalizer{
		Now:               time.Now,
		MaxObservationAge: defaultMaxObservationAge,
		MaxFutureSkew:     defaultMaxFutureSkew,
		MaxLogs:           defaultMaxLogs,
		MaxLogInputs:      defaultMaxLogInputs,
		MaxLogRunes:       defaultMaxLogRunes,
		MaxTargets:        defaultMaxTargets,
		MaxRuntimeHealth:  defaultMaxRuntimeHealth,
		MaxAlarms:         defaultMaxAlarms,
		MaxStatusBuckets:  defaultMaxStatusBuckets,
		MaxFieldRunes:     defaultMaxFieldRunes,
	}
}

func (normalizer Normalizer) Normalize(input OperationContext) (NormalizedContext, error) {
	now := time.Now()
	if normalizer.Now != nil {
		now = normalizer.Now()
	}
	maxAge := normalizer.MaxObservationAge
	if maxAge <= 0 {
		maxAge = defaultMaxObservationAge
	}
	maxFutureSkew := normalizer.MaxFutureSkew
	if maxFutureSkew <= 0 {
		maxFutureSkew = defaultMaxFutureSkew
	}
	maxLogs := normalizer.MaxLogs
	if maxLogs <= 0 {
		maxLogs = defaultMaxLogs
	}
	maxLogInputs := normalizer.MaxLogInputs
	if maxLogInputs <= 0 {
		maxLogInputs = defaultMaxLogInputs
	}
	maxLogRunes := normalizer.MaxLogRunes
	if maxLogRunes <= 0 {
		maxLogRunes = defaultMaxLogRunes
	}
	maxTargets := normalizer.MaxTargets
	if maxTargets <= 0 {
		maxTargets = defaultMaxTargets
	}
	maxRuntimeHealth := normalizer.MaxRuntimeHealth
	if maxRuntimeHealth <= 0 {
		maxRuntimeHealth = defaultMaxRuntimeHealth
	}
	maxAlarms := normalizer.MaxAlarms
	if maxAlarms <= 0 {
		maxAlarms = defaultMaxAlarms
	}
	maxStatusBuckets := normalizer.MaxStatusBuckets
	if maxStatusBuckets <= 0 {
		maxStatusBuckets = defaultMaxStatusBuckets
	}
	maxFieldRunes := normalizer.MaxFieldRunes
	if maxFieldRunes <= 0 {
		maxFieldRunes = defaultMaxFieldRunes
	}

	result := NormalizedContext{ObservationStatus: "absent"}
	present := 0
	fresh := 0

	if input.ResourceSnapshot != nil {
		present++
		if len(input.ResourceSnapshot.Targets) > maxTargets {
			return result, fmt.Errorf(
				"resource_snapshot.targets exceeds the maximum of %d items",
				maxTargets,
			)
		}
		isFresh, err := observationFresh(
			"resource_snapshot",
			input.ResourceSnapshot.Source,
			input.ResourceSnapshot.ObservedAt,
			now,
			maxAge,
			maxFutureSkew,
		)
		if err != nil {
			return result, err
		}
		if isFresh {
			copy := *input.ResourceSnapshot
			copy.Targets = make(
				[]appdeploy.RuntimeHealthSnapshot,
				0,
				len(input.ResourceSnapshot.Targets),
			)
			for index, target := range input.ResourceSnapshot.Targets {
				itemFresh, timestampErr := nestedTimestampFresh(
					fmt.Sprintf("resource_snapshot.targets[%d].last_checked_at", index),
					target.LastCheckedAt,
					input.ResourceSnapshot.ObservedAt,
					now,
					maxAge,
					maxFutureSkew,
				)
				if timestampErr != nil {
					return result, timestampErr
				}
				if !itemFresh {
					result.StaleSources = appendUnique(
						result.StaleSources,
						"resource_snapshot.targets",
					)
					continue
				}
				if err := normalizeRuntimeHealth(
					&target,
					fmt.Sprintf("resource_snapshot.targets[%d]", index),
					maxFieldRunes,
					&result.RedactedValues,
				); err != nil {
					return result, err
				}
				copy.Targets = append(copy.Targets, target)
			}
			result.ResourceSnapshot = &copy
			fresh++
		} else {
			result.StaleSources = appendUnique(result.StaleSources, "resource_snapshot")
		}
	}

	if input.MonitoringSummary != nil {
		present++
		if len(input.MonitoringSummary.Summary.RuntimeHealth) > maxRuntimeHealth {
			return result, fmt.Errorf(
				"monitoring_summary.summary.runtime_health exceeds the maximum of %d items",
				maxRuntimeHealth,
			)
		}
		if len(input.MonitoringSummary.Summary.Alarms) > maxAlarms {
			return result, fmt.Errorf(
				"monitoring_summary.summary.alarms exceeds the maximum of %d items",
				maxAlarms,
			)
		}
		if len(input.MonitoringSummary.Summary.Deployments.ByStatus) > maxStatusBuckets {
			return result, fmt.Errorf(
				"monitoring_summary.summary.deployments.by_status exceeds the maximum of %d items",
				maxStatusBuckets,
			)
		}
		isFresh, err := observationFresh(
			"monitoring_summary",
			input.MonitoringSummary.Source,
			input.MonitoringSummary.ObservedAt,
			now,
			maxAge,
			maxFutureSkew,
		)
		if err != nil {
			return result, err
		}
		if isFresh {
			generatedFresh, timestampErr := nestedTimestampFresh(
				"monitoring_summary.summary.generated_at",
				input.MonitoringSummary.Summary.GeneratedAt,
				input.MonitoringSummary.ObservedAt,
				now,
				maxAge,
				maxFutureSkew,
			)
			if timestampErr != nil {
				return result, timestampErr
			}
			if !generatedFresh {
				result.StaleSources = appendUnique(result.StaleSources, "monitoring_summary")
			} else {
				copy := cloneMonitoringObservation(*input.MonitoringSummary)
				if err := normalizeMonitoringSummary(
					&copy,
					input.MonitoringSummary.Summary.GeneratedAt,
					now,
					maxAge,
					maxFutureSkew,
					maxFieldRunes,
					maxLogRunes,
					&result,
				); err != nil {
					return result, err
				}
				result.MonitoringSummary = &copy
				fresh++
			}
		} else {
			result.StaleSources = appendUnique(result.StaleSources, "monitoring_summary")
		}
	}

	if input.DeploymentLogs != nil {
		present++
		if len(input.DeploymentLogs.Items) > maxLogInputs {
			return result, fmt.Errorf(
				"deployment_logs.items exceeds the maximum of %d items",
				maxLogInputs,
			)
		}
		isFresh, err := observationFresh(
			"deployment_logs",
			input.DeploymentLogs.Source,
			input.DeploymentLogs.ObservedAt,
			now,
			maxAge,
			maxFutureSkew,
		)
		if err != nil {
			return result, err
		}
		if isFresh {
			copy := *input.DeploymentLogs
			items, normalizeErr := normalizeLogs(
				input.DeploymentLogs.Items,
				input.DeploymentLogs.ObservedAt,
				now,
				maxAge,
				maxFutureSkew,
				maxLogs,
				maxLogRunes,
				maxFieldRunes,
				&result,
			)
			if normalizeErr != nil {
				return result, normalizeErr
			}
			copy.Items = items
			if len(items) > 0 {
				result.DeploymentLogs = &copy
			}
			fresh++
		} else {
			result.StaleSources = appendUnique(result.StaleSources, "deployment_logs")
		}
	}

	if input.MetricsSummary != nil {
		present++
		isFresh, err := observationFresh(
			"metrics_summary",
			input.MetricsSummary.Source,
			input.MetricsSummary.ObservedAt,
			now,
			maxAge,
			maxFutureSkew,
		)
		if err != nil {
			return result, err
		}
		if input.MetricsSummary.LatencyP95MS < 0 ||
			input.MetricsSummary.ThroughputRPS < 0 ||
			input.MetricsSummary.ErrorRate < 0 ||
			input.MetricsSummary.ErrorRate > 1 ||
			math.IsNaN(input.MetricsSummary.LatencyP95MS) ||
			math.IsNaN(input.MetricsSummary.ThroughputRPS) ||
			math.IsNaN(input.MetricsSummary.ErrorRate) ||
			math.IsInf(input.MetricsSummary.LatencyP95MS, 0) ||
			math.IsInf(input.MetricsSummary.ThroughputRPS, 0) ||
			math.IsInf(input.MetricsSummary.ErrorRate, 0) ||
			input.MetricsSummary.SampleCount < 0 {
			return result, fmt.Errorf("metrics_summary contains a non-finite, negative, or out-of-range value")
		}
		if err := validateBoundedField(
			"metrics_summary.deployment_id",
			input.MetricsSummary.DeploymentID,
			maxFieldRunes,
		); err != nil {
			return result, err
		}
		copy := *input.MetricsSummary
		if isFresh {
			result.MetricsSummary = &copy
			fresh++
		} else {
			result.StaleSources = appendUnique(result.StaleSources, "metrics_summary")
		}
	}

	switch {
	case present == 0:
		result.ObservationStatus = "absent"
	case fresh == present:
		result.ObservationStatus = "fresh"
	case fresh == 0:
		result.ObservationStatus = "stale"
	default:
		result.ObservationStatus = "mixed"
	}
	if result.ObservationStatus == "fresh" &&
		(len(result.StaleSources) > 0 || result.DroppedLogs > 0) {
		result.ObservationStatus = "mixed"
	}
	return result, nil
}

func observationFresh(
	label string,
	source string,
	observedAt time.Time,
	now time.Time,
	maxAge time.Duration,
	maxFutureSkew time.Duration,
) (bool, error) {
	if strings.TrimSpace(source) == "" {
		return false, fmt.Errorf("%s.source is required", label)
	}
	if !sourceIdentifier.MatchString(source) {
		return false, fmt.Errorf("%s.source must be a bounded identifier", label)
	}
	if observedAt.IsZero() {
		return false, fmt.Errorf("%s.observed_at is required", label)
	}
	if observedAt.After(now.Add(maxFutureSkew)) {
		return false, fmt.Errorf("%s.observed_at is in the future", label)
	}
	return now.Sub(observedAt) <= maxAge, nil
}

func nestedTimestampFresh(
	label string,
	timestamp time.Time,
	parentObservedAt time.Time,
	now time.Time,
	maxAge time.Duration,
	maxFutureSkew time.Duration,
) (bool, error) {
	if timestamp.IsZero() {
		return false, fmt.Errorf("%s is required", label)
	}
	if timestamp.After(now.Add(maxFutureSkew)) {
		return false, fmt.Errorf("%s is in the future", label)
	}
	if timestamp.After(parentObservedAt.Add(maxFutureSkew)) {
		return false, fmt.Errorf("%s is later than its parent observation", label)
	}
	return now.Sub(timestamp) <= maxAge, nil
}

func normalizeMonitoringSummary(
	observation *MonitoringObservation,
	generatedAt time.Time,
	now time.Time,
	maxAge time.Duration,
	maxFutureSkew time.Duration,
	maxFieldRunes int,
	maxMessageRunes int,
	result *NormalizedContext,
) error {
	if err := validateBoundedField(
		"monitoring_summary.summary.request_id",
		observation.Summary.RequestID,
		maxFieldRunes,
	); err != nil {
		return err
	}
	observation.Summary.Status = sanitizeBoundedText(
		observation.Summary.Status,
		maxFieldRunes,
		&result.RedactedValues,
	)
	if observation.Summary.Deployments.Total < 0 ||
		observation.Summary.Deployments.Active < 0 ||
		observation.Summary.Deployments.Failed < 0 ||
		observation.Summary.Deployments.Stopped < 0 {
		return fmt.Errorf("monitoring_summary.summary.deployments contains a negative count")
	}
	var normalizedByStatus map[string]int
	if observation.Summary.Deployments.ByStatus != nil {
		normalizedByStatus = make(
			map[string]int,
			len(observation.Summary.Deployments.ByStatus),
		)
	}
	for status, count := range observation.Summary.Deployments.ByStatus {
		if err := validateBoundedField(
			"monitoring_summary.summary.deployments.by_status key",
			status,
			maxFieldRunes,
		); err != nil {
			return err
		}
		if count < 0 {
			return fmt.Errorf(
				"monitoring_summary.summary.deployments.by_status contains a negative count",
			)
		}
		normalizedStatus := sanitizeBoundedText(
			status,
			maxFieldRunes,
			&result.RedactedValues,
		)
		if normalizedStatus == "" {
			return fmt.Errorf("monitoring_summary.summary.deployments.by_status contains an empty key")
		}
		if _, exists := normalizedByStatus[normalizedStatus]; exists {
			return fmt.Errorf(
				"monitoring_summary.summary.deployments.by_status contains duplicate normalized keys",
			)
		}
		normalizedByStatus[normalizedStatus] = count
	}
	observation.Summary.Deployments.ByStatus = normalizedByStatus

	runtimeHealth := make(
		[]appdeploy.RuntimeHealthSnapshot,
		0,
		len(observation.Summary.RuntimeHealth),
	)
	for index, snapshot := range observation.Summary.RuntimeHealth {
		fresh, err := nestedTimestampFresh(
			fmt.Sprintf(
				"monitoring_summary.summary.runtime_health[%d].last_checked_at",
				index,
			),
			snapshot.LastCheckedAt,
			generatedAt,
			now,
			maxAge,
			maxFutureSkew,
		)
		if err != nil {
			return err
		}
		if !fresh {
			result.StaleSources = appendUnique(
				result.StaleSources,
				"monitoring_summary.runtime_health",
			)
			continue
		}
		if err := normalizeRuntimeHealth(
			&snapshot,
			fmt.Sprintf("monitoring_summary.summary.runtime_health[%d]", index),
			maxFieldRunes,
			&result.RedactedValues,
		); err != nil {
			return err
		}
		runtimeHealth = append(runtimeHealth, snapshot)
	}
	observation.Summary.RuntimeHealth = runtimeHealth

	alarms := make(
		[]appdeploy.DeploymentAlarmSummary,
		0,
		len(observation.Summary.Alarms),
	)
	for index, alarm := range observation.Summary.Alarms {
		fresh, err := nestedTimestampFresh(
			fmt.Sprintf("monitoring_summary.summary.alarms[%d].latest_at", index),
			alarm.LatestAt,
			generatedAt,
			now,
			maxAge,
			maxFutureSkew,
		)
		if err != nil {
			return err
		}
		if !fresh {
			result.StaleSources = appendUnique(
				result.StaleSources,
				"monitoring_summary.alarms",
			)
			continue
		}
		if alarm.Count < 0 {
			return fmt.Errorf(
				"monitoring_summary.summary.alarms[%d].count is negative",
				index,
			)
		}
		if err := validateBoundedField(
			fmt.Sprintf(
				"monitoring_summary.summary.alarms[%d].latest_deployment_id",
				index,
			),
			alarm.LatestDeploymentID,
			maxFieldRunes,
		); err != nil {
			return err
		}
		alarm.Severity = sanitizeBoundedText(
			alarm.Severity,
			maxFieldRunes,
			&result.RedactedValues,
		)
		alarm.ErrorCode = sanitizeBoundedText(
			alarm.ErrorCode,
			maxFieldRunes,
			&result.RedactedValues,
		)
		alarm.LatestStage = sanitizeBoundedText(
			alarm.LatestStage,
			maxFieldRunes,
			&result.RedactedValues,
		)
		alarm.LatestMessage = sanitizeBoundedText(
			alarm.LatestMessage,
			maxMessageRunes,
			&result.RedactedValues,
		)
		alarms = append(alarms, alarm)
	}
	observation.Summary.Alarms = alarms
	return nil
}

func normalizeRuntimeHealth(
	snapshot *appdeploy.RuntimeHealthSnapshot,
	label string,
	maxFieldRunes int,
	redactedValues *int,
) error {
	if err := validateBoundedField(
		label+".target_profile_id",
		snapshot.TargetProfileID,
		maxFieldRunes,
	); err != nil {
		return err
	}
	snapshot.Status = sanitizeBoundedText(
		snapshot.Status,
		maxFieldRunes,
		redactedValues,
	)
	snapshot.RuntimeHealth = sanitizeBoundedText(
		snapshot.RuntimeHealth,
		maxFieldRunes,
		redactedValues,
	)
	return nil
}

type timestampedLog struct {
	item      appdeploy.DeploymentLog
	timestamp time.Time
}

func normalizeLogs(
	items []appdeploy.DeploymentLog,
	observedAt time.Time,
	now time.Time,
	maxAge time.Duration,
	maxFutureSkew time.Duration,
	maxLogs int,
	maxMessageRunes int,
	maxFieldRunes int,
	result *NormalizedContext,
) ([]appdeploy.DeploymentLog, error) {
	candidates := make([]timestampedLog, 0, len(items))
	for index, item := range items {
		timestamp, err := time.Parse(time.RFC3339Nano, strings.TrimSpace(item.Timestamp))
		if err != nil {
			result.DroppedLogs++
			continue
		}
		fresh, err := nestedTimestampFresh(
			fmt.Sprintf("deployment_logs.items[%d].timestamp", index),
			timestamp,
			observedAt,
			now,
			maxAge,
			maxFutureSkew,
		)
		if err != nil {
			return nil, err
		}
		if !fresh {
			result.DroppedLogs++
			result.StaleSources = appendUnique(
				result.StaleSources,
				"deployment_logs.items",
			)
			continue
		}
		item.Timestamp = timestamp.Format(time.RFC3339Nano)
		if err := normalizeLogItem(
			&item,
			fmt.Sprintf("deployment_logs.items[%d]", index),
			maxMessageRunes,
			maxFieldRunes,
			&result.RedactedValues,
		); err != nil {
			result.DroppedLogs++
			continue
		}
		candidates = append(candidates, timestampedLog{
			item:      item,
			timestamp: timestamp,
		})
	}

	sort.SliceStable(candidates, func(left int, right int) bool {
		return candidates[left].timestamp.After(candidates[right].timestamp)
	})
	if len(candidates) > maxLogs {
		result.DroppedLogs += len(candidates) - maxLogs
		candidates = candidates[:maxLogs]
	}
	resultItems := make([]appdeploy.DeploymentLog, 0, len(candidates))
	for _, candidate := range candidates {
		resultItems = append(resultItems, candidate.item)
	}
	return resultItems, nil
}

func normalizeLogItem(
	item *appdeploy.DeploymentLog,
	label string,
	maxMessageRunes int,
	maxFieldRunes int,
	redactedValues *int,
) error {
	if err := validateBoundedField(label+".request_id", item.RequestID, maxFieldRunes); err != nil {
		return err
	}
	if err := validateBoundedField(
		label+".deployment_id",
		item.DeploymentID,
		maxFieldRunes,
	); err != nil {
		return err
	}
	item.Level = sanitizeBoundedText(item.Level, maxFieldRunes, redactedValues)
	item.Component = sanitizeBoundedText(item.Component, maxFieldRunes, redactedValues)
	item.Stage = sanitizeBoundedText(item.Stage, maxFieldRunes, redactedValues)
	item.ErrorCode = sanitizeBoundedText(item.ErrorCode, maxFieldRunes, redactedValues)
	item.Message = sanitizeBoundedText(item.Message, maxMessageRunes, redactedValues)
	return nil
}

func validateBoundedField(label string, value string, maxRunes int) error {
	if len([]rune(value)) > maxRunes {
		return fmt.Errorf("%s exceeds the maximum of %d characters", label, maxRunes)
	}
	for _, character := range value {
		if unicode.IsControl(character) {
			return fmt.Errorf("%s contains a control character", label)
		}
	}
	return nil
}

func sanitizeBoundedText(value string, maxRunes int, redactedValues *int) string {
	redacted, count := redactSensitiveText(value)
	if redactedValues != nil {
		*redactedValues += count
	}
	redacted = strings.Map(func(character rune) rune {
		if unicode.IsControl(character) {
			return ' '
		}
		return character
	}, redacted)
	return truncateRunes(strings.TrimSpace(redacted), maxRunes)
}

func appendUnique(values []string, value string) []string {
	for _, existing := range values {
		if existing == value {
			return values
		}
	}
	return append(values, value)
}

func cloneMonitoringObservation(source MonitoringObservation) MonitoringObservation {
	result := source
	result.Summary.RuntimeHealth = append(
		[]appdeploy.RuntimeHealthSnapshot(nil),
		source.Summary.RuntimeHealth...,
	)
	result.Summary.Alarms = append(
		[]appdeploy.DeploymentAlarmSummary(nil),
		source.Summary.Alarms...,
	)
	if source.Summary.Deployments.ByStatus != nil {
		result.Summary.Deployments.ByStatus = make(
			map[string]int,
			len(source.Summary.Deployments.ByStatus),
		)
		for key, value := range source.Summary.Deployments.ByStatus {
			result.Summary.Deployments.ByStatus[key] = value
		}
	}
	return result
}

func redactSensitiveText(value string) (string, int) {
	if strings.TrimSpace(value) == "" {
		return value, 0
	}
	privateKeyMatches := privateKeyMarker.FindAllStringIndex(value, -1)
	if len(privateKeyMatches) > 0 {
		return "[REDACTED PRIVATE KEY]", len(privateKeyMatches)
	}
	count := len(sensitiveAssignment.FindAllStringIndex(value, -1))
	value = sensitiveAssignment.ReplaceAllString(value, "$1[REDACTED]")
	bearerMatches := bearerValue.FindAllStringIndex(value, -1)
	count += len(bearerMatches)
	value = bearerValue.ReplaceAllString(value, "Bearer [REDACTED]")
	return value, count
}

func truncateRunes(value string, limit int) string {
	runes := []rune(value)
	if limit <= 0 || len(runes) <= limit {
		return value
	}
	return string(runes[:limit])
}
