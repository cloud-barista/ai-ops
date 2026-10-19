import assert from 'node:assert/strict';
import test from 'node:test';

const module = await import('../policy.mjs').catch((error) => {
  if (error.code === 'ERR_MODULE_NOT_FOUND') return {};
  throw error;
});

const now = '2026-09-29T04:00:00Z';
const small = 'aws+us-west-2+m6a.large';
const large = 'aws+us-west-2+m6a.2xlarge';
function fixture() {
  return {
    schema_version: 'khu-cpu-policy/v1',
    price_snapshot: {
      region: 'us-west-2', operating_system: 'Linux', purchase_option: 'OnDemand', currency: 'USD',
      checked_at_utc: '2026-09-29T03:00:00Z', valid_until_utc: '2026-09-30T03:00:00Z',
      hourly_usd: { [small]: 0.036, [large]: 0.144 },
    },
    cohorts: [{
      cohort_id: 'tiny', profile_name: 'bert-tiny-latency', model_id: 'model/tiny',
      model_revision: 'revision-1', batch_size: 1, max_length: 64,
      options: [
        { spec_id: small, device: 'cpu', cpu_core_limit: 2, cpu_threads: 2,
          independent_run_count: 3, run_ids: ['s1', 's2', 's3'],
          median_throughput: 100, throughput_min: 90, throughput_max: 110,
          median_p95_ms: 10, p95_min_ms: 9, p95_max_ms: 25 },
        { spec_id: large, device: 'cpu', cpu_core_limit: 8, cpu_threads: 8,
          independent_run_count: 3, run_ids: ['l1', 'l2', 'l3'],
          median_throughput: 800, throughput_min: 700, throughput_max: 850,
          median_p95_ms: 5, p95_min_ms: 4, p95_max_ms: 8 },
      ],
    }],
  };
}
function evaluate(report = fixture(), input = {}) {
  assert.equal(typeof module.evaluateBenchmarkPolicy, 'function', 'Benchmark policy is not implemented');
  return module.evaluateBenchmarkPolicy(report, {
    cohortId: 'tiny', minThroughput: 80, maxBatchP95Ms: 30, inputCount: 1000000, nowUtc: now, ...input,
  });
}

test('hourly and fixed-work objectives can choose different eligible VMs', () => {
  const result = evaluate();
  assert.equal(result.status, 'ready');
  assert.equal(result.hourlySelection.spec_id, small);
  assert.equal(result.workSelection.spec_id, large);
  assert.ok(Math.abs(result.workSelection.estimated_compute_cost_usd - 0.05) < 1e-12);
  assert.equal(result.workSelection.estimated_processing_seconds, 1250);
});

test('uses the slowest observed throughput rather than the median to filter', () => {
  const result = evaluate(fixture(), { minThroughput: 95 });
  assert.equal(result.hourlySelection.spec_id, large);
  assert.ok(result.options[0].reasons.includes('throughput_below_requirement'));
});

test('uses the largest observed batch p95 rather than the median to filter', () => {
  const result = evaluate(fixture(), { maxBatchP95Ms: 20 });
  assert.equal(result.hourlySelection.spec_id, large);
  assert.ok(result.options[0].reasons.includes('batch_p95_above_requirement'));
});

test('includes equality at the observed throughput and p95 boundaries', () => {
  const result = evaluate(fixture(), { minThroughput: 90, maxBatchP95Ms: 25 });
  assert.equal(result.hourlySelection.spec_id, small);
});

test('does not recommend from absent or unmatched workload requirements', () => {
  assert.equal(evaluate(fixture(), { minThroughput: null, maxBatchP95Ms: null }).status, 'inputs_required');
  assert.equal(evaluate(fixture(), { cohortId: 'another-revision' }).status, 'unsupported_workload');
});

test('does not substitute a cheaper VM that misses both requirements', () => {
  const result = evaluate(fixture(), { minThroughput: 900, maxBatchP95Ms: 2 });
  assert.equal(result.status, 'no_supported_candidate');
  assert.equal(result.hourlySelection, null);
  assert.equal(result.workSelection, null);
});

test('does not count duplicate run IDs as independent repetitions', () => {
  const report = fixture();
  report.cohorts[0].options[0].run_ids = ['s1', 's1', 's1'];
  assert.equal(evaluate(report).hourlySelection.spec_id, large);
});

test('does not recommend a fast, cheap single-run candidate', () => {
  const report = fixture();
  const option = report.cohorts[0].options[0];
  option.independent_run_count = 1;
  option.run_ids = ['s1'];
  assert.equal(evaluate(report).hourlySelection.spec_id, large);
});

test('does not interpret a missing or zero price as free compute', () => {
  for (const value of [undefined, 0, -1, Infinity]) {
    const report = fixture();
    report.price_snapshot.hourly_usd[small] = value;
    const result = evaluate(report);
    assert.equal(result.hourlySelection, null);
    assert.equal(result.status, 'price_unavailable');
  }
});

test('blocks ranking with expired, future or incompatible price snapshots', () => {
  const variations = [
    { valid_until_utc: now },
    { checked_at_utc: '2026-09-30T03:00:00Z' },
    { currency: 'KRW' },
    { region: 'ap-northeast-2' },
    { operating_system: 'Windows' },
    { purchase_option: 'Spot' },
  ];
  for (const variation of variations) {
    const report = fixture();
    Object.assign(report.price_snapshot, variation);
    const result = evaluate(report);
    assert.equal(result.status, 'price_unavailable');
    assert.equal(result.workSelection, null);
  }
});

test('invalid measurements cannot qualify a candidate', () => {
  for (const variation of [
    { throughput_min: NaN }, { median_throughput: 0 }, { p95_max_ms: Infinity },
    { throughput_min: 101 }, { p95_max_ms: 8 }, { cpu_core_limit: 1 }, { device: 'cuda' },
  ]) {
    const report = fixture();
    Object.assign(report.cohorts[0].options[0], variation);
    assert.equal(evaluate(report).hourlySelection.spec_id, large);
  }
});

test('rejects invalid requirements and impossible work quantities', () => {
  for (const input of [
    { minThroughput: 0 }, { maxBatchP95Ms: -1 }, { minThroughput: NaN },
    { maxBatchP95Ms: Infinity }, { inputCount: 0 }, { inputCount: 1.5 },
    { inputCount: Number.MAX_VALUE }, { nowUtc: 'invalid' },
  ]) assert.throws(() => evaluate(fixture(), input), /invalid/i);
});

test('never presents historical advisory as a deployment or availability check', () => {
  const result = evaluate();
  assert.equal(result.serviceSloVerified, false);
  assert.equal(result.currentAvailabilityChecked, false);
  assert.equal(result.cloudExecutionEnabled, false);
});
