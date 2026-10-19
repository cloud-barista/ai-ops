# Resource Scoring Formula

필수 조건을 통과한 후보만 점수화한다. 각 dimension 점수는 0~100이다.

```text
availabilityRatio      = clamp(available / total, 0, 1)
postAllocationHeadroom = clamp((available - required) / available, 0, 1)
dimensionScore         = 100 × (0.65 × availabilityRatio
                              + 0.35 × postAllocationHeadroom)
```

기본 가중치:

- health: 10
- CPU: 30
- memory: 30
- storage: 10, storage requirement가 있을 때만
- accelerator: 30, accelerator requirement가 있을 때만

GPU score는 조건을 만족하는 장치 중 높은 점수부터 필요한 수만큼 선택해 평균낸다. GPU utilization이 있으면 VRAM capacity score 80%, idle score 20%를 사용한다.

```text
gpuScore = 0.8 × vramCapacityScore + 0.2 × (100 - gpuUtilization)
```

활성 dimension의 가중치 합으로 다시 나누며 최종 출력은 소수 둘째 자리로 반올림한다. 동점은 `resource_id` 오름차순이다.
