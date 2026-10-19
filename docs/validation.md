# 검증 범위

## 저장소에서 실행하는 검사

각 Go 모듈에서 `go test ./...`와 `go vet ./...`를 실행합니다. 모듈은 `llmop/go/service-control-api`, `resource_ops`, `deployment_agent/go/service-control-api`, `deployment_agent/go/aiops-guard`입니다.

플랫폼 전체 검사는 Windows와 PowerShell이 필요합니다. 아래 명령은 Windows의 Git Bash 기준입니다. 일부 실행 소유권 및 mutex 검사는 Linux와 macOS에서 지원하지 않습니다.

```bash
python -m pip install -r platform/requirements-test.txt
node --test platform/tests/*.test.mjs
python -m unittest discover -s platform/tests -p 'test_*.py'
python -m unittest discover -s platform/runtime-vm -p 'test_*.py'
```

외부 모델, 원본 DB 또는 실행 서버가 필요한 선택적 검사는 명시적 환경변수가 없으면 제외합니다. 제외한 검사를 성공한 실제 실행으로 계산하지 않습니다.

## 별도 환경에서 확인한 실행

- 로컬 CPU: BERT Tiny 프로세스의 복제본 변화와 실제 추론, 관측 및 종료를 확인했습니다. 동일 PC의 프로세스 수이며 VM 수가 아닙니다.
- AWS CPU VM: m6a.2xlarge의 응용 배포, 실제 추론, Prometheus 관측, 운영 판단과 자원 정리를 확인했습니다.
- AWS GPU VM: g5.xlarge의 NVIDIA A10G에서 모델과 입력의 CUDA 배치, 실제 추론 64회, 요청 오류 0건, KEEP 판단, VM·연결 디스크 정리를 확인했습니다.
- 실제 CPU VM 수: 별도 실험에서 명시적 적용 요청에 따른 1→2→1→0과 총 256회 추론, 오류 0건을 확인했습니다. 자동 정책의 GPU VM 증감을 검증한 결과는 아닙니다.
- 워크숍 촬영: AWS CPU VM의 실제 추론 64회, 요청 오류 0건과 KEEP 1→1을 보여줍니다. 촬영에서 Revision 증가나 성능 개선을 입증한 것은 아닙니다.

클라우드 실행은 2026-10-17~19의 별도 검증입니다. 계정 정보, 원본 실행 저널과 VM 식별자는 공개 패키지에 넣지 않습니다. 이번 공개 저장소의 단위 검사와 구분합니다.

## 해석상 제한

HTTP 응답시간은 관리 프록시와 SSH/SSM 전송 시간을 포함할 수 있으므로 모델 연산 시간과 직접 비교하지 않습니다. 오프라인 정책의 예측·재생 결과는 자동 운영 효과, CPU/GPU 성능 우위 또는 실제 AWS 비용 절감의 증거가 아닙니다. 기관 정식 인터페이스 연동, 장기 안정성, 실제 AI 반도체 배포는 별도 검증 대상입니다.
