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

const reasonLabels = {
  invalid_evidence: '측정 근거 확인 필요',
  insufficient_independent_runs: '독립 VM 3회 반복 부족',
  throughput_below_requirement: '최소 처리량 미달',
  batch_p95_above_requirement: '배치 p95 상한 초과',
};
const statusLabels = {
  inputs_required: '최소 처리량 또는 배치 p95 상한을 입력하세요.',
  unsupported_workload: '해당 모델·리비전·배치 조건의 비교 근거가 없습니다.',
  no_supported_candidate: '현재 CPU 후보에서 요구조건을 만족하는 반복 측정 근거가 없습니다.',
  price_unavailable: '비용 순위를 정하려면 AWS 가격을 다시 확인해야 합니다.',
  ready: '과거 측정 범위에서 요구조건을 만족하는 CPU 후보를 비용으로 비교했습니다.',
};
const shortSpec = (value) => value.split('+').at(-1);
const money = (value, digits = 4) => value === null ? '확인 필요' : `$${value.toFixed(digits)}`;

function addCell(row, value, tag = 'td') {
  const cell = document.createElement(tag);
  cell.textContent = value;
  row.append(cell);
}

function renderPolicy(report, cohort, input) {
  const result = evaluateBenchmarkPolicy(report, { ...input, cohortId: cohort.cohort_id });
  const status = document.querySelector('#policyStatus');
  status.textContent = statusLabels[result.status];
  status.dataset.status = result.status;
  const cards = document.querySelector('#policySelections');
  cards.replaceChildren();
  for (const [label, option, cost] of [
    ['상시 서비스 · 시간당 비용', result.hourlySelection, 'hourly'],
    [`정해진 작업량 · 입력 ${input.inputCount.toLocaleString('ko-KR')}건`, result.workSelection, 'work'],
  ]) {
    const card = document.createElement('div');
    const name = document.createElement('span');
    const spec = document.createElement('strong');
    const detail = document.createElement('small');
    name.textContent = label;
    spec.textContent = option ? shortSpec(option.spec_id) : '추천 보류';
    detail.textContent = option ? (cost === 'hourly' ? `${money(option.hourly_usd)}/시간` :
      `${money(option.estimated_compute_cost_usd)} · 계산 약 ${(option.estimated_processing_seconds / 60).toFixed(1)}분`) :
      '요구조건과 근거를 확인하세요.';
    card.append(name, spec, detail);
    cards.append(card);
  }
  const body = document.querySelector('#policyComparisonBody');
  body.replaceChildren();
  for (const option of result.options) {
    const row = document.createElement('tr');
    if (option.spec_id === result.hourlySelection?.spec_id || option.spec_id === result.workSelection?.spec_id) {
      row.classList.add('policy-selected');
    }
    addCell(row, shortSpec(option.spec_id), 'th');
    addCell(row, `${option.cpu_core_limit}`);
    addCell(row, `${option.independent_run_count}`);
    addCell(row, `${option.throughput_min.toFixed(1)}–${option.throughput_max.toFixed(1)}`);
    addCell(row, `${option.p95_max_ms.toFixed(2)}`);
    addCell(row, money(option.hourly_usd));
    addCell(row, money(option.estimated_compute_cost_usd));
    addCell(row, option.reasons.length ? option.reasons.map((reason) => reasonLabels[reason]).join(' · ') :
      result.status === 'inputs_required' ? '조건 입력 대기' : '과거 관측상 충족');
    body.append(row);
  }
  document.querySelector('#policyWorkloadNote').textContent =
    `${cohort.model_id} · 리비전 ${cohort.model_revision.slice(0, 12)} · ` +
    `batch ${cohort.batch_size} · max length ${cohort.max_length}. ` +
    'VM별 독립 3회 결과의 최소 처리량과 최대 배치 p95로 필터링합니다. 비용은 회차별 처리량 중앙값으로 추정합니다.';
}

async function loadPolicy() {
  const status = document.querySelector('#policyStatus');
  if (!status) return;
  try {
    const response = await fetch('./data/khu-cpu-policy.json', { cache: 'no-store' });
    if (!response.ok) throw new Error('Policy data unavailable');
    const report = await response.json();
    if (report.schema_version !== 'khu-cpu-policy/v1' || !report.cohorts?.length) throw new Error('Invalid policy data');
    const select = document.querySelector('#policyCohort');
    select.replaceChildren();
    for (const cohort of report.cohorts) {
      const option = document.createElement('option');
      option.value = cohort.cohort_id;
      option.textContent = `${cohort.profile_name} · batch ${cohort.batch_size} · 길이 ${cohort.max_length}`;
      select.append(option);
    }
    const inputs = [document.querySelector('#policyMinThroughput'), document.querySelector('#policyMaxP95'), document.querySelector('#policyInputCount')];
    const show = () => {
      try {
        const cohort = report.cohorts.find((row) => row.cohort_id === select.value);
        const [min, max, count] = inputs.map((item) => item.value.trim());
        renderPolicy(report, cohort, {
          minThroughput: min === '' ? null : Number(min), maxBatchP95Ms: max === '' ? null : Number(max), inputCount: Number(count),
        });
      } catch {
        status.textContent = '처리량·p95는 양수, 작업량은 1 이상 1조 이하의 정수로 입력하세요.';
        status.dataset.status = 'invalid_input';
        document.querySelector('#policySelections').replaceChildren();
        document.querySelector('#policyComparisonBody').replaceChildren();
      }
    };
    select.addEventListener('change', show);
    inputs.forEach((item) => item.addEventListener('input', show));
    document.querySelectorAll('[data-policy-example]').forEach((button) => {
      button.addEventListener('click', () => {
        const [profile, min, max] = button.dataset.policyExample.split('|');
        const cohort = report.cohorts.find((row) => row.profile_name === profile);
        if (!cohort) return;
        select.value = cohort.cohort_id;
        inputs[0].value = min;
        inputs[1].value = max;
        show();
      });
    });
    const date = new Date(report.price_snapshot.checked_at_utc).toLocaleString('ko-KR', { hour12: false });
    document.querySelector('#policyPriceDate').textContent =
      `AWS us-west-2 · Linux On-Demand · 가격 확인 ${date} · 24시간 이후 재확인 필요`;
    show();
  } catch {
    status.textContent = '검증된 CPU 비교 자료를 읽지 못했습니다. 추천을 보류합니다.';
    status.dataset.status = 'data_unavailable';
  }
}

if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', loadPolicy, { once: true });
  else loadPolicy();
}
