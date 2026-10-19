# RAGPulse 공개 요청 표본

- 저자 공식 저장소: [RAGPulse](https://github.com/flashserve/RAGPulse/tree/7da286becf0f049b2bcb1e5a11d9ba8eb638eff4), 고정 commit `7da286becf0f049b2bcb1e5a11d9ba8eb638eff4`.
- 원본: [`data/0_trace.jsonl`](https://github.com/flashserve/RAGPulse/blob/7da286becf0f049b2bcb1e5a11d9ba8eb638eff4/data/0_trace.jsonl), 9,961,760바이트, Git blob SHA-1 `a058ecc31c56648b20709a8c211f21dd97c7d08b`, 내려받은 바이트 SHA-256 `017b54972ba3c6c4d52c6cc6967f4168630c381ac58a3b5625a45e795ffa13fa`.
- [README](https://github.com/flashserve/RAGPulse/blob/7da286becf0f049b2bcb1e5a11d9ba8eb638eff4/README.md)는 약 6주에 걸쳐 **표집된** 34,987개의 RAG 요청이라고 설명한다. `timestamp`는 시작 시각 12:00부터의 상대 초, `input_length`는 입력 토큰 수다. 원본 JSON에서는 시각이 숫자 **문자열**이다.
- [공식 LICENSE](https://github.com/flashserve/RAGPulse/blob/7da286becf0f049b2bcb1e5a11d9ba8eb638eff4/LICENSE)는 MIT다.

`0_trace.jsonl`은 원본 그대로 보존한다. 이 표본에서 기록이 없는 시간은 전체 서비스에서 요청이 없었다는 뜻이 아니다. 이 연구는 인프라·VM 조치나 사용자 지연 결과를 이 파일에 결합하지 않는다.
