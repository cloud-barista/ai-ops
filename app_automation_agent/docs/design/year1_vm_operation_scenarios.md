# 1차년도 VM-only 동작 시나리오

## 통합 시나리오

1. 외부 인프라 계층이 CPU 또는 GPU VM을 생성하고 resource snapshot을 제공합니다.
2. 사용자가 AI workload ID와 운영 요구를 입력합니다.
3. service-control이 Ops LLM 정책과 Agent registry를 확인합니다.
4. 실제 VM의 accelerator와 수집된 자원을 workload 요구사항과 비교합니다.
5. capability와 Action이 일치하는 등록 외부 실행 에이전트를 찾습니다.
6. Go Guard 경계를 포함한 비실행 배포·제어 handoff 계획을 생성합니다.
7. 외부 실행 에이전트의 상태 feedback을 다음 판단 입력으로 사용합니다.

## 개별 시나리오 A: GPU VM 적합성

- 입력: NVIDIA L4 VM snapshot, GPU workload 요구사항
- 확인: evidence status, accelerator, CPU·메모리, VRAM, driver·CUDA
- 출력: `provisionally_compatible` 또는 `compatible`, 조건별 checks

## 개별 시나리오 B: 미등록 실행 에이전트

- 입력: VM 적합성 통과, 일치 capability agent 없음
- 확인: service-control이 임의 실행 주체를 만들지 않는지
- 출력: `register_executor_agent` precondition, `execution_status=not_executed`

## 1차년도 경계

컨테이너와 Kubernetes는 사용하지 않습니다. VM 생성은 외부 인프라 계층, 실제 응용 실행은 등록 외부 에이전트, 판단·검증·계획은 본 service-control의 책임입니다.
