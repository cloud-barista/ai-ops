# H100/vLLM 시작 실험 원본

- 게시자: [Cold Start Latency on Serverless Inference](https://github.com/mkurup27/cold-start-latency-on-serverless-inference), commit `39e20fbbbc26722650a9115b704fea52d6e32766`.
- 원본 파일: [`data/final.jsonl`](https://github.com/mkurup27/cold-start-latency-on-serverless-inference/blob/39e20fbbbc26722650a9115b704fea52d6e32766/data/final.jsonl), 이 디렉터리의 `final.jsonl`에 원문 그대로 보관.
- 라이선스: [원본의 MIT LICENSE](https://github.com/mkurup27/cold-start-latency-on-serverless-inference/blob/39e20fbbbc26722650a9115b704fea52d6e32766/LICENSE)를 원문 그대로 로컬 LICENSE (`LICENSE`)에 함께 보관했다. 원본의 copyright·permission notice를 자료 사본과 함께 유지한다.
- SHA-256: `e70ff5175249a090ce3c2ece3e17251c27fb9e2429d03526b4d70e854d61c07b`; 크기 638,869 B; JSONL 200행.
- 수집일: 2026-09-29 UTC. 웹에서 고정 commit의 raw GitHub 파일을 받아 위 해시를 확인했다.

원본은 실험자가 제어한 H100·vLLM·Qwen3 캐시 조건 시작 실험이다. C0/C1/C3은 `sweep1`, C2/C4는 `rerun`으로 실행 묶음이 다르다. `vllm.markers`의 사건 시각은 기록되지만 판단기에 실제 전달된 시각은 없다.
