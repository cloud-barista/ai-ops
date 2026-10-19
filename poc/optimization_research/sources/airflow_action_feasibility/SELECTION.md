# Airflow 단일 실험 사전 선택

2026-09-28. `db.tar.gz`의 파일 목록이나 실험 결과를 보기 전에
`experiments.pdf`의 설정표만 이용해 `PerformanceFeedback_3_0_5__154`를
선택했다. 이 실험은 WL III(`3_0`) subset 1의 워크플로 5개,
PerformanceFeedback/MA5, autoscaler 간격 15초, 예산 30/0,
small/large 자원 각 8개로 명시되어 있어 작은 DB의 이벤트 연결
가능성을 시험하기에 적합하다. 결과가 부적합해도 다른 실험으로
바꾸지 않는다.

아카이브에서 이름이 이 디렉터리와 일치하는 멤버만 추출한다.
`stat_log`의 ResourceAllocated/ResourceDeallocated, ResourceIdle/Busy,
TaskStarted/TaskFinished, WorkflowArrived/Finished 또는 `dag_run`과
`task_instance` 시각을 조사한다. 각 조치의 동일 자원 ID 및 후속
작업 이벤트 매칭 건수와 매칭률을 보고한다. 같은 시각에 여러 조치가
있거나 자원 ID가 비어 있으면 별도로 센다. 이 한 실험으로
대체 조치의 인과 효과를 주장하지 않는다.

출처: [Zenodo 2635573](https://zenodo.org/records/2635573)의
`experiments.pdf`(60,012 B, MD5 `1dbeef18281de69261b7af71241279f2`),
`db.tar.gz`(4,179,532,095 B, MD5 `ad17be54eca6a1f2d4b964a7518af96d`).
