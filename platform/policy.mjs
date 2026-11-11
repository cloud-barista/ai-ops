/** Historical CPU advisory. This module has no cloud execution capability. */
const supportedCpu = new Map([
  ['aws+us-west-2+m6a.large', 2],
  ['aws+us-west-2+m6a.2xlarge', 8],
]);
const positive = (value) => Number.isFinite(value) && value > 0;

function validMeasurements(option) {
  const cores = supportedCpu.get(option.spec_id);
  return option.device === 'cpu' && cores !== undefined &&
    option.cpu_core_limit === cores && option.cpu_threads === cores &&
    [option.throughput_min, option.median_throughput, option.throughput_max,
      option.p95_min_ms, option.median_p95_ms, option.p95_max_ms].every(positive) &&
    option.throughput_min <= option.median_throughput &&
    option.median_throughput <= option.throughput_max &&
    option.p95_min_ms <= option.median_p95_ms && option.median_p95_ms <= option.p95_max_ms;
}

function freshPrices(snapshot, now) {
  const checked = Date.parse(snapshot?.checked_at_utc);
  const expires = Date.parse(snapshot?.valid_until_utc);
  return snapshot?.region === 'us-west-2' && snapshot.operating_system === 'Linux' &&
    snapshot.purchase_option === 'OnDemand' && snapshot.currency === 'USD' &&
    Number.isFinite(checked) && Number.isFinite(expires) && checked <= now && now < expires;
}

export function evaluateBenchmarkPolicy(report, {
  cohortId, minThroughput = null, maxBatchP95Ms = null,
  inputCount = 1000000, nowUtc = new Date().toISOString(),
} = {}) {
  if (report?.schema_version !== 'khu-cpu-policy/v1' || !Array.isArray(report.cohorts)) {
    throw new Error('Invalid benchmark policy data');
  }
  if ([minThroughput, maxBatchP95Ms].some((value) => value !== null && !positive(value)) ||
      !Number.isSafeInteger(inputCount) || inputCount <= 0 || inputCount > 1e12 ||
      !Number.isFinite(Date.parse(nowUtc))) {
    throw new Error('Invalid requirement, work quantity or evaluation time');
  }
  const result = {
    status: 'unsupported_workload', options: [], hourlySelection: null, workSelection: null,
    serviceSloVerified: false, currentAvailabilityChecked: false, cloudExecutionEnabled: false,
  };
  const cohort = report.cohorts.find((row) => row.cohort_id === cohortId);
  if (!cohort) return result;
  const prices = report.price_snapshot;
  const pricesFresh = freshPrices(prices, Date.parse(nowUtc));
  result.pricesFresh = pricesFresh;
  result.options = (cohort.options ?? []).map((option) => {
    const reasons = [];
    if (!validMeasurements(option)) reasons.push('invalid_evidence');
    const runs = option.run_ids;
    if (!Array.isArray(runs) || runs.some((id) => typeof id !== 'string' || id.length === 0) ||
        new Set(runs).size !== runs.length || runs.length < 3 || option.independent_run_count !== runs.length) {
      reasons.push('insufficient_independent_runs');
    }
    if (minThroughput !== null && option.throughput_min < minThroughput) reasons.push('throughput_below_requirement');
    if (maxBatchP95Ms !== null && option.p95_max_ms > maxBatchP95Ms) reasons.push('batch_p95_above_requirement');
    const rate = prices?.hourly_usd?.[option.spec_id];
    const priced = pricesFresh && positive(rate);
    const processingSeconds = positive(option.median_throughput) ? inputCount / option.median_throughput : null;
    return {
      ...option, reasons, eligible: reasons.length === 0,
      hourly_usd: priced ? rate : null,
      estimated_processing_seconds: processingSeconds,
      estimated_compute_cost_usd: priced && processingSeconds !== null ? rate * processingSeconds / 3600 : null,
    };
  });
  if (minThroughput === null && maxBatchP95Ms === null) {
    result.status = 'inputs_required';
    return result;
  }
  const eligible = result.options.filter((option) => option.eligible);
  if (!eligible.length) {
    result.status = 'no_supported_candidate';
    return result;
  }
  if (eligible.some((option) => option.hourly_usd === null)) {
    result.status = 'price_unavailable';
    return result;
  }
  const bySpec = (a, b) => a.spec_id.localeCompare(b.spec_id);
  result.hourlySelection = [...eligible].sort((a, b) => a.hourly_usd - b.hourly_usd || bySpec(a, b))[0];
  result.workSelection = [...eligible].sort((a, b) => a.estimated_compute_cost_usd - b.estimated_compute_cost_usd || bySpec(a, b))[0];
  result.status = 'ready';
  return result;
}
