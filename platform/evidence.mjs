/** Read-only view of the locally verified KHU campaign export. */

export function summarizeEvidence(report) {
  if (report?.schema_version !== 'khu-campaign-evidence/v1') {
    throw new Error('Unsupported KHU evidence schema');
  }
  const totals = report.totals;
  if (!totals || !Number.isFinite(totals.canonical_raw_bytes) || totals.canonical_raw_bytes <= 0) {
    throw new Error('KHU evidence has no verified raw bytes');
  }
  return {
    rawMiB: Math.round(totals.canonical_raw_bytes / 1048576 * 100) / 100,
    traceSharePercent: Math.round(totals.request_trace_bytes / totals.canonical_raw_bytes * 1000) / 10,
    traceRecordCount: totals.trace_record_count,
    executionCount: totals.execution_count,
    modelCount: totals.unique_model_count,
    verifiedRunCount: report.verified_run_count,
    rawTargetReached: totals.raw_target_reached === true,
    campaignGoalSatisfied: report.campaign_goal_satisfied === true,
    automaticSelectionReady: report.automatic_selection_ready === true,
    largestTwoRunSharePercent: Math.round((report.coverage?.largest_two_run_raw_share ?? 0) * 1000) / 10,
    repeatedPairCount: report.coverage?.paired_workloads_with_two_runs_per_option ?? 0,
    pairedWorkloadCount: totals.paired_workload_count ?? 0,
  };
}

export function comparableWorkloads(report) {
  return (report.paired_workloads ?? [])
    .filter((row) => new Set(row.options?.map((option) => option.spec_id)).size >= 2)
    .sort((a, b) => Number(b.load_condition === 'nominal') - Number(a.load_condition === 'nominal') ||
      a.model_id?.localeCompare(b.model_id ?? '') || a.profile_name.localeCompare(b.profile_name));
}

export function evaluateHistoricalFit(cohort, { maxP95Ms = null, minThroughput = null } = {}) {
  for (const value of [maxP95Ms, minThroughput]) {
    if (value !== null && (!Number.isFinite(value) || value <= 0)) {
      throw new Error('SLO thresholds must be positive finite numbers');
    }
  }
  return cohort.options.map((option) => ({
    ...option,
    evidence: option.independent_run_count >= 2 ? 'repeated_runs' : 'single_run',
    status: maxP95Ms === null && minThroughput === null ? 'unassessed' :
      (maxP95Ms === null || option.median_latency_p95_ms <= maxP95Ms) &&
      (minThroughput === null || option.median_throughput_examples_per_second >= minThroughput)
        ? 'observed_within' : 'observed_outside',
  }));
}

const text = (value) => String(value);
const shortSpec = (spec) => spec.split('+').at(-1);

function cell(row, value, tag = 'td') {
  const item = document.createElement(tag);
  item.textContent = text(value);
  row.append(item);
}

function renderComparison(cohort, tbody, note, maxP95Ms, minThroughput) {
  tbody.replaceChildren();
  const fitLabels = { unassessed: '미검토', observed_within: '관측상 충족', observed_outside: '관측상 미달' };
  for (const option of evaluateHistoricalFit(cohort, { maxP95Ms, minThroughput })) {
    const row = document.createElement('tr');
    cell(row, shortSpec(option.spec_id), 'th');
    cell(row, option.device === 'cuda' ? 'GPU' : 'CPU');
    cell(row, option.cpu_core_limit);
    cell(row, option.independent_run_count);
    cell(row, option.median_latency_p95_ms.toFixed(2));
    cell(row, option.median_throughput_examples_per_second.toFixed(2));
    cell(row, `${fitLabels[option.status]}${option.evidence === 'single_run' ? ' · 1회' : ''}`);
    tbody.append(row);
  }
  const runs = cohort.options.map((option) => option.independent_run_count);
  const limited = runs.some((count) => count < 2);
  note.textContent = `${cohort.model_id} · batch ${cohort.batch_size} · max length ${cohort.max_length} · ` +
    `${cohort.load_condition}. ${limited ? '일부 VM의 독립 반복은 1회뿐입니다. ' : ''}` +
    '같은 VM 회차의 실행은 먼저 중앙값으로 묶어 회차당 같은 가중치로 집계했습니다. ' +
    '위 자연어 배포 요청과 자동 연결되지 않습니다. GPU·VRAM 같은 필수 자원 조건과 현재 VM 가용성은 별도로 확인해야 합니다.';
}

