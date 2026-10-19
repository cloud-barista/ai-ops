# CB-Tumblebug 1.2 GiB VM 데이터 수집 캠페인

## 무엇을 수집하는가

이 캠페인은 CB-Tumblebug가 AWS VM을 생성한 뒤 실제 Hugging Face 추론을
수행하고, 추론 결과와 CPU/GPU 상태 및 관측 기반 정책 결과를 수집한다.
모델 학습 데이터가 아니라 **VM 추론·운영 실험 데이터**다.

데이터 계보는 다음과 같이 구분한다.

- `/home/researcher/khu-policy-experiment-20260911`: 팀장님이 수집한 원본
  참조 데이터다. 캠페인이 수정하거나 새 수집량으로 계산하지 않는다.
- `/home/researcher/geon-khudata`: 원본을 복사한 작업 폴더이며 기존 V6~V11c
  결과가 들어 있다.
- `runs/tumblebug/campaign-1g-20260923`: 이번 캠페인이 새로 만드는 결과다.
  1.2 GiB 목표에는 이 경로의 성공한 추출 데이터만 포함한다.

CPU VM 원시 데이터와 GPU VM 원시 데이터는 각각 저장한다. 비교 결과는
원시 데이터 자체가 아니라 이 두 데이터에서 계산하는 파생 분석이다. 현재
GPU 후보는 검증된 `g6.8xlarge` 한 종류이므로 CPU 대 GPU 비교는 가능하지만,
GPU 사양 간 비교는 두 번째 GPU 사양을 별도로 검토한 뒤 추가해야 한다.

## 완료 조건

- 전체 정상 목표: 1,288,490,189 bytes (1.2 GiB)
- 절대 상한: 1,610,612,736 bytes (1.5 GiB)
- GPU 데이터: 최소 734,003,200 bytes (700 MiB)
- CPU 데이터: 최소 419,430,400 bytes (400 MiB)
- 각 VM 사양: 성공한 독립 실행 최소 3회
- 모든 아카이브 SHA-256 일치
- 모든 `collection_runs.status`: `complete`
- 실패한 추론: 0건
- 성공 후와 실패 처리 후 모두 VM 자동 삭제

용량에는 성공한 추출 폴더의 SQLite, CSV, 요약 JSON, provenance만 포함한다.
가상환경, 모델 캐시, 아카이브 복사본, 제어 로그, SQLite WAL/SHM 파일은
포함하지 않는다.

## 1. 무료 계획 확인

다음 명령은 VM을 만들지 않으며 AWS 비용이 발생하지 않는다.

```bash
cd "$HOME/geon-khudata"

CAMPAIGN_ROOT="$HOME/geon-khudata/runs/tumblebug/campaign-1g-20260923"
MANIFEST="$HOME/geon-khudata/config/tumblebug-campaign-1g.json"
COLLECTOR="$HOME/geon-khudata/scripts/collect-via-tumblebug.sh"

PYTHONPATH="$PWD/src" python3 scripts/tumblebug_campaign.py plan \
  --manifest "$MANIFEST" \
  --campaign-root "$CAMPAIGN_ROOT" \
  --collector "$COLLECTOR"
```

출력의 `live_cloud_execution`은 `false`여야 한다. 첫 실행 순서는 GPU 1개와
CPU 3종을 라운드별로 섞어, 특정 장치의 데이터만 먼저 쌓이지 않게 한다.

## 2. 무료 CB-Tumblebug 검토

`review`는 사양, 이미지, 리전, 가용성 및 예상 시간당 비용을 조회하지만
VM을 생성하지 않는다. CB-Tumblebug가 실행·초기화된 상태여야 한다.

```bash
cd "$HOME/geon-khudata"

export TUMBLEBUG_PASSWORD="$(
  sed -n 's/^TB_API_PASSWORD=//p' \
    "$HOME/go/src/github.com/cloud-barista/cb-tumblebug/.env"
)"

PYTHONPATH="$PWD/src" python3 scripts/tumblebug_campaign.py review \
  --manifest "$MANIFEST" \
  --campaign-root "$CAMPAIGN_ROOT" \
  --collector "$COLLECTOR"
```

