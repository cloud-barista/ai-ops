# OpenStack Monasca 공개 원본 다운로드 판단

2026-09-28 확인. **현재 환경에서는 전체 원본 다운로드·추출 NO-GO.** 실제 `CLUSTER_SCALE_OUT/IN` 발행 시각·명령 ID가 공개 아티팩트에 있는지 확인되지 않았고, 작은 전송 표본의 속도와 알 수 없는 해제 크기에 비해 검증 이득이 불확실하다. 전체 원본은 받지 않았다.

| 항목 | 확인 결과 |
| --- | --- |
| 공식 출처 | [Zenodo DOI 10.5281/zenodo.5618882](https://zenodo.org/records/5618882), [공식 API 레코드](https://zenodo.org/api/records/5618882). `UCC2021-data.tar.bz2` **948,445,354 B**(약 904.5 MiB), `md5:0f1cc42212e9cd25e3d26b45f0f16232`, CC BY 4.0. [파일 URL](https://zenodo.org/api/records/5618882/files/UCC2021-data.tar.bz2/content). 전체 MD5는 다운로드하지 않아 대조하지 못했다. |
| 부분 전송 | 공식 파일 URL에 `Range: bytes=0-8388607`을 보내자 HTTP **206**, `Accept-Ranges: bytes`, `Content-Range: bytes 0-8388607/948445354`를 받았다. 30초 이하의 단일 표본에서 **25.10초에 7,864,320 B**(약 0.30 MiB/s)를 읽고 연결을 닫았다. 그 속도가 일정하다고 가정하면 전량은 약 **50분**이다. 네트워크 속도는 변동할 수 있다. |
| 압축 구조 | 첫 1 MiB만 메모리에서 부분 해제해 `data/` 및 여러 실험 로그·예측 JSON 헤더를 확인했다. 이어지는 `data/distwalk-lin-04.log`의 tar 헤더에 **539,611,911 B**가 표시됐다. `.tar.bz2`는 순차 스트림이므로 HTTP Range가 가능해도 뒤쪽 파일을 ZIP처럼 곧바로 꺼낼 인덱스가 없다. [게시자 목록](https://zenodo.org/records/5618882)에는 실행 로그·CPU 실측/예측 JSON·요청별 응답시간 CSV 외에 Nova/Octavia VM 이미지 두 개도 포함된다. 정확한 전체 해제 크기는 현재 미확인이다. |
| 로컬 공간 | `/home/researcher/ai-mcmp`와 `/tmp`가 있는 파일시스템의 여유는 **12,030,767,104 B**(약 11.2 GiB). 압축 원본 저장 후에는 약 **11.08 GB**가 남는다. 최소한 위 540 MB 로그와 미확인 크기의 VM 이미지·기타 로그가 추가되므로 전체 해제의 안전한 여유는 확정할 수 없다. |
| 조치 로그 | [실험 저장소의 Heat 템플릿](https://github.com/giacomolanciano/UCC2021-predictive-auto-scaling-openstack/blob/master/openstack/heat/senlin-auto-scaling.yaml)은 Monasca 알람→Senlin `CLUSTER_SCALE_OUT/IN` webhook을 정의한다. 그러나 [게시자 파일 목록](https://zenodo.org/records/5618882)과 [실행 스크립트](https://github.com/giacomolanciano/UCC2021-predictive-auto-scaling-openstack/blob/master/run.sh)는 Senlin 명령/이벤트 로그 내보내기를 명시하지 않는다. `…-times.csv`는 [노트북 처리](https://github.com/giacomolanciano/UCC2021-predictive-auto-scaling-openstack/blob/master/notebooks/results_times.py)에 따르면 실행 시작 기준 요청 시각·지연 두 열이고, `…-real.json`은 Monasca CPU 메트릭이다. 실행 스크립트의 `start_real` 및 [노트북의 실행 메타데이터](https://github.com/giacomolanciano/UCC2021-predictive-auto-scaling-openstack/blob/master/notebooks/constants.py)로 시각 결합을 시도할 수 있다. 이것은 명령 ID·발행 시각의 존재를 입증하지 않는다. |

향후 이 원본을 다시 검토하려면, 먼저 저자 또는 공개된 별도 Senlin 이벤트 기록에서 **실제 명령 발행 시각·대상 그룹 ID·결과 상태**가 제공되는지 확인하는 편이 낫다. 그 자료 없이 큰 아카이브를 받아도 실측 VM 등장 시각과 요청 지연의 **관측상 결합**에 그칠 가능성이 높다. 별도 [PAX 소형 원본 감사](public_subgigabyte_action_outcome_audit.md)의 파일은 그대로 유지했다.
