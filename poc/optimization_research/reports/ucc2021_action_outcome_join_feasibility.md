# UCC2021 OpenStack 실험: 스케일 조치와 요청 결과의 결합 가능성

2026-09-28 확인. **조치 명령의 발행 시각·ID와 요청별 지연의 결합은 확인되지 않아 정책 학습에 NO-GO**다. 반면 같은 실행의 서버 시작 시각과 요청별 지연은 실행 시작 시각을 이용해 **관측상 정렬할 수 있다**. 서버 시작은 조치 명령 자체가 아니므로 두 수준을 구분해야 한다.

## 실제 아카이브에서 확인한 값

[Zenodo 원본](https://zenodo.org/records/5618882)의 `UCC2021-data.tar.bz2` 첫 **1,048,576 B**만 HTTP Range로 받았다(`206`, 전체 크기 **948,445,354 B**). 이 접두부 SHA-256은 `dbc84777afc1f26e32dd706798c68d3f42e66d5aa611b7e33f6af800b72f6f91`이다. 전체 파일은 받지 않아 게시된 MD5를 직접 검증하지 않았다. [재현 스크립트](../scripts/audit_ucc2021_archive_prefix.py)는 이 범위와 해시를 확인하고 메모리에서만 읽으며, 첫 대형 클라이언트 로그의 시작 부분에서 멈춘다.

| 같은 `lin-04` 실행의 원본 멤버 | 직접 관찰한 값 | 해석 |
| --- | --- | --- |
| `data/distwalk-lin-04-instance-2.log` | `Server started at: 2021-09-09T12:18:31Z` | VM 안의 `distwalk` 서버 시작이다. Senlin 조치 명령의 발행 시각은 아니다. |
| `data/distwalk-lin-04.log` | 첫 요청 `t: 1409 us, elapsed: 4430 us, req_id: 0, thr_id: 4, sess_id: 0`; 압축 전 크기 539,611,911 B | 요청별 상대시각과 지연이 같은 실행 로그에 있다. 접두부에서는 첫 요청만 읽었다. |
| `data/distwalk-lin-04-pred.json` | 예측 CPU 메트릭 82개, 2021-09-09 11:24–12:47 UTC | 메트릭 시계열이다. 알람이나 스케일 명령의 이벤트 로그가 아니다. |

[공식 실행 메타데이터](https://github.com/giacomolanciano/UCC2021-predictive-auto-scaling-openstack/blob/25092893f424b5b001e7439269eb8cb12bb07662/notebooks/constants.py)는 `lin-04`를 **2021-09-09 11:05:01–12:47:29 UTC**로 기록한다. 따라서 위 서버 시작은 실행 시작으로부터 **73분 30초 뒤**다. [로그 변환 스크립트](https://github.com/giacomolanciano/UCC2021-predictive-auto-scaling-openstack/blob/25092893f424b5b001e7439269eb8cb12bb07662/distwalk-log2csv.sh)는 각 요청의 `t`와 `elapsed`를 `-times.csv`로 꺼낸다. `start_real + t`를 요청 절대시각의 근사값으로 써서 서버 시작 전후 요청 지연을 같은 실행 안에서 나눌 수 있다. `start_real`은 초 단위이고 [실행 스크립트](https://github.com/giacomolanciano/UCC2021-predictive-auto-scaling-openstack/blob/25092893f424b5b001e7439269eb8cb12bb07662/run.sh)가 이 시각을 기록한 뒤 클라이언트를 실행하므로, 정확한 마이크로초 단위 절대시각은 보장되지 않는다.

## 조치 발행 시각은 왜 결합할 수 없는가

[Heat 템플릿](https://github.com/giacomolanciano/UCC2021-predictive-auto-scaling-openstack/blob/25092893f424b5b001e7439269eb8cb12bb07662/openstack/heat/senlin-auto-scaling.yaml)은 Monasca 알람이 Senlin의 `CLUSTER_SCALE_OUT/IN` webhook을 호출하도록 정의한다. 하지만 [실행 스크립트](https://github.com/giacomolanciano/UCC2021-predictive-auto-scaling-openstack/blob/25092893f424b5b001e7439269eb8cb12bb07662/run.sh)는 클라이언트 로그·서버 로그·Monasca 측정값만 내보낸다. [서버 로그 내보내기](https://github.com/giacomolanciano/UCC2021-predictive-auto-scaling-openstack/blob/25092893f424b5b001e7439269eb8cb12bb07662/server-logs-export.sh)는 **실행 종료 시점에 ACTIVE인 멤버**만 열거한다. 중간에 삭제된 VM, 명령 실패, 명령 ID와 발행 시각은 이 경로로 보존되지 않는다. 실제 접두부의 서버 로그에도 시작 시각만 있다. 공개 [파일 목록](https://zenodo.org/records/5618882) 역시 별도의 Senlin 조치 내보내기를 명시하지 않는다.

서버 시작을 VM 생성 시각으로 역산할 수도 없다. [Heat 템플릿](https://github.com/giacomolanciano/UCC2021-predictive-auto-scaling-openstack/blob/25092893f424b5b001e7439269eb8cb12bb07662/openstack/heat/senlin-auto-scaling.yaml)은 인스턴스의 `user_data`에서 기본 **330초 대기 후** 서버 시작을 로그에 적는다. 앞에는 VM 생성·부팅·초기화 시간이 추가된다. `lin-04-instance-0.log`의 서버 시작은 실험 전날이고 `instance-1.log`도 실험 시작 전이어서, 파일의 숫자만으로 그 실행에서 발행된 조치나 초기 VM 수를 복원할 수 없다.

**연구 사용 범위:** 단일 실험의 서버 시작 관측과 요청 지연을 시각상 연결하는 데이터 처리 검증에는 쓸 수 있다. 실제 `SCALE_OUT/IN` 판단 결과, 발행 시각, 준비 완료까지의 시간, 대체 조치의 성능 효과를 학습·검증하는 근거로는 쓰지 않는다. 이 판정은 확인한 접두부와 게시자의 내보내기 코드에 기반한다. 전체 아카이브의 다른 로그 내용까지 부재를 증명한 것은 아니다. 추가 실험에는 `run_id, app_id, action_id, action_type, issued_at, target_vm, ready_at, status`와 요청 시각·지연을 함께 수집해야 한다.

재현 명령: `/home/researcher/ai-mcmp`에서 `python3 poc/optimization_research/scripts/audit_ucc2021_archive_prefix.py`를 실행한다. 외부 의존 패키지와 저장 공간은 필요하지 않다.