각 항목은 `creationViable=true`여야 한다. 결과와 가격은
`campaign-state.json` 및 각 실행 폴더의 `review-response.json`에 저장된다.

## 3. 유료 실행 경계

아래 명령만 실제 AWS VM을 만든다. 첫 파일럿은 검토가 끝난 첫 4개
(`g6.8xlarge`, `m6a.large`, `m6a.2xlarge`, `m6a.8xlarge`)만 실행한다.
현재 검토 가격과 항목당 최대 120분을 적용한 파일럿 상한은 `$7.6576`이다.

```bash
PYTHONPATH="$PWD/src" python3 scripts/tumblebug_campaign.py run \
  --manifest "$MANIFEST" \
  --campaign-root "$CAMPAIGN_ROOT" \
  --collector "$COLLECTOR" \
  --execute-live-cloud \
  --max-live-entries 4 \
  --max-cost-usd 7.6576
```

`--execute-live-cloud` 또는 양수 비용 한도가 빠지면 실행을 거부한다. 환경에
`KHU_TB_AUTO_DESTROY=0`이나 `KHU_TB_DESTROY_ON_FAILURE=0`이 있으면 역시
실행하지 않는다. 각 항목은 생성 직전에 다시 `review`하며, 누적 최대비용,
디스크 여유, 데이터 상한을 검사한다. 실패하면 다음 VM을 만들지 않고 같은
항목부터 재개할 수 있게 진단 상태를 남긴다.

파일럿이 끝나면 `campaign-summary.json`의 실제 데이터 크기를 확인해 남은
실행 수와 다음 비용 상한을 다시 계산한다. `--max-live-entries`를 생략한 전체
실행은 이 확인 후에만 사용한다.

## 4. 검증과 재개

`run`을 같은 명령으로 다시 실행하면 완료된 항목은 건너뛴다. 데이터 파일을
다시 검증하는 명령은 비용이 들지 않는다.

```bash
PYTHONPATH="$PWD/src" python3 scripts/tumblebug_campaign.py verify \
  --manifest "$MANIFEST" \
  --campaign-root "$CAMPAIGN_ROOT" \
  --collector "$COLLECTOR"
```

최종 `goal_satisfied`가 `true`이고, `tier_coverage`의 모든 항목이
`satisfied=true`여야 한다.

## 5. Windows 백업

서버에서 최종 묶음과 체크섬을 만든다.

```bash
cd "$HOME/geon-khudata/runs/tumblebug"
tar -czf "$HOME/campaign-1g-20260923.tar.gz" campaign-1g-20260923
sha256sum "$HOME/campaign-1g-20260923.tar.gz" \
  > "$HOME/campaign-1g-20260923.tar.gz.sha256"
```

Windows PowerShell에서 두 파일을 내려받은 후 SHA-256을 비교한다.

```powershell
$Base = "C:\Users\developer\Documents\geon-khudata\runs\tumblebug"
scp -P 7877 "ubuntu216@192.0.2.216:/home/researcher/campaign-1g-20260923.tar.gz" $Base
scp -P 7877 "ubuntu216@192.0.2.216:/home/researcher/campaign-1g-20260923.tar.gz.sha256" $Base

$Archive = Join-Path $Base "campaign-1g-20260923.tar.gz"
$Checksum = Join-Path $Base "campaign-1g-20260923.tar.gz.sha256"
$Actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Archive).Hash.ToLower()
$Expected = ((Get-Content -LiteralPath $Checksum -Raw).Trim() -split '\s+')[0].ToLower()
if ($Actual -ne $Expected) { throw "캠페인 백업 해시 불일치" }
"캠페인 Windows 백업 검증: 정상"
```
