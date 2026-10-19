# HAS-GPU 추론 요청 자료의 학습 적격성 게이트

2026-09-28 기준. [공식 Zenodo 아티팩트](https://zenodo.org/records/15596511)의 `HAS-GPU.zip`와 [공식 GitHub 저장소](https://github.com/KontonGu/HAS-GPU)를 확인했다. 결론은 **요청 기록만 사용한 다음 10·30초 지연 p95의 탐색 예측은 GO**, **스케일 조치 효과나 운영 정책 학습은 NO-GO**다. 예측 결과를 독립적인 외부 검증으로 부르거나 새 VM·GPU로 일반화하지 않는다.

## 권리와 원본 고정

[Zenodo 공식 레코드 API](https://zenodo.org/api/records/15596511)의 `metadata.license.id`는 `cc-by-4.0`이다. 웹 페이지에도 CC BY 4.0 권리 정보가 내장돼 있지만, 텍스트 보기에서는 `Copyright 2025 HAS-GPU Authors` 문구만 드러난다. [Zenodo의 권리 설명](https://support.zenodo.org/help/en-gb/2-content/21-can-i-get-permission-to-use-a-specific-record)은 개별 레코드의 라이선스가 재사용 조건을 정한다고 명시한다. 따라서 출처와 변경 내용을 표시하는 내부 연구 학습은 이 레코드의 CC BY 4.0 조건으로 진행할 수 있다. [GitHub README](https://github.com/KontonGu/HAS-GPU/blob/master/README.md)는 코드에 Apache 2.0 문구를 싣는다. 그 문구만으로 결과 CSV의 권리를 따로 추정하지 않는다.

원본 ZIP은 62,262,205 B, 게시된 MD5는 `b8da5cea33e59b9b21dab8163633118a`다. [범위 감사 스크립트](../scripts/audit_has_gpu_clock_ranges.py)는 ZIP의 중앙 목차, 요청 작성 코드, README, 18개 실행의 요청·GPU CSV 쌍만 HTTP Range로 읽는다. 총 155회·1,005,185 B를 받아 각 선택 멤버의 CRC와 SHA-256을 확인했다. 전체 ZIP을 받지 않았으므로 게시 MD5 전체 검증은 하지 않았다. 18개 요청 CSV의 SHA-256은 GitHub 커밋 `31f8ea29ca184193dbc7523736d77c817ddce980`의 같은 경로 파일과 일치한다. 정확한 멤버 경로·해시는 [감사 JSON](../artifacts/has_gpu_clock_ranges_v1.json)에 있다.

재현 명령: `cd /home/researcher/ai-mcmp/research && python3 scripts/audit_has_gpu_clock_ranges.py`.

## 요청과 GPU 시각의 의미

[요청 생성 코드](https://github.com/KontonGu/HAS-GPU/blob/31f8ea29ca184193dbc7523736d77c817ddce980/validation/client/test_batch.py)는 `requests.post` 전후 `time.time()`의 차이를 `duration`에 **초 단위**로 기록하고, 응답을 받은 뒤 `datetime.now().isoformat()`을 `timestamp`에 쓴다. 따라서 CSV `timestamp`는 요청 시작 시각이 아니라 **완료 시각**이다. 시작 시각은 `timestamp - duration`으로 근사한다. 이 지연은 클라이언트에서 본 HTTP 왕복시간이며 순수 모델 실행시간이 아니다. `datetime.now()` 결과에는 시간대가 없고, 사용된 벽시계의 동기화 오차도 기록되지 않았다.

18개 요청 CSV는 61,021행이다. `duration` 범위는 0.037967~6.997230초였고, 음수·0·비유한값·30초 초과값은 없었다. 요청 완료 시각의 CSV 행 순서 역행도 없었다. GPU CSV는 2,633행이며, 시각은 모두 `Z`로 끝나고 행 순서 역행이 없었다. **요청의 시간대 없는 시각을 UTC로 해석하는 가정**을 둘 때, 요청 완료 60,791/61,021건이 같은 실행의 GPU 관측 범위 안에 든다. 이 겹침은 시각 정렬의 실무상 근거지만, 두 장비의 시계 동기화나 정확한 요청별 GPU 상태를 증명하지 않는다.

[컨트롤러 기록 코드](https://github.com/KontonGu/HAS-GPU/blob/31f8ea29ca184193dbc7523736d77c817ddce980/internal/controller/persistentReconcile.go)는 `time.Now().Format(time.RFC3339)`으로 GPU 기록 시각을 쓰고, `sm_partition`·`quota`라는 Pod 설정을 기록한다. 공개 CSV 중 `2-resnet` 1실행만 이 형태다. 다른 17실행의 GPU 열은 `sm_usage`, `quota_usage`, `pod_count`로 집계돼 있으나, 이 집계 열을 만드는 변환 코드와 단위는 확인되지 않았다. 17실행에서는 `sm_usage`가 30·50, `quota_usage`가 10~100의 9개 값, `pod_count`가 전부 1이다. `2-resnet`은 `sm_partition=30`, `quota`가 10·30·50·70·90이다. 모든 실행의 GPU 기록은 `kgpu1`의 같은 `gpu_id`를 가리킨다. 이름에 `usage`가 들어 있어도 물리 GPU 사용률이라고 단정할 수 없다. **첫 예측 모델의 입력에서 GPU 열을 제외한다.**

## 표본 수와 판정 범위

18개 실행은 BERT 8, ResNet 4, ConvNeXt 3, MobileNet 3으로 나뉜다. GPU 관측 구간 안에서 서로 겹치지 않는 10초 미래 창은 515개이며, 역산한 시작 요청이 20건 이상인 창은 486개다. 30초 창은 163개 중 162개가 같은 조건을 채운다. GPU 관측 간격마다 만든 서로 **겹치는** 창은 각각 2,542개와 2,363개지만 독립 실험 수로 세지 않는다. 이 창 수는 후속 요청이 기록을 마칠 충분한 여유, 과거 입력 창의 요청 수, 학습·시험 분리를 적용하기 **전**의 상한 성격이다.

요청 로그만으로 사전 30초 완료 요청의 도착률·지연을 만들고, 다음 30초에 **시작한** 요청의 p95를 예측하는 탐색은 가능하다. 출발점은 과거 p95 유지값과 훈련 구간 평균 같은 단순 기준선이다. 같은 실행에서 인접 창을 훈련·시험에 나누지 말고, 실행 전체 및 모델 계열 전체 제외를 구분해 평가해야 한다. 모델 계열 제외는 4개 계열뿐이므로 전이 가능성을 탐색하는 근거이며, 별도 장비·응용의 외부 검증은 아니다. `timestamp - duration`으로 시작 시각을 만들 때 마지막 구간의 미완료 요청이 누락될 수 있으므로, 기록 종료 근처의 목표 창은 제외해야 한다.

이 자료에는 실제 스케일 명령 ID·발행 시각·Pod Ready 시각이 연결돼 있지 않다. 17개 실행의 `pod_count=1`이고 quota 변화는 상태 관측이다. 그러므로 학습한 지연 예측을 `SCALE_OUT/IN`의 실제 효과, 대체 quota의 반사실 효과, VM 생성시간 예측으로 해석하지 않는다.
