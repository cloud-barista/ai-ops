import assert from 'node:assert/strict';
import test from 'node:test';

import { summarizeEvidence, comparableWorkloads, evaluateHistoricalFit } from '../evidence.mjs';

const evidence = {
  schema_version: 'khu-campaign-evidence/v1',
  verified_run_count: 11,
  campaign_goal_satisfied: false,
  automatic_selection_ready: false,
  totals: {
    canonical_raw_bytes: 1075425283,
    raw_target_bytes: 1073741824,
    raw_target_reached: true,
    request_trace_bytes: 952860675,
    trace_record_count: 1232208,
    execution_count: 2278,
    unique_model_count: 9,
    paired_workload_count: 66,
  },
  coverage: {
    largest_two_run_raw_share: 0.896399,
    paired_workloads_with_two_runs_per_option: 0,
  },
  paired_workloads: [
    { profile_name: 'grid', load_condition: 'exhaustive_grid', options: [{ spec_id: 'cpu' }, { spec_id: 'gpu' }] },
    { profile_name: 'bert-tiny-latency', load_condition: 'nominal', options: [{ spec_id: 'cpu' }, { spec_id: 'gpu' }] },
    { profile_name: 'single', load_condition: 'nominal', options: [{ spec_id: 'cpu' }] },
  ],
};

test('summarizes verified storage without treating records as unique inputs', () => {
  const summary = summarizeEvidence(evidence);
  assert.equal(summary.rawMiB, 1025.61);
  assert.equal(summary.traceSharePercent, 88.6);
  assert.equal(summary.traceRecordCount, 1232208);
  assert.equal(summary.verifiedRunCount, 11);
  assert.equal(summary.rawTargetReached, true);
  assert.equal(summary.campaignGoalSatisfied, false);
  assert.equal(summary.automaticSelectionReady, false);
  assert.equal(summary.largestTwoRunSharePercent, 89.6);
  assert.equal(summary.repeatedPairCount, 0);
  assert.equal(summary.pairedWorkloadCount, 66);
});

test('rejects an unknown export schema', () => {
  assert.throws(() => summarizeEvidence({ ...evidence, schema_version: 'other' }), /schema/);
});

test('shows only exact paired workloads with nominal comparisons first', () => {
  assert.deepEqual(comparableWorkloads(evidence).map((row) => row.profile_name),
    ['bert-tiny-latency', 'grid']);
});

test('assesses SLOs from historical measurements while keeping single-run evidence visible', () => {
  const cohort = {
    options: [
      { spec_id: 'gpu', independent_run_count: 1, median_latency_p95_ms: 12, median_throughput_examples_per_second: 800 },
      { spec_id: 'cpu', independent_run_count: 3, median_latency_p95_ms: 14, median_throughput_examples_per_second: 500 },
    ],
  };
  const result = evaluateHistoricalFit(cohort, { maxP95Ms: 13, minThroughput: 600 });
  assert.deepEqual(result.map(({ status, evidence }) => [status, evidence]), [
    ['observed_within', 'single_run'],
    ['observed_outside', 'repeated_runs'],
  ]);
  assert.equal(evaluateHistoricalFit(cohort, { maxP95Ms: null, minThroughput: null })[0].status, 'unassessed');
  assert.throws(() => evaluateHistoricalFit(cohort, { maxP95Ms: -1 }), /positive/);
});