function renderRuns(runs, tbody) {
  tbody.replaceChildren();
  for (const run of runs) {
    const row = document.createElement('tr');
    cell(row, run.run_id, 'th');
    cell(row, shortSpec(run.spec_id));
    cell(row, run.execution_count.toLocaleString('ko-KR'));
    cell(row, run.model_count);
    cell(row, (run.canonical_raw_bytes / 1048576).toFixed(2));
    cell(row, (run.request_trace_bytes / 1048576).toFixed(2));
    tbody.append(row);
  }
}

async function loadEvidence() {
  const status = document.querySelector('#evidenceStatus');
  try {
    const response = await fetch('./data/khu-campaign-evidence.json', { cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const report = await response.json();
    const summary = summarizeEvidence(report);
    const cohorts = comparableWorkloads(report);
    const metrics = document.querySelector('#evidenceMetrics');
    metrics.replaceChildren();
    for (const [label, value] of [
      ['원본 측정 저장량', `${summary.rawMiB.toLocaleString('ko-KR')} MiB`],
      ['반복 요청 기록 비중', `${summary.traceSharePercent}%`],
      ['실측 실행', summary.executionCount.toLocaleString('ko-KR')],
      ['모델 / 검증 회차', `${summary.modelCount} / ${summary.verifiedRunCount}`],
    ]) {
      const item = document.createElement('div');
      const name = document.createElement('span');
      const number = document.createElement('strong');
      name.textContent = label;
      number.textContent = value;
      item.append(name, number);
      metrics.append(item);
    }
    const select = document.querySelector('#evidenceCohort');
    select.replaceChildren();
    for (const [index, cohort] of cohorts.entries()) {
      const option = document.createElement('option');
      option.value = text(index);
      option.textContent = `${cohort.profile_name} · ${cohort.model_id.split('/').at(-1)} · batch ${cohort.batch_size}`;
      select.append(option);
    }
    const tbody = document.querySelector('#evidenceComparisonBody');
    const note = document.querySelector('#evidenceComparisonNote');
    const p95Input = document.querySelector('#evidenceMaxP95');
    const throughputInput = document.querySelector('#evidenceMinThroughput');
    if (cohorts.length) {
      const show = () => {
        const maxP95Ms = p95Input.value.trim() === '' ? null : Number(p95Input.value);
        const minThroughput = throughputInput.value.trim() === '' ? null : Number(throughputInput.value);
        try {
          renderComparison(cohorts[Number(select.value)], tbody, note, maxP95Ms, minThroughput);
        } catch (error) {
          tbody.replaceChildren();
          note.textContent = `요구조건 입력 오류: ${error.message}`;
        }
      };
      select.addEventListener('change', show);
      p95Input.addEventListener('input', show);
      throughputInput.addEventListener('input', show);
      select.value = '0';
      show();
    } else {
      select.disabled = true;
      note.textContent = '동일 모델·리비전·요청 조건으로 비교 가능한 VM 측정이 없습니다.';
    }
    renderRuns(report.runs, document.querySelector('#evidenceRunsBody'));
    status.textContent = `캠페인 ${report.campaign_id} · 아카이브 SHA-256 및 SQLite 검증 ${summary.verifiedRunCount}회차 · ` +
      `원본 1GiB ${summary.rawTargetReached ? '달성' : '미달'} · ` +
      `캠페인 별도 목표 ${summary.campaignGoalSatisfied ? '충족' : '미충족'}`;
    document.querySelector('#evidenceDecisionNote').textContent =
      `요청 기록 ${summary.traceRecordCount.toLocaleString('ko-KR')}건은 반복 측정입니다. ` +
      `원본의 ${summary.largestTwoRunSharePercent}%가 상위 2회차에 집중되어 있고, ` +
      `${summary.pairedWorkloadCount}개 비교 조건 중 모든 VM 사양에서 독립 회차 2회 이상인 조건은 ` +
      `${summary.repeatedPairCount}개입니다. ` +
      '이 화면은 실측 근거 조회이며 자동 자원 추천·VM 생성·배포를 수행하지 않습니다.';
  } catch (error) {
    status.textContent = `KHU 실측 근거를 읽지 못했습니다: ${error.message}`;
  }
}

if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', loadEvidence, { once: true });
  } else {
    loadEvidence();
  }
}
