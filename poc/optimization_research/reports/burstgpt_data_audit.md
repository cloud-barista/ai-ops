# BurstGPT 독립 AI 요청 기록 품질 감사

## 결론

[BurstGPT v2.0](https://github.com/HPMLL/BurstGPT/releases/tag/v2.0)의 `_3` 파일은 Alibaba GenTD26과 독립적인 LLM 서비스 요청 기록이다. 모델 ID와 요청 시각, 입력 토큰 수, 전체 응답 경과시간이 있어 별도 서비스에서 **예측 절차**를 재검증할 후보가 된다. 다만 5,344,021건 중 **3,175,935건(59.4%)의 `Elapsed time`이 정확히 0초**이고, 2,794,838건은 응답 토큰이 양수인데 경과시간이 0초다. 공식 자료는 0초가 실제 초미만 응답인지, 반올림인지, 결측 표시인지 정의하지 않는다. 전체 요청에 대한 정확한 지연시간 라벨로 바로 채택할 수 없다.

## 원본 확인

[`fetch_burstgpt_v2.py`](../scripts/fetch_burstgpt_v2.py)가 저자의 v2.0 release asset을 받아 크기 231,682,327 bytes와 SHA256 `2299986a07388aa303ec2c41d1131e756db650a39ed6ef9dfe7cc3d7f9a43b8f`를 검증한다. 출처와 [CC BY 4.0](https://github.com/HPMLL/BurstGPT/blob/7eb2c4f8350f8a6985272386f5c14af1f678b299/LICENSE)은 [`SOURCE.md`](../data/external/burstgpt_v2/SOURCE.md)에 기록했다. 실제 열은 `Timestamp`, `Session ID`, `Elapsed time`, `Model`, `Request tokens`, `Response tokens`, `Total tokens`, `Log Type`다. `_1`·`_2` release 파일에는 `Elapsed time` 열이 없어 라벨 검증에 사용할 수 없다.

[`audit_burstgpt_v2.py`](../scripts/audit_burstgpt_v2.py)로 전체 5,344,021행을 읽었다. 숫자 파싱 오류, 토큰 합계 불일치, 시각 역전은 0건이다. 상대 시각은 19,440,110~28,943,983초이며 day index 225~334의 110일이 모두 있다. 날짜와 절대 시각은 공개 자료만으로 복원할 수 없다. 경과시간은 정수 초, 최대 714초다.

| 항목 | 전체 건수 |
| --- | ---: |
| 경과시간 0초 | 3,175,935 |
| 경과시간 양수 | 2,168,086 |
| 응답 토큰 0개 | 387,963 |
| 경과시간 양수·응답 토큰 양수 | 2,161,220 |
| 경과시간 0초·응답 토큰 양수 | 2,794,838 |
| 세션 ID 없음 | 5,110,404 |

0초 비율은 day 225에서 3.0%, day 247에서 92.6%, day 271에서 94.9%, day 300에서 89.7%로 크게 바뀐다. `ChatGPT | API log`에서 4,395,665건 중 2,809,862건(63.9%)이 0초이고, `GPT-4 | Conversation log`는 75,500건 중 3,881건(5.1%)이다. 0초 기록 중 응답 토큰 64개 이상인 요청도 120,122건이다. 이 패턴은 0초를 실제 완료시간으로 읽기 전에 기록 방식과 반올림 규칙을 확인해야 함을 보여준다. 원인을 특정할 근거는 없다.

## 검증 시 주의점

- 응답 토큰 수와 총 토큰 수는 사후 결과다. 요청 도착 시 예측에 넣으면 누출된다. 세션 ID는 API 로그에서 비어 있으며, 모델·요청 토큰·로그 유형과 상대 시각이 기본 입력 후보이다.
- 실제 완료 관측 시각은 `Timestamp + Elapsed time`이지만 0초 행은 가용 시각도 불확실하다. 보수적으로 양수 경과시간·양수 응답 토큰 부분집합을 평가할 수 있으며, 이는 **전체 534만 요청이 아닌 선택된 216만 요청**에 대한 결과로만 해석해야 한다.
- `Elapsed time`은 요청 제출부터 전체 응답까지의 LLM 종단간 지연이다. GenTD26의 이미지 생성 실행시간과 단위는 모두 초이지만 구성 요소가 달라 수치나 학습기를 직접 이전할 수 없다. 같은 시계열 분할·보정 절차의 재현성만 평가할 수 있다.
- 원본은 시각 순서로 정렬돼 있으므로 뒤 구간 검증이 가능하다. 컷오프 전에 제출된 요청도 `Timestamp + Elapsed time`이 컷오프 뒤이면 당시 학습·보정에 사용할 수 없다. 별도 모니터링·VM 증설 사건이 없으므로 이 데이터도 Scale-out/in 효과의 정답이 아니다.

재현 명령:

```bash
python scripts/fetch_burstgpt_v2.py
python scripts/audit_burstgpt_v2.py
```

원본 전체 검사 결과는 `audit.json` (`../data/external/burstgpt_v2/audit.json`)에 있다. 이 파일은 요청별 자료가 아니라 날짜·모델·로그 유형별 집계다.
