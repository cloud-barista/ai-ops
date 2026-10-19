# 1GB 이하 공개 자료의 스케일 상태·사용자 결과 결합 감사

2026-09-28 기준. 이번 감사는 기존 [Google·gym-hpa·Airflow 조사](public_scale_action_dataset_audit.md)에 없는 소형 자료를 직접 확인했다. **기록된 스케일 명령과 요청별 결과가 동일 앱 ID·시각으로 연결되는 자료는 아직 확인하지 못했다.** 가장 가까운 실측 자료는 아래 PAX이며, 온라인 VM 수의 변화와 같은 실험의 사용자 처리량·응답시간을 연결할 수 있다.

**후속 결정:** 아래 표의 PAX `GO`는 원시 파일을 폴더·시각으로 기술적으로 연결할 수 있다는 뜻이다. [사전 전후 창 적격성 검사](pax_transition_gap_bracket_v2_findings.md)에서는 안정된 부하의 변화가 6건(증가 1, 감소 5)에 그쳐 성능 비교를 중단했다. 이후 별도 점검에서 결과값을 열람했으므로 같은 원본의 새 평가는 독립 시험으로 취급하지 않는다.

| 자료 | 원본 크기·권한 | 실제 필드와 결합 가능성 | 판정 |
| --- | --- | --- | --- |
| [PAX: Cassandra 오토스케일 실험](https://zenodo.org/records/1136276), [논문 원문](https://clouds.cis.unimelb.edu.au/papers/PAX-Cassandra-NOMS2018.pdf) | `PAX.zip` **1,307,826 B**, CC BY 4.0. Zenodo MD5와 직접 받은 원본의 MD5가 모두 `56cf6c11f79e0cffadeb2414784a789b`. | 각 실험 폴더의 `runningVMs.csv`는 `날짜,시각,온라인 Cassandra 노드 수`를, `results.csv`는 `datetime,throughput,readC,readRT,updateC,updateRT`를 담는다. `clients.csv`의 `datetime,clients`로 동시 사용자 수를 확인할 수 있다. 동일 폴더와 시각으로 결합되지만 행별 앱 ID와 명령 ID는 없다. | **GO: 관측된 VM 활성 수와 후속 성능의 기술적 결합. NO-GO: 명령 효과·대체 조치 결과·AI-App 정책 학습.** |
| [H100 LLM token-aware autoscaling 실험 원본](https://github.com/asm582/token-aware-autoscaling), [실험 보고서](https://github.com/asm582/token-aware-autoscaling/blob/main/experiments/2026-08-20-staged-ramp/EXPERIMENT-REPORT.md) | 저장소 `experiments/`의 추적 파일 합계 **1,254,736 B**, Apache 2.0. | Qwen3-32B의 3개 실행. `autoscaling-timeline.csv`는 257·160·359행의 시간·prefill/decode 희망 또는 관측 replica·Ready 수·트리거 값이다. `stage_N_lifecycle_metrics.json`에는 단계별 요청 수·TTFT·요청 지연·처리량 요약이 있다. 공개 아티팩트에는 요청별 시각 및 명령 이벤트가 없어 단계 수준의 기술 비교만 가능하다. | **NO-GO: 전후 요청 결과를 특정 조치에 연결하는 학습.** GPU LLM 사례 분석 자료로는 유용하다. |
| [OpenStack Monasca 예측 스케일 실험](https://zenodo.org/records/5618882), [동반 저장소](https://github.com/giacomolanciano/UCC2021-predictive-auto-scaling-openstack) | `UCC2021-data.tar.bz2` **948,445,354 B**, CC BY 4.0. | 게시자가 실행 ID별 `distwalk-…log`, 실측 메트릭 `…-real.json`, 요청별 지연 `…-times.csv`를 열거한다. 실제 스케일 발행 이벤트와 이 파일의 시간·ID 매칭은 압축 원본을 열어야 알 수 있다. 이번에는 대용량 다운로드를 하지 않았다. | **보류:** VM 생성 실험이라는 점은 유망하나 행 결합을 검증하지 않았으므로 GO 판정 불가. |
| [컨테이너 오토스케일 시뮬레이션 자료](https://zenodo.org/records/4159582) | ZIP **273,678 B**, CC BY 4.0. | 원본을 열어 확인한 `result1–4.csv`는 `Task id,CPU Usage,Memory Usage,Container Type,Arrival Rate,…,Processing Time,Processing Cost`이며 실행 명령 시각이나 전후 사용자 지연이 없다. | **NO-GO:** 정적 시뮬레이션 결과다. |

## PAX 결합 검증과 한계

PAX 원본의 `README.txt`는 `runningVMs.csv`의 마지막 값을 **온라인 Cassandra 노드 수**로, `results.csv`의 `readRT`를 YCSB 읽기 요청의 응답시간으로 설명한다. 응답시간 단위는 README에 명시되지 않아 임의로 ms라고 표시하지 않는다. 논문은 Azure에서 이미 마련한 VM을 활성화·휴면시키고 Cassandra 파티션을 동기화하는 방식을 설명한다. 따라서 관측 대상은 **실제 활성 노드 변화**이지만 CSP VM 생성 명령이나 AI 추론 GPU Pod의 변화는 아니다.

9개 실험 폴더에서 `runningVMs.csv`의 연속 관측값이 달라진 **58회**를 찾았다. 사건은 `(폴더, 마지막 이전 노드 수의 관측 시각, 처음 새 노드 수의 관측 시각, 이전 수, 새 수)`로 정의한다. 실제 명령 시각은 이 두 관측 시각 **사이 어딘가**에 있으며 원본에는 기록되지 않는다. 이 구간의 중앙 길이는 **186.7초**, 최대 **311.8초**다. 온라인 노드 수를 처음 새 값으로 관측한 시간을 명령 시각으로 쓰면 수분의 시간 오차가 생긴다.

각 사건에서 마지막 이전 관측 **직전 3분**과 처음 새 관측 **직후 3분**의 `results.csv`를 분리해 확인했다. 58회 모두 양쪽에 최소 10행의 처리량·응답시간 기록이 있고, 이 창 안에 다른 **관측된 노드 수 전이**는 없다. 예를 들어 `Validation/Step Proactive`에서 `2017-08-21 09:46:02.494424`에 2개, `09:49:11.020616`에 8개를 관측했으며 동일 폴더의 `results.csv`는 전 `09:46:00`, 후 `09:49:20` 측정값을 담는다. 이 결합은 폴더를 실험 식별자로 사용한다. 원본 행에 전역 앱 ID는 없다.

부하 교란은 크다. 위 두 3분 창의 평균 `clients` 수 차이가 이전 평균의 10% 이내인 사건은 **9/58회**뿐이다. `clients`가 같아도 요청률·읽기/쓰기 혼합·핫 파티션은 달라질 수 있다. 논문도 일부 실행에서 질의 구성을 바꿨다고 명시한다. 또한 `runningVMs.csv`는 활성 노드의 **수**만 보이므로 같은 수를 유지하며 다른 파티션의 VM으로 교체하는 과정은 포착하지 못한다. 전후 평균의 차이를 노드 증감의 인과 효과로 해석할 수 없다.

**권장 용도:** PAX로 폴더·시각 결합, 결과 창의 누락 검사, 관측 상태 변화 뒤의 처리량·응답시간 예측 절차를 검증한다. 58회, 특히 부하가 거의 일정한 9회는 새 스케일 정책을 학습·검증하기에 충분한 독립 조치 자료라고 보기 어렵다. KHU의 `SCALE_OUT/IN` 모델 성능이나 GPU VM 일반화의 근거로 사용하지 않는다. 같은 앱·리소스에 대해 명령 ID·발행 시각·실제 준비 시각·요청별 결과를 수집해야 그 연구를 직접 검증할 수 있다.

## 보관과 재현

공식 `PAX.zip` (`../data/external/pax_autoscale_feasibility/PAX.zip`)과 원본의 `README.txt` (`../data/external/pax_autoscale_feasibility/extracted/PAX/README.txt`), `Validation/Step Proactive`의 `runningVMs.csv` (`../data/external/pax_autoscale_feasibility/extracted/PAX/Validation/Step%20Proactive/runningVMs.csv`)·`results.csv` (`../data/external/pax_autoscale_feasibility/extracted/PAX/Validation/Step%20Proactive/results.csv`)·`clients.csv` (`../data/external/pax_autoscale_feasibility/extracted/PAX/Validation/Step%20Proactive/clients.csv`)를 보관했다. 압축본 SHA-256은 `db15cfa75f05d44020a3f5c4f6f4930cbb1c2518dd3884391917615cea52a23a`다. 원본 ZIP에는 LICENSE 파일이 없으므로 `zenodo_record_provenance.json` (`../data/external/pax_autoscale_feasibility/zenodo_record_provenance.json`)에 게시자의 CC BY 4.0 표시와 MD5를 보존하고 `LICENSE_SOURCE.md` (`../data/external/pax_autoscale_feasibility/LICENSE_SOURCE.md`)에 공식 라이선스 링크를 명시했다.

`/home/researcher/ai-mcmp`에서 아래 명령으로 보관한 공식 압축본의 예시 파일을 다시 검증·추출할 수 있다. 기존 파일이 있으면 내용만 대조한다.

```bash
python - <<'PY'
from pathlib import Path
import hashlib
import zipfile

archive_path = Path('poc/optimization_research/data/external/pax_autoscale_feasibility/PAX.zip')
assert hashlib.md5(archive_path.read_bytes()).hexdigest() == '56cf6c11f79e0cffadeb2414784a789b'
with zipfile.ZipFile(archive_path) as archive:
    for name in ('PAX/README.txt',
                 'PAX/Validation/Step Proactive/runningVMs.csv',
                 'PAX/Validation/Step Proactive/results.csv',
                 'PAX/Validation/Step Proactive/clients.csv'):
        content = archive.read(name)
        destination = archive_path.parent / 'extracted' / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            assert destination.read_bytes() == content
        else:
            destination.open('xb').write(content)
        print(destination, len(content))
PY
```
