# AI-MCMP 학습 연구

원본 SQLite를 읽기 전용으로 진단하고, 고정된 추출 데이터로 배치 지연·처리량, 반복 실행의 성능 변동, 관측 GPU 메모리 피크, 로컬 모델 적재시간을 연구하는 코드다. 부족한 사용자 요청·부하·GPU 시계열에는 Alibaba GenTD26·MaaS, BurstGPT, Azure LLM, ETH SwissAI, Microsoft Philly 공개 추적 자료를 각각 사용한다. Google 2011은 기존 배정 아래 관측 CPU·RAM 사용량과 두 자원 동시 피복을 좁게 학습·평가했으며, 배정 후 첫 `EVICT/FAIL` 위험 학습기는 개발 검증에서 미채택했다. Alibaba GPU v2026은 자원 요구량 정답의 적합성을 표본으로 진단했다. 공개 H100/vLLM 자료에서는 시작 단계별 준비시간 예측을 모델 크기 전체 제외 방식으로 평가하고, 별도 llm-d 오토스케일 자료의 목표 replica·Ready 관측 가능 범위를 감사했다. AgentPerfBench에서는 AI 서빙의 한 GPU 형상 실측을 이용한 다른 형상 성능 전이를 모델 전체 제외 방식으로 평가해 직접 회귀 기준선보다 악화된 전이 방법을 미채택했다. Scherm B200 서빙에서는 TP2 실측에서 TP4 성능을 예측하는 모델을 별도 학습했지만, 출력률과 TTFT의 공동 개선이 안정적이지 않아 미채택했다. 출처마다 응용·장비·시각축이 달라 원시 행을 합쳐 학습하지 않는다. 이 디렉터리의 연구 스크립트는 원본 SQLite와 공개 자료를 변경하지 않는다.

- [진단 및 첫 결과](reports/diagnosis_and_first_experiment.md)
- [Argonne vLLM 공개 벤치마크 감사와 새 모델 형상별 지연 전이: 앵커 잔차 보정 미채택](reports/argonne_vllm_shape_transfer_findings.md)
- [Philly 기계별 GPU 위험 보정 뒤 슬롯별 잔차 추가: 공개 개발 구간의 탐색 결과](reports/philly_slot_day_calibration_exploratory_findings.md)
- [Philly 새 기계 51대에서 슬롯별 전날 잔차 보정 확인: 강한 기준선 대비 Brier 2.63% 감소](reports/philly_slot_day_calibration_holdout_findings.md)
- [Philly 슬롯 위험 보정을 플랫폼 중립 계산 모듈로 분리: 저장된 812,729개 판단 전 행 재생](reports/philly_slot_day_core_replay.md)
- [Philly 슬롯별 위험 보정의 가정한 피드백 지연 민감도: 12시간에도 상대 Brier 감소 1.48%](reports/philly_slot_feedback_delay_findings.md)
- [Philly 전날 잔차 보정의 Alibaba MaaS 인스턴스 전이: Brier 0.13% 개선으로 사전 5% 기준 실패](reports/maas_day_ahead_instance_transfer_findings.md)
- [MaaS 전날 인스턴스 보정의 경보 순위 상승과 동점·반복 판단 사후 진단](reports/maas_alert_ranking_posthoc_diagnostic.md)
- [저장 MaaS GPU 위험 모델의 새 ID 787개 단회 시험: 개선했지만 사전 5% 기준 실패](reports/maas_existing_model_new_id_findings.md)
- [llm-d-autoscaling 공개 실행 3개 직접 감사: 요청별 결과는 있으나 조치·Ready 결합 실패](reports/llmd_autoscaling_committed_results_audit.md)
- [MaaS 상태별 전날 보정의 새 ID 795개 단회 시험: 개선했지만 2%·재표집 기준 실패](reports/maas_state_day_calibration_holdout_findings.md)
- [MaaS 전날 상태 보정의 사후 실패 진단: 이득의 72–95%가 최근 고사용률 상태에 집중](reports/maas_state_day_no_go_diagnostic.md)
- [전체 데이터 진단](data/audit_v1/data_audit.md)
- [실험 보고서](artifacts/performance_v1/experiment_report.md)
- [저장 성능 모델의 모델별 입력 지원 범위 수정과 외부 캠페인 재평가](reports/saved_model_condition_support_fix.md)
- [저장 GPU 후보 선택의 예측 여유값 보류: 합성 목표에서 정상 승인 손실·남는 오승인](reports/saved_gpu_margin_abstention_findings.md)
- [저장 GPU 추천 뒤 순차 형상 확인의 사후 재생: 확인 횟수 감소, 일부 최소 형상·GPU시간 손실](reports/sequential_shape_confirmation_findings.md)
- [반복 GPU 벤치마크의 다음 회차 합격 감사: 경계 목표에서 3회 확인의 위험 감소 불일치](reports/repeat_confirmation_findings.md)
- [공유 GPU 후보 격자의 검증·키 재사용: 1,125개 오프라인 SLO 채점 1.608배](reports/decision_inference_microbatch_v3_findings.md)
- [복사된 GPU 후보 격자의 정확한 검증 재사용: v2 대비 오프라인 채점 1.334배](reports/decision_inference_microbatch_v4_findings.md)
- [GPU 후보 묶음 예측 8→64격자: 같은 출력으로 복사 입력 2.007배·고유 격자 3.753배](reports/decision_inference_chunk64_findings.md)
- [GPU 후보 묶음 예측 64→256격자: 같은 출력으로 고유 575격자 1.518배](reports/decision_inference_chunk256_findings.md)
- [64격자 ExtraTrees 2·4 worker: 마지막 자리 출력 차이로 정확 동등성 게이트 실패](reports/decision_inference_workers64_findings.md)
- [기계 판독 지표](artifacts/performance_v1/metrics.json)
- [GPU 후보 선택 연구 보고서](reports/gpu_policy_research.md)
- [GPU 정책별 평가 지표](artifacts/gpu_policy_v1/metrics.json)
- [AgentPerfBench 공개 LLM 서빙 GPU 형상 비교의 원본·지원량 감사](reports/agentperfbench_feasibility.md)
- [AgentPerfBench의 한 형상 계측 후 새 모델 형상 성능 전이 평가 규약](reports/agentperfbench_shape_transfer_protocol.md)
- [AgentPerfBench의 모델 전체 제외 형상비 전이 평가: 직접 회귀보다 악화로 미채택](reports/agentperf_shape_transfer_findings.md)
- [AgentPerfBench 보수적 형상 승인 보정의 모델 지원량 부족](reports/agentperfbench_uncertainty_design.md)
- [AgentPerfBench 요청 일부 실패의 GPU 형상별 학습 정답 적격성](reports/agentperf_partial_failure_shape_feasibility.md)
- [AgentPerfBench A100 4→8의 첫 토큰·토큰당 지연 성분 학습: 결합법 미채택](reports/agentperf_component_pair_findings.md)
- [AgentPerfBench GPU 커널 자료의 앱 수준 가속기 추천 정답 적격성: 미채택](reports/agentperf_kernel_shape_feasibility.md)
- [Scherm B200 공개 서빙의 TP2→TP4 모델 전체 제외 학습: 동시성별 단순 기준선 대비 공동 개선 실패](reports/scherm_b200_tp24_findings.md)
- [Scherm B200 TP2 저부하 세 점에서 고부하 출력률·TTFT 학습: 평균 개선, 모델별 안정성 실패](reports/scherm_b200_load_curve_findings.md)
- [Scherm B200 고부하 곡선의 훈련 모델 전용 수축 선택: 모델별 안정성 재실패](reports/scherm_b200_load_curve_shrinkage_findings.md)
- [FreeInference 공개 GPU·서빙 텔레메트리: 실제 스케일 조치 정답 부재](reports/freeinference_live_telemetry_feasibility_2026_09_29.md)
- [Chutes 공개 요청 추적의 모델별 다음 5분 TTFT 예측 적격성](reports/chutes_serving_signal_feasibility.md)
- [Chutes 인스턴스·캐시 특징의 다음 5분 TTFT p90 학습: 검증 미채택](reports/chutes_ttft_tail_v1_findings.md)
- [Chutes 다음 5분 요청량 p90 학습: 손실 개선·모델별 승리 게이트 실패](reports/chutes_demand_p90_v1_findings.md)
- [Chutes 다음 5분 요청량 p90의 최근 오차 적응: 고정 혼합 대비 내부 검증 미채택](reports/chutes_demand_p90_v2_findings.md)
- [새 AI 모델의 기준 측정 조건부 예측 연구](reports/anchor_conditioned_research_findings.md)
- [기준 측정 연구 비교 그림](artifacts/novel_method_summary_v1/comparison.png)
- [모델당 소수 기준 측정 실험](reports/sparse_anchor_transfer_findings.md)
- [이전 모델 오차 공분산 전이 실험](reports/cross_model_covariance_findings.md)
- [지연·처리량 공동 잔차 전이의 사후 탐색 규약](reports/cross_metric_anchor_transfer_protocol.md)
- [지연·처리량 공동 잔차 전이 평가: 처리량·선택 악화로 미채택](reports/cross_metric_anchor_transfer_findings.md)
- [공분산·평균 오차 원인 분해](reports/covariance_bias_ablation_findings.md)
- [공유 조건 잔차의 순열 대조 실험](reports/covariance_permutation_control_findings.md)
- [BERT 계열 공동 제외 검증](reports/family_holdout_transfer_findings.md)
- [중심화 공분산의 BERT 계열 공동 제외 검증](reports/family_holdout_centered_findings.md)
- [반복 측정의 새 입력 조건 전이 검증](reports/live_repeat_transfer_findings.md)
- [새 입력 조건에서의 원인 분해](reports/covariance_bias_ablation_external_findings.md)
- [두 자원 형상 측정의 실패 실험](reports/dual_axis_sparse_probe_failed_hypothesis.md)
- [기준 형상·자원 반응 잔차를 분리한 GPU2 sentinel 실험](reports/relative_response_sentinel_findings.md)
- [전체 자원 잔차 전이의 실패 실험](reports/joint_resource_residual_failed_hypothesis.md)
- [훈련 자료 기반 이중 SLO 탐색 평가](reports/counterfactual_slo_findings.md)
- [합성 SLO 난도·오류 구성 감사](reports/slo_case_mix_findings.md)
- [동일 측정 예산의 예측기 동의 판단 평가](reports/decision_consensus_findings.md)
- [합성 SLO의 최소 자원 추천 평가](reports/resource_regret_findings.md)
- [지원 범위 게이트 실패 실험](reports/support_aware_transfer_gate_findings.md)
- [훈련 donor 내부검증의 새 조건 전이 실패 실험](reports/support_hybrid_transfer_findings.md)
- [비슷한 과거 모델 가중 전이 실험](reports/donor_similarity_transfer_findings.md)
- [SLO·측정비용 가중 프로브 선택 실험](reports/probe_design_findings.md)
- [관측 잔차 기반 순차 프로브 선택 실험](reports/adaptive_probe_transfer_findings.md)
- [자원 반응의 구조 제약·새 조건 지연 보정 실험](reports/physical_response_transfer_findings.md)
- [반복 GPU 측정의 성능 변동 진단](reports/repeat_variability_findings.md)
- [소수 반복 측정으로 미래 성능분포 예측](reports/repeated_measurement_distribution_findings.md)
- [반복 측정 분포예측 실험 규약](reports/repeated_measurement_distribution_protocol.md)
- [등록 모델 크기와 작업 조건으로 관측 GPU 예약 피크 예측](reports/vram_peak_findings.md)
- [GPU 1개 관측으로 GPU 2개 장치별 예약 피크 예측: 피복률 부족으로 미채택](reports/replica_vram_peak_findings.md)
- [추가 알고리즘 학습 결과 요약](reports/trained_algorithms_v1.md)
- [모델 제외 잔차로 관측 GPU 예약 피크 상한 학습](reports/vram_upper_findings.md)
- [이전 모델 반복분포에서 예측폭 학습](reports/learned_repeat_distribution_findings.md)
- [로컬 모델 적재시간 학습](reports/load_time_findings.md)
- [GPU 2개 프로파일 소요시간 예측의 학습 적격성·강한 단순 기준선](reports/replica_profiling_budget_feasibility_2026_09_29.md)
- [GPU 2개 로컬 프로파일 시간 예산 학습: 피복률·오승인 기준 미달로 미채택](reports/gpu2_profile_time_budget_findings_v1.md)
- [작업 종류별 적재시간 예측 지원 범위](reports/load_support_findings.md)
- [작업 종류·모델 크기 조건부 관측 VRAM 피크 상한](reports/vram_conditional_findings.md)
- [측정 없는 정적 GPU·CPU 후보 추천: 직접 분류와 회귀 비교](reports/cold_start_policy_findings.md)
- [측정 없는 정적 추천의 donor 보정 승인 문턱 사전 규약](reports/cold_start_selective_safety_protocol.md)
- [측정 없는 정적 추천의 선택적 승인 실패 결과](reports/cold_start_selective_safety_findings.md)
- [AI-App 여러 입력 조건의 공통 형상 추천 연구 제안](reports/workload_envelope_recommendation_proposal.md)
- [공통 형상 추천의 사전 자료 적격성 게이트 실패](reports/workload_envelope_gate_findings.md)
- [PAX 온라인 노드 전환의 사전 관측 적격성 실패](reports/pax_transition_gate_findings.md)
- [PAX 전환 관측 공백을 제외한 전후 창의 적격성 재검사](reports/pax_transition_gap_bracket_v2_findings.md)
- [기존 SQLite의 새 응용 요구사항 분석 학습 적격성](reports/sqlite_requirement_analysis_new_task_feasibility.md)
- [원본 SQLite 7개의 응용 요구사항 감독 정답 재점검](reports/sqlite_seven_requirement_supervision_audit.md)
- [배포·스케일 조치 학습 적격성과 SLO 재평가 최적화 규약](reports/scale_decision_feasibility.md)
- [동일 작업의 변경된 SLO 질의에서 예측 재사용: 출력 동등성과 CPU 시간](reports/slo_reuse_findings.md)
- [정적 8후보 평가기 보정의 신규 확인 실험 적격성](reports/static_assessor_calibration_gap.md)
- [동일 후보·다른 SLO 오프라인 API 설계](reports/slo_reuse_api_design.md)
- [동일 작업 SLO 묶음 평가 API의 저장 모델 검증](reports/slo_reuse_many_findings.md)
- [서로 다른 GPU 작업 조건을 묶는 정확한 추론: 4모델 평가 2.228배](reports/cross_workload_microbatch_findings.md)
- [묶음 추론의 중복 검증 제거: 1,125개 질의에서 v1 대비 1.665배](reports/cross_workload_microbatch_v2_findings.md)
- [MLPerf Inference v5.0 공개 결과의 외부 검증 적격성](reports/external_inference_benchmark_feasibility.md)
- [IOG 공개 다중 GPU 추론 벤치마크의 형상 연구 적합성](reports/iog_llm_inference_benchmark_feasibility.md)
- [IOG RTX 4090 GPU 2→4 상대 반응의 채점 전 구현 규약](reports/iog_gpu24_response_protocol.md)
- [IOG RTX 4090 GPU 2→4 상대 반응의 모델 전체 제외 후향 결과](reports/iog_gpu24_response_findings.md)
- [경희대 연구용 GPU 형상 증거 계약 구현 가능성](reports/research_evidence_contract_feasibility.md)
- [저장 모델에서 8후보 연구 증거를 직접 생성하는 호출 경로](reports/gpu_shape_evidence_direct_generation.md)
- [기존 VRAM·로컬 적재시간 학습 모델의 요구 신호 API 적격성](reports/requirement_signal_api_feasibility.md)
- [응용 요구 신호의 요청 결속 연구 증거 계약·생성·검증](reports/requirement_signal_evidence_v0.1.0.md)
- [요구 신호 지원 조건의 모델 제외 예측 재평가](reports/requirement_signal_gate_impact.md)
- [Google 2011 제출 후 배정 지연 라벨의 학습 적격성 실패](reports/google_2011_placement_delay_feasibility.md)
- [Google 2011 배정 후 업데이트·재시도까지의 첫 사건과 후속 종료 감사](reports/google_2011_early_exit_feasibility.md)
- [Google 2011 첫 EVICT/FAIL 위험 학습의 개발 검증 미채택](reports/google_2011_early_exit_risk_v2_findings.md)
- [Google 2011 첫 사건 위험의 교정 평가 규약](reports/google_2011_early_exit_risk_v2_protocol.md)
- [Google 2011 CPU·RAM 동시 관측 상한의 새 시간 구간 시험](reports/google_2011_joint_envelope_findings.md)
- [Google 2011 배정 후 초기 계측 피드백으로 남은 CPU·RAM 관측 피크 예측: 검증 미채택](reports/google_2011_early_feedback_findings_v1.md)
- [Google 2011 초기 계측 피드백 학습의 점수 전 규약](reports/google_2011_early_feedback_protocol_v1.md)
- [Google 2011 job 제출·task 요청 접합과 부하 연구 적격성](reports/google_2011_job_arrival_feasibility.md)
- [Google 2019 공식 GCS collection 사건 10개 확보와 첫 조각 감사](reports/google_2019_collection_events_feasibility.md)
- [Google 2019 전체 10개 사건의 다음 5분 JOB 도착량: 사전 동결 학습기 미채택](reports/google_2019_arrival_model_findings_v1.md)
- [Google 2019 JOB 도착량 점수 전 고정 규약](reports/google_2019_arrival_model_protocol_v1.md)
- [Google 2019 이미 공개된 네 셀의 고부하 위험 분류 탐색: 새 셀 시험 보류](reports/google_2019_high_load_risk_exposed_no_go.md)
- [Google 2019 JOB 제출 후 5초 시작 지연 학습 규약](reports/google_2019_start_delay_protocol.md)
- [Google 2019 JOB 시작 지연 모델의 강한 기준선 재평가와 미채택](reports/google_2019_job_schedule_5s_findings.md)
- [공개 LLM 시작시간 자료의 배포 판단 연구 적합성](reports/public_llm_startup_trace_feasibility.md)
- [H100/vLLM 단계별 준비시간 예측: 강한 단순 기준선과 비교 후 미채택](reports/h100_readiness_eta_findings.md)
- [H100 llm-d 오토스케일 원본의 목표 replica·Ready 관측과 조치 라벨 적격성](reports/token_aware_autoscaling_action_feasibility.md)
- [llm-d 벤치마크의 replica 시계열·Pod 준비·요청별 수집 기능과 공개 실행 자료 적격성](reports/llmd_benchmark_action_data_audit_2026_09_29.md)
- [공개 AI 서비스 요청 실행시간의 90백분위 예측](reports/gentd26_request_latency_findings.md)
- [GenTD26 요청 실패 상태 위험의 검증 통과·사후 반증](reports/gentd26_request_failure_validation_findings.md)
- [GenTD26 요청 실패 상태의 같은 서비스 후행 기간 민감도](reports/gentd26_request_failure_temporal_findings.md)
- [공개 AI 서비스 요청의 모델 ID별 과거 오차 보정](reports/gentd26_subgroup_findings.md)
- [공개 AI 서비스 요청 보정의 시간순 반복 평가](reports/gentd26_walkforward_findings.md)
- [공개 AI 서비스 요청의 커버리지 제약 보정 실험](reports/gentd26_coverage_guard_findings.md)
- [공개 AI 서비스 요청의 선택적 보류 실험](reports/gentd26_selective_guard_findings.md)
- [공개 AI 서비스 QPS·대기열의 다음 5분 위험 예측](reports/gentd26_monitoring_findings.md)
- [공개 AI 서비스 파이프라인 적재 지연의 시간순 예측](reports/gentd26_update_latency_findings.md)
- [GenTD26 파이프라인 응답시간의 새 예측 과제 표본 적합성 감사](reports/gentd26_pipeline_inference_support_no_go.md)
- [AI 서빙 컨테이너의 30분 관측 GPU 메모리 피크 예측](reports/gentd26_gpu_memory_peak_findings.md)
- [공개 AI 서비스 대기열 위험 확률의 시간별 보정 검증](reports/gentd26_queue_calibration_findings.md)
- [Alibaba GenTD26 공개 데이터 출처와 범위](data/external/gentd26/SOURCE.md)
- [Alibaba MaaS 응용별 상대 QPS 예측 연구](reports/maas_trace_findings.md)
- [MaaS 상대 QPS 예측의 응용별·시간별 안정성 점검](reports/maas_robustness_findings.md)
- [Alibaba MaaS 공개 사례의 출처와 범위](data/external/alibaba_maas_2026/SOURCE.md)
- [SwissAI 요청 종료시간 탐색 평가와 직접 p90 기준선](reports/swissai_exploratory_findings.md)
- [SwissAI 활성 모델 미래 10분 요청량 평가 규약](reports/swissai_demand_protocol.md)
- [SwissAI 요청량 예측의 완료 피드백 실패 결과](reports/swissai_demand_findings.md)
- [SwissAI 상대 수요 p90 후속 평가 규약](reports/swissai_scale_adaptive_protocol.md)
- [SwissAI 상대 수요 후속 평가의 표본 게이트 실패](reports/swissai_scale_adaptive_gate_failure.md)
- [SwissAI 요청 오류 상태 위험의 점수 전 평가 규약](reports/swissai_error_status_protocol.md)
- [SwissAI 요청 오류 위험 학습과 지원범위 게이트 실패](reports/swissai_error_risk_findings.md)
- [SwissAI 출력 토큰 p90의 관측 조건부 시간순 학습 규약](reports/swissai_output_tokens_protocol.md)
- [SwissAI 출력 토큰 p90의 실제 학습·검증 미채택 결과](reports/swissai_output_tokens_validation_no_go.md)
- [SwissAI Qwen 입력 버킷 접두의 점수 전 원본·지원도 감사](reports/swissai_qwen_bucket_structure_findings.md)
- [SwissAI Qwen 전체 접두 60분 재사용 학습의 사전 지원량 실패](reports/swissai_qwen_full_prefix_support_no_go.md)
- [SwissAI Qwen 접두 깊이별 재사용의 사후 지원량 탐색](reports/swissai_qwen_prefix_depth_support_exploratory.md)
- [Alibaba MaaS 인스턴스 GPU 위험 예측 사전 규약](reports/maas_instance_risk_protocol.md)
- [Alibaba MaaS 인스턴스 GPU 위험 예측 실패 결과](reports/maas_instance_risk_findings.md)
- [GPU 위험 예측 선행 연구와 계측 품질을 반영한 후속 가설](reports/maas_risk_prior_art_and_next_hypothesis.md)
- [MaaS 상태별·결측 인식 GPU 위험: 점수 전 구현 선택](reports/maas_state_followup_implementation_note.md)
- [MaaS 상태별·결측 인식 GPU 위험 후속 탐색 결과](reports/maas_state_followup_findings.md)
- [Alibaba MaaS SM 관측 상태 예측의 사전 규약](reports/maas_sm_observability_protocol_v1.md)
- [Alibaba MaaS SM 관측 상태의 학습 전 지원량 감사](reports/maas_sm_observability_feasibility_v1.md)
- [Alibaba MaaS SM 관측 상태 모델 학습·개발 검증: 직접 3분류보다 낮은 성능으로 미채택](reports/maas_sm_observability_findings_v1.md)
- [새 인스턴스 ID 시험 추출기의 동결 조건과 합성 검증](reports/maas_bucket1_extractor_readiness.md)
- [Microsoft Philly GPU 30분 고사용률 위험: 사전 동결 시간순 시험 결과](reports/philly_gpu_risk_findings.md)
- [Philly GPU 추적 자료의 출처·전처리 감사](data/external/msr_philly_2017/SOURCE.md)
- [Philly 미사용 기계 홀드아웃 평가 규약·결과](reports/philly_machine_holdout_findings.md)
- [Philly 동결 모델의 30분 GPU 위험 신호 API·예시](reports/philly_gpu_risk_signal_api.md)
- [Philly GPU 위험 신호의 요청 결속·버전형 연구 증거](reports/philly_gpu_risk_evidence_v0.1.0.md)
- [Philly GPU 위험 확률의 연속 확인 경보 탐색: NO-GO](reports/philly_alert_state_machine_exploratory_no_go.md)
- [Philly 미사용 기계 표본의 출처·전처리 감사](data/external/msr_philly_2017_holdout/SOURCE.md)
- [Philly 새 기계의 사전기간 위험 보정 규약·실패 결과](reports/philly_machine_calibration_findings.md)
- [Philly 기계별 위험 보정 실패의 사후 분해](reports/philly_machine_calibration_posthoc_diagnosis.md)
- [Philly GPU 위험 확률의 전날 기계별 보정 사후 개발 분석](reports/philly_day_ahead_calibration_exploratory.md)
- [Philly 미사용 기계 전날 보정 시험 초안](reports/philly_day_ahead_calibration_holdout_protocol_draft.md)
- [Philly 기계별 보정 시험 표본의 출처·전처리 감사](data/external/msr_philly_2017_calibration_holdout/SOURCE.md)
- [Philly 기계 유휴 후 GPU 재활성화 예측: 개발 검증 단계 중단](reports/philly_idle_rebound_validation_no_go.md)
- [Philly 유휴 재활성화 적응 확률 v3: 지원량 통과·검증 성능 미채택](reports/philly_idle_rebound_adaptive_v3_validation_no_go.md)
- [SwissAI 유휴 모델 재도착 위험의 학습 가능성 진단](reports/next_research_idle_return_feasibility.md)
- [SwissAI 유휴 모델 10분 내 재도착 위험: 고정 탐색 시험 결과](reports/swissai_idle_return_findings.md)
- [SwissAI 유휴 모델 재도착 위험: 후행 점수 동결 탐색 규약·미채택 결과](reports/swissai_idle_return_post_findings.md)
- [경희대 담당 연구의 현황과 데이터 활용 결정](reports/khu_research_progress_external_data.md)
- [BurstGPT 요청 입력 기반 출력 토큰 분위수의 시간순 학습·시험](reports/burstgpt_output_tokens_findings.md)
- [BurstGPT 출력 토큰 시험 전 모델 선택 기록](reports/burstgpt_output_tokens_selection_frozen.md)
- [BurstGPT 앞선 기간의 출력 토큰 규칙 재현: 손실 개선과 상한 실패](reports/burstgpt_12_output_tokens_findings.md)
- [BurstGPT 앞선 기간의 시험 전 고정 규약](reports/burstgpt_12_output_tokens_protocol.md)
- [BurstGPT 대화 로그의 1시간 입력 토큰 수요: 검증 피복률 실패·시험 중단](reports/burstgpt_input_demand_validation_no_go.md)
- [BurstGPT 입력 토큰 수요의 학습 블록 보정: GPT-4 검증 피복률 실패·시험 중단](reports/burstgpt_input_demand_v2_validation_no_go.md)
- [BurstGPT 대화 로그의 1시간 출력 토큰 수요: 검증 피복률 실패·시험 중단](reports/burstgpt_output_demand_validation_no_go.md)
- [BurstGPT 1시간 출력 토큰 수요의 검증 전 탐색 규약](reports/burstgpt_output_demand_protocol.md)
- [Azure 코딩 서비스 출력 토큰 p90의 7일 시간순 검증](reports/azure_code_output_tokens_findings.md)
- [Azure 코딩 서비스 시험 전 고정 규약](reports/azure_code_output_tokens_protocol.md)
- [Azure Coding 공개 표본의 다음 5분 입력 토큰 부하 학습·후향 평가](reports/azure_code_input_demand_findings.md)
- [Coding→Conversation 입력 부하 전이의 사전 규약](reports/azure_input_demand_cross_service_protocol.md)
- [Conversation 시각 파서 복구의 단회 규약](reports/azure_input_demand_cross_service_parser_addendum_v1_1.md)
- [Azure Coding→Conversation 입력 부하 모델 이전 평가 결과](reports/azure_input_demand_cross_service_findings_v1_1.md)
- [Azure 입력 수요 모델의 서비스 전이 실패 사후 진단](reports/azure_input_demand_transfer_shift_diagnosis.md)
- [RAGPulse 공개 표본 입력 수요 이전 시험 규약](reports/ragpulse_input_demand_transfer_protocol.md)
- [RAGPulse 시각 문자열 파서 복구 부록](reports/ragpulse_input_demand_transfer_parser_addendum_v1_1.md)
- [RAGPulse 입력 수요 이전 시험의 지원량 부족·무채점 결과](reports/ragpulse_input_demand_transfer_support_no_go.md)
- [Google ClusterData2011 CPU·RAM 요구 형상 학습 가능성 진단](reports/google_2011_resource_feasibility.md)
- [Google ClusterData2011 기존 배정 아래 30분 CPU·RAM 관측 최대값 예측](reports/google_2011_observed_resource_findings.md)
- [Google ClusterData2011 관측 사용량 시험 전 규약](reports/google_2011_observed_resource_protocol.md)
- [Google ClusterData2019 ALLOC_SET 시작 지연 라벨 지원량 감사](reports/google_2019_alloc_set_start_delay_no_go.md)
- [Alibaba GPU v2026 자료의 자원 요구 분석 적합성](reports/alibaba_gpu_2026_feasibility.md)
- [Alibaba GPU v2026 시간순 AI GPU 계측의 학습 가능성](reports/alibaba_gpu_temporal_feasibility.md)
- [Alibaba GPU v2026 GPU SM 변화의 작업 분리 지원량 감사](reports/alibaba_gpu_sm_change_support_no_go.md)
- [Alibaba GPU v2026 배포 준비 지연 라벨 적합성](reports/alibaba_gpu_readiness_feasibility.md)
- [로컬 SQLite 새 물리 측정·실행 라벨 재점검](reports/local_signal_refresh_2026_09_28.md)
- [실제 배포·스케일 효과 검증을 위한 연구 데이터 계약 초안](reports/scale_effect_data_contract_v1.md)
- [배포·스케일 사건 연결용 JSONL 계약과 검증기](reports/scale_action_events_v0_1_0.md)
- [새 VM Scale-out 사건 연결용 JSONL 계약 0.2.0과 검증기](reports/scale_action_events_v0_2_0.md)
- [연구 예측값과 기존 Deployment Spec의 연결 경계](reports/research_to_deployment_spec_boundary.md)
- [공개 자료의 실제 스케일 조치·결과 연결 가능성 감사](reports/public_scale_action_dataset_audit.md)
- [추가 공개 AI 추론 자료의 조치·Ready·요청 결과 연결 엄격 검사](reports/public_action_ready_slo_strict_followup_2026_09_29.md)
- [공개 오토스케일 자료 3종의 조치·Ready·요청 원본 재점검](reports/public_action_ready_request_refresh_2026_09_29.md)
- [1GB 이하 공개 오토스케일 자료의 상태·사용자 결과 연결 감사](reports/public_subgigabyte_action_outcome_audit.md)
- [HAS-GPU 추론 요청·GPU 시계와 학습 범위 게이트](reports/has_gpu_training_gate.md)
- [HAS-GPU 요청별 30초 미래 지연 예측의 고정 프로토콜](reports/has_gpu_request_p95_30s_protocol.md)
- [HAS-GPU 요청별 30초 미래 지연 예측: Ridge 미채택 결과](reports/has_gpu_request_p95_30s_findings.md)
- [공개 자료를 더한 다음 연구 우선순위](reports/next_research_priority_2026_09_28.md)
- [현재 SQLite·공개 자료로 열 수 있는 경희대 담당 학습 과제 결정](reports/khu_available_data_decision_2026_09_29.md)
- [OpenStack Monasca 공개 원본 다운로드·명령 기록 확인 게이트](reports/monasca_archive_download_gate.md)
- [UCC2021 OpenStack 원본 접두부의 서버 시작·요청 지연 결합 감사](reports/ucc2021_action_outcome_join_feasibility.md)
- [Airflow 조치 로그 원본 DB의 수집 가능성·사전 선택 기록](reports/airflow_action_feasibility.md)
- [정적 배포 후보의 2단계 추론 비용 실험 규약](reports/cascade_inference_protocol.md)
- [정적 배포 후보의 2단계 추론 품질·시간 실패 결과](reports/cascade_inference_findings.md)
- [저장된 성능 예측기의 단일 worker 추론 적용·검증](reports/performance_predict_workers_findings.md)
- [저장된 성능 예측기의 8후보 일괄 채점 규약](reports/performance_batch_predict_protocol.md)
- [저장된 성능 예측기의 8후보 일괄 채점 적용·검증](reports/performance_batch_predict_findings.md)
- [저장된 모델로 동일 작업의 8개 GPU·CPU 후보를 평가하는 오프라인 CLI](reports/gpu_shape_assessment_v1.md)
- [저장된 GPU 형상 모델의 선택 결과와 목표 민감도 감사](reports/saved_gpu_shape_decision_audit_findings.md)
- [저장된 GPU 형상 모델의 연속 SLO 민감도면 후향 진단](reports/saved_gpu_shape_slo_surface_findings.md)
- [8후보 평가 결과의 버전형 연구 증거 JSON·스키마와 검증](reports/gpu_shape_evidence_v0.1.0_findings.md)
- [물리 GPU 인덱스·입력 조합 출처를 확인하는 연구 증거 v0.2.0](reports/gpu_shape_evidence_v0.2.0_findings.md)
- [8후보 연구 증거의 독립 수신 의미·원본 쌍 검증](reports/gpu_shape_evidence_receiver_validation.md)
- [연구 증거와 Go 배포 계약의 연결 가능성](reports/research_evidence_contract_feasibility.md)
- [실행 중 모니터링을 이용한 GPU 복제 성능 예측](reports/telemetry_conditioned_scaling_findings.md)
- [현재 GPU 2개 관측에서 GPU 1개 정적 축소 적격성 평가](reports/static_scale_in_findings.md)
- [모니터링 기반 복제 예측의 BERT 계열 공동 제외 검증](reports/telemetry_family_holdout_findings.md)
- [모니터링 값의 작업별 대응 순열 대조](reports/telemetry_permutation_control_findings.md)
- [기존 SQLite의 원격 새 모델 스트레스 테스트](reports/remote_sqlite_validation_findings.md)
- [고유 방법의 근거·한계와 다음 검증](reports/novelty_gap_and_next_experiment.md)
- [선행연구 대비 방법론 기여 범위 검토](reports/novelty_position_review.md)
- [기존 SQLite만 사용할 때의 연구 결정](reports/sqlite_only_research_decision.md)
- [경희대 방법 후보 v1: 소수 기준 측정과 모델 제외 잔차 전이](reports/anchor_residual_transfer_method_v1.md)
- [경희대 방법 후보: 현재 계측으로 GPU 복제 용량 예측](reports/replica_capacity_method_v1.md)
- [조건별 비대칭 SLO 여유값의 실패 실험](reports/asymmetric_margin_findings.md)
- [별도 lifecycle SQLite의 출처·라벨 감사](reports/untapped_signal_audit.md)
- [GPU 복제 벤치마크의 측정 의미](reports/replica_benchmark_semantics.md)
- [GPU 복제의 물리 병목 가설 검증](reports/replica_bottleneck_identifiability_findings.md)
- [우세 형상 한 번 측정의 배포 가능성 판단 한계](reports/pareto_one_probe_assessment.md)

## 재현

작업 디렉터리: `/home/researcher/ai-mcmp/research`. 사용한 패키지 버전은 `requirements.txt`와 실험 manifest에 기록한다. 학습은 CPU 2개 worker를 사용하며 GPU 작업을 실행하지 않는다.

```bash
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/audit_data.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python -m unittest discover -s tests -v
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/train_performance.py
python scripts/summarize_experiment.py
python scripts/predict_performance.py --input examples/performance_supported_input.json
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/evaluate_gpu_policy.py
python scripts/plot_gpu_policy.py
python scripts/verify_gpu_policy.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/sparse_anchor_transfer.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/cross_model_covariance.py
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 python scripts/evaluate_cross_metric_anchor_transfer.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/summarize_cross_model_covariance.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/cross_model_covariance_external.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/covariance_bias_ablation.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/covariance_permutation_control.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/family_holdout_transfer.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/family_holdout_transfer.py --include-distilbert
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/family_holdout_centered.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/family_holdout_centered.py --include-distilbert
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/live_repeat_transfer.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/covariance_bias_ablation_external.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/dual_axis_sparse_probe.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/relative_response_sentinel.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/joint_resource_residual.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/evaluate_counterfactual_slo.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/audit_slo_case_mix.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/evaluate_decision_consensus.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/evaluate_resource_regret.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/support_aware_transfer_gate.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/support_hybrid_transfer.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/donor_similarity_transfer.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/evaluate_probe_design.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/adaptive_probe_transfer.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/physical_response_transfer.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/audit_repeat_variability.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/telemetry_conditioned_scaling.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/evaluate_static_scale_in.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/evaluate_telemetry_scaling_decision.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/telemetry_family_holdout.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/telemetry_family_holdout.py --include-distilbert
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/telemetry_permutation_control.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/remote_sparse_stress_test.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/repeated_measurement_distribution.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/evaluate_vram_peak.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/evaluate_vram_upper.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/learned_repeat_distribution.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/evaluate_load_time.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/evaluate_load_support.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/evaluate_vram_conditional.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/cold_start_policy.py
python scripts/fetch_gentd26.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/evaluate_gentd26_request_latency.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/evaluate_gentd26_subgroup.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/evaluate_gentd26_walkforward.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/evaluate_gentd26_coverage_guard.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/evaluate_gentd26_selective_guard.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/evaluate_gentd26_monitoring.py
python scripts/fetch_gentd26_update.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/evaluate_gentd26_update_latency.py --stage select
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/evaluate_gentd26_update_latency.py --stage score
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/evaluate_gentd26_gpu_memory_peak.py --stage select --output artifacts/gentd26_gpu_memory_peak_repro
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/evaluate_gentd26_gpu_memory_peak.py --stage score --output artifacts/gentd26_gpu_memory_peak_repro
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 python scripts/evaluate_burstgpt_output_tokens.py validate --output artifacts/burstgpt_output_tokens_repro
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 python scripts/evaluate_burstgpt_output_tokens.py test --output artifacts/burstgpt_output_tokens_repro
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 python scripts/evaluate_burstgpt_12_output_tokens.py prepare --output artifacts/burstgpt_12_output_tokens_repro
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 python scripts/evaluate_burstgpt_12_output_tokens.py test --output artifacts/burstgpt_12_output_tokens_repro
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/evaluate_gentd26_queue_calibration.py
python scripts/fetch_maas_app_cases.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/evaluate_maas_trace.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 python scripts/evaluate_maas_robustness.py
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 /usr/bin/python scripts/evaluate_maas_state_followup.py --dry-run
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 /usr/bin/python scripts/evaluate_maas_state_followup.py --score
OPENBLAS_NUM_THREADS=2 OMP_NUM_THREADS=2 /usr/bin/python scripts/audit_maas_state_followup.py
```

`audit_data.py`를 다시 실행하면 원본의 현재 상태를 다시 추출한다. 기존 실험을 정확히 재현하려면 고정된 `data/audit_v1/measurements.parquet`를 그대로 사용한다. 새 추출·실험은 `--output`으로 별도 디렉터리에 저장한다. summary 스크립트는 기본 v1 경로용이다.

## 입력과 출력

현재 승인되는 입력 JSON 배열 예시는 [`examples/performance_supported_input.json`](examples/performance_supported_input.json)에 있다. `python scripts/predict_performance.py --input examples/performance_supported_input.json`으로 직접 확인할 수 있다. 초기 실험의 `artifacts/performance_v1/example_input.json`은 CPU 훈련 범위 밖이므로 현재 `ABSTAIN`하며, 과거 기록으로 남긴다. 학습에서 사용한 모델/revision·장치·호스트·replica 조합과 모델·장치별 수치 범위를 벗어나면 `ABSTAIN`한다. 범위 안에 있다는 사실만으로 오차나 미측정 조건 조합의 성공이 보장되지는 않는다.

- `latency_p95_ms`: 배치 p95 지연. 여러 GPU replica에서는 개별 p95 중 최댓값.
- `examples_per_second`: 처리 샘플/초. 여러 GPU replica에서는 개별 처리량의 합.

이 값은 온라인 요청 지연·생성 token/초·여러 VM의 end-to-end 성능을 의미하지 않는다. 불확실성 보정 전 점 예측이며, 배포·스케일링 action을 출력하지 않는다.

`performance_model.joblib`은 이 실험에서 생성한 로컬 모델이다. 모델 메타데이터, 데이터/코드/아티팩트 해시와 분할 내역을 함께 보관한다.

## 분할과 모델 선택

- 같은 model/revision·batch·길이·동시성·dataset의 모든 자원 후보를 한 그룹으로 묶는다.
- 모델별 그룹 70/15/15 분할, seed 42. 정수 반올림 때문에 정확한 행 비율과 다르다.
- 학습 후보: 모델/device별 중앙값, Random Forest, Extra Trees.
- validation의 두 타깃 평균 그룹 절대 로그오차로만 선택한다. test로 튜닝하지 않는다.
- 새 응용, 새 호스트, 미래 시점에 대한 일반화 검증은 별도 과제다.

`data/`, `artifacts/`는 로컬 연구 산출물이다. `fetch_gentd26.py`와 `fetch_maas_app_cases.py`는 각자 명시한 공개 원본만 내려받는다. 기존 SQLite와 로컬 추출 자료의 외부 서비스 전송은 수행하지 않는다.

## GPU 형상 선택 연구

원본의 575개 입력 조건마다 8개 GPU 형상을 모두 측정한 자료에서, 기존 train 그룹을 다시 나누어 모델 학습과 독립 보정을 수행한다. 처리량 목표는 요청/초이므로 측정 샘플/초를 batch 크기로 나눈다. 점 예측, 행 단위 보정, 후보 집합 전체 보정과 실측 oracle을 비교한다. 자세한 방법·문헌·결과·한계는 [연구 보고서](reports/gpu_policy_research.md)에 기록했다.

첫 평가에서는 보정 전 정책이 선택한 54개 조건 모두 관측 SLO를 만족했고, 90% 동시 보정 정책은 37개만 선택했다. 이 결과는 한 호스트·생성된 SLO·정적 후보에 대한 오프라인 실험이다. 보정 정책의 도입 효과는 확인되지 않았다.

## 새 모델의 한 번 기준 측정 연구

9개 모델을 하나씩 통째로 제외하고, 새 모델의 GPU 1개·CPU affinity 2개 실행 결과 하나만 입력으로 써서 다른 7개 형상의 지연·요청 처리량을 예측했다. 기준 측정값을 상대 반응 모델의 입력에 넣으면 동일한 정적 특징과 사후 보정을 쓰는 기준선보다 평균 절대 로그오차가 줄었다(지연 0.276→0.219, 처리량 0.114→0.090). 강한 RandomForest 모델도 사실상 같은 수준이었고, 100회 추가 측정에서의 SLO 충족 조건 회수는 둘 다 98개였다. 독보적인 방법이나 운영 수준 추천 성능은 아직 입증되지 않았다. 평가 설계, 선행연구, 실패한 가설과 재현 방법은 [연구 결과](reports/anchor_conditioned_research_findings.md)에 적었다.

## 모델당 소수 측정과 이전 모델 오차 전이

후속 실험은 새 모델 전체에서 GPU 1개·CPU affinity 2코어를 4·8·16개 입력 조건에서만 측정했다. 과거 모델의 모델 단위 제외 예측 오차에서 조건 간 공분산을 학습해, 새 모델에서 측정하지 않은 조건의 성능을 추정한다. 모델당 8회 측정·같은 공간 선택 프로브에서 공유 입력 격자의 encoder 8모델 예측은 부드러운 커널과 직접 ridge 보정보다 나았다. 다만 [계열 공동 제외](reports/family_holdout_transfer_findings.md)에서는 지연 이득이 사라지거나 역전됐고, [새 입력 조건의 반복 측정](reports/live_repeat_transfer_findings.md)에서도 공분산 고유의 이득이 거의 없었다. 이 방법은 [탐색 결과](reports/cross_model_covariance_findings.md)이며, 선행연구 대비 최초성은 주장하지 않는다. 데이터의 적합 범위와 다음 독립 검증은 [연구 계획](reports/novelty_gap_and_next_experiment.md)에 정리했다.

GPU 1개 벤치마크의 성능과 GPU·CPU 모니터링을 입력으로 GPU 2개에서의 처리량을 예측하면, 모델 단위 제외 평가에서 기준선보다 오차가 작았다. [계열 공동 제외](reports/telemetry_family_holdout_findings.md)에서는 주 조건의 이득이 거의 없거나 새 조건에서 역전됐다. [정적 Scale-out 판단 평가](reports/telemetry_conditioned_scaling_findings.md)에서도 성공과 잘못된 승인 사이에 상충이 있어 action 개선으로 채택하지 않았다. 실제 scale 전환 로그가 없으므로 이 결과는 용량 예측 연구에 한정된다.

역방향인 [GPU 2개 관측 후 GPU 1개 정적 형상 선택](reports/static_scale_in_findings.md)은 모델 전체 제외 575작업에서 단순 비율 규칙의 안전 승인/오승인 `232/14`보다 정적 특징 학습 `240/8`이 나았다. 그러나 Qwen 한 모델의 승인 오차율은 13.2%였고 새 입력 조건에서는 단순 규칙보다 잘못 승인한 작업이 생겼다. GPU 1개 측정이 전부 GPU 2개보다 **먼저** 수집돼 실제 Scale-in의 시간순 재연이나 효과는 아니다.

GPU 2개 실행은 두 replica가 **각각 동일한 전체 작업**을 측정하고 그 요청 처리율을 합친 것이다. 실제 요청을 나누어 처리한 완료 시간이나 VM Scale-out 결과로 해석하지 않는다. [원본 수집기와 2,300쌍 검증](reports/replica_benchmark_semantics.md)

기존 CPU 측정을 GPU 추천의 사전 신호로 쓰는 가설도 시험했다. CPU 2코어·8개 입력 조건만 쓰면 모델 제외 평가의 지연/처리량 로그오차가 `0.399/0.386`으로 GPU 4회 기준 `0.231/0.158`보다 높았다. CPU 결과를 GPU 4회에 더해도 성과는 상충했고, CPU 측정은 모델당 평균 529 wall초가 필요했다. [비교·비용·시간순서](reports/cpu_preprofile_transfer_findings.md)와 [직접 선행연구 검토](reports/cpu_proxy_novelty_review.md)를 기록했다. GPU 1개 실행 계측으로 CPU affinity 변경을 예측하는 [별도 실험](reports/vertical_telemetry_probe_findings.md)도 새 입력 조건의 오차와 가상 형상 선택을 개선하지 못했다. [메모리 적합성 감사](reports/memory_feasibility_review.md)에서는 OOM 정답이 없어 RAM/VRAM 추천 모델을 검증할 수 없음을 확인했다. 기존 SQLite만 사용할 때의 항목별 결론은 [연구 결정](reports/sqlite_only_research_decision.md)에 모았다.

별도 lifecycle SQLite의 행동 결과도 [출처와 라벨을 감사](reports/untapped_signal_audit.md)했다. `SCALE_OUT`/`SCALE_IN`은 정적 복제 측정에서 파생됐고, 후보 특징에 실행 후 GPU 사용률이 포함되며, 원본 샘플/초가 요청/초로 잘못 쓰인 SLO 판정이 있다. 이 DB의 기존 점수를 운영 제어 성능으로 인용하지 않는다.

## 다른 경희대 담당 항목에서 수행한 SQLite 연구

**배포·제어 추론 최적화:** 같은 GPU 형상의 처음 2·3·5·10회 측정으로 미래 한 번 실행의 배치 지연·요청 처리량 분포를 예측했다. 모델을 통째로 제외하고 실제 시각순으로 평가했다. 2회에서는 다른 모델의 변동 폭을 섞은 방법의 지연 CRPS가 대상 모델 단독 정규·Cauchy 기준선보다 낮았지만, 5회에서는 대상 모델 단독 기준선이 네 버전×지표 모두에서 CRPS가 더 낮았다. 반복 측정 예산과 불확실성의 [탐색 결과](reports/repeated_measurement_distribution_findings.md)이며 실제 배포·Scale-out 성과로 해석하지 않는다.

**응용 요구 분석·인프라 형상 추천의 입력:** 등록 시 모델 파라미터 바이트 수를 확보한다고 가정하면, 같은 GPU에서 관측한 PyTorch 예약 메모리 피크를 작업 조건과 함께 회귀할 수 있다. [모델 제외 실험](reports/vram_peak_findings.md)에서는 개선된 경우가 있었지만 계열 공동 제외와 새 작업 조건에서 약했고 과소예측도 잦았다. 관측 피크는 최소 VRAM이나 OOM 정답이 아니므로 이 모델을 GPU 적합성 필터로 사용하지 않는다.

후속 학습에서는 관측 피크의 [90% 상한 예측](reports/vram_upper_findings.md), 반복 실행의 [donor 기반 예측폭 학습](reports/learned_repeat_distribution_findings.md), [로컬 모델 적재시간 예측](reports/load_time_findings.md)을 모델 제외 방식으로 평가하고 연구용 학습 모델을 저장했다. 상한은 유일한 생성형 모델에서 실패했고, 학습한 반복 예측폭은 강한 기준선을 일관되게 넘지 못했으며, 적재시간은 새 모델 종류에서 크게 틀렸다. 핵심 수치·산출물·적용 범위는 [결과 요약](reports/trained_algorithms_v1.md)에 모았다.

## 공개 추적 자료를 이용한 요청·부하 연구

GenTD26 요청에서는 실행시간 p90 모델을 학습하고 과거 피드백으로 보정했다. 모델 ID별 보정은 [5개 시간순 시험 구간](reports/gentd26_walkforward_findings.md)에서 평균 손실을 줄였으나 전체 포괄률이 90%에 못 미쳤다. [검증 커버리지 제약](reports/gentd26_coverage_guard_findings.md)은 합산 포괄률을 높였지만 한 시험 구간은 86.5%에 그쳤다. 독립 LLM 로그 [BurstGPT 품질 감사](reports/burstgpt_data_audit.md)에서는 완료시간 0초가 59.4%로 나타나 전체 요청 검증에 쓸 수 없었다. 양수 완료시간이 있는 [부분집합 탐색](reports/burstgpt_observed_duration_findings.md)은 별도 참고 결과로만 기록했다. [SwissAI 전량 감사](reports/swissai_trace_full_audit.md)에서는 원래 독립 검증 규약의 품질 게이트가 실패했다. 1,385만 양수 라벨 요청에 대한 [별도 탐색](reports/swissai_exploratory_findings.md)에서는 모델별 잔차 보정이 전역 보정보다 악화했고(손실 4.743초 대 4.490초), 사전 고정한 직접 최근 완료시간 p90 기준선(4.152초)에도 졌다. 여유값을 적용해도 24개 시험 블록 중 8개에서 90% 포괄률에 미달했다. [선행연구 검토](reports/online_tail_prior_art.md)는 최근 잔차·그룹별 보정 자체를 새 알고리즘으로 주장할 수 없음을 보여 준다.

[같은 GenTD26 요청의 최종 `FAILED` 상태 위험](reports/gentd26_request_failure_validation_findings.md)은 별도 라벨로 학습했다. 검증 7,263건·실패 92건에서 날짜 동일 가중 Brier가 사전 선택 기준선 `0.006053→0.005100`으로 낮아 탐색 게이트를 통과했다. 그러나 학습에서 전량 실패했던 모델 세 개가 검증 실패의 **54/92건**을 차지했고, 이를 제외한 사후 분석에서 후보가 기준선보다 나빴다. 사후 추가한 1 pseudo-count 모델별 빈도 기준선은 [같은 서비스의 후행 5일 민감도 평가](reports/gentd26_request_failure_temporal_findings.md)에서도 저장된 로지스틱보다 날짜 동일 Brier가 **0.007122→0.006255**로 낮았고, 세 모델을 제외한 6,300건에서도 **0.006983→0.006270**으로 낮았다. 반면 AP는 로지스틱이 더 높았다(`0.3947` 대 `0.3706`). 이 단순 기준선은 검증 결과를 본 뒤 정했고 후행 시험 분포도 일부 열람됐으므로 블라인드·독립 일반화 증거가 아니다. 최종 상태 확정 시각과 실패 원인도 없어 **판단기에는 채택하지 않는다**.

[GenTD26 파이프라인 적재 지연](reports/gentd26_update_latency_findings.md)은 36,046개 기록 중 마지막 2,924개에서 상태 추가 p90 회귀의 pinball 손실이 검증 선택 경험적 기준선보다 7.56% 낮았다. 이력만 쓴 회귀보다 나은 정도는 1.05%로 사전 2% 기준에 못 미쳤다. 업데이트 명령 시각이 없어서 특징 시점은 미래 기록 시각을 조건으로 정의했으며, 운영용 업데이트 판단기로 채택하지 않는다.

GenTD26 모니터링의 [대기열 위험](reports/gentd26_monitoring_findings.md)과 MaaS 사례의 [상대 QPS 예측](reports/maas_trace_findings.md)은 Scale-out/in 판단에 쓸 수 있는 입력 후보이며, 실제 실행 효과의 정답은 없다. [SwissAI 미래 10분 요청량 탐색](reports/swissai_demand_findings.md)에서는 완료 결과를 더한 학습기의 p90 손실이 도착량 전용 학습기보다 1.22% 높았고, 검증으로 고른 단순 지속 기준선보다도 높았다. 이 실패를 보고 고정한 [상대 수요 후속 규약](reports/swissai_scale_adaptive_protocol.md)은 새 기간에서 한 모델의 적격 판단이 134개·양수 정답이 18개뿐이라 [표본 게이트에서 중단](reports/swissai_scale_adaptive_gate_failure.md)됐다. 후속 모델의 점수는 계산하지 않았다. [Alibaba MaaS 인스턴스 GPU 위험 실험](reports/maas_instance_risk_findings.md)에서는 로지스틱 모델의 시험 Brier 0.059147이 검증 선택 단순 기준선 0.006128보다 높았다. 이를 보고 고정한 [상태별·결측 인식 후속 탐색](reports/maas_state_followup_findings.md)은 완전 창·현재 정상층에서 기존 단순 기준선보다 Brier를 6.28% 낮췄지만, 같은 확대 학습 자료의 새 단일 모델이 상태별 모델보다 조금 좋았다. 부분결측층은 ID 구성과 미래 정답 관측률이 크게 달라 해석에 주의가 필요하다. 이 실험들에는 실제 VM 증감 효과의 정답이 없다. SwissAI 요청량 실험의 전체 추적 계산은 `numba==0.60.0`을 설치한 `/usr/bin/python`으로 실행하며, 명령은 결과 보고서에 있다. 담당 항목별 결론과 다음 계측 항목은 [종합 보고서](reports/khu_research_progress_external_data.md)에 정리했다.

[BurstGPT 대화 로그의 1시간 입력 토큰량 검증](reports/burstgpt_input_demand_validation_no_go.md)은 ChatGPT·GPT-4 각각 480시간에서 학습 분위수 HGB의 동일 집단 가중 pinball 손실을 선택 기준선 `6,901.853→6,180.628`로 낮췄다. 그러나 두 집단 피복률이 **84.17%·81.46%**로 사전 85% 기준에 미달해 미채택했고, 후행 50일의 시간별 시험 점수는 계산하지 않았다. 이 값은 기록된 요청 수요이며 실제 GPU·VM 형상 또는 스케일 행동의 정답이 아니다.

[같은 로그의 1시간 출력 토큰 합계 후향 탐색](reports/burstgpt_output_demand_validation_no_go.md)은 입력 합계 및 요청별 출력 길이와 다른 라벨을 사용했다. 검증의 집단 동일 가중 pinball은 전날 같은 시간 기준선 **1,485.291→학습기 1,284.764**로 낮았으나, ChatGPT·GPT-4 피복률 **82.08%·82.50%**가 사전 85% 문턱에 못 미쳤다. 학습기는 미채택했고 후행 50일의 시간별 출력 합계 시험 정답은 열지 않았다. 앞선 연구에서 같은 원본의 출력 분포를 이미 확인했으므로 독립 검증이 아니다.

실패를 보고 고정한 [학습 기간 블록 보정 후속](reports/burstgpt_input_demand_v2_validation_no_go.md)은 두 집단의 30일 적합·10일 보정 뒤 같은 검증 480시간씩에서 손실을 **6,152.008**로 낮췄다. ChatGPT 피복률은 87.08%였지만 GPT-4는 **82.92%**로 사전 85% 기준에 다시 못 미쳤다. 보정량을 재조정하지 않고 시험 채점을 중단했다. 같은 검증을 본 뒤 설계한 탐색 실험이다.

별도 [SwissAI 요청 오류 상태 위험 실험](reports/swissai_error_risk_findings.md)에서는 완료 후 1시간이 지난 과거 오류 결과와 모델별 이력을 사용했다. 고정 시험 736,928건의 HGB Brier는 검증 선택 최근 전역 오류율의 `0.228107`보다 낮은 `0.137778`이었지만, 다섯 7일 블록 중 사전 기준인 4개 승리를 채우지 못했고(3개), 평균 위험을 실제 오류율보다 9.58%p 낮게 예측했다. 지원범위 보류를 추가하면 Brier가 `0.146081`로 악화돼 두 방법 모두 판단기에 채택하지 않는다. 이 기록 상태의 원인이나 스케일 명령 효과는 관측되지 않는다.

[독립 Philly GPU 추적의 사전 동결 시험](reports/philly_gpu_risk_findings.md)은 완전 관측·현재 80% 미만 217,750건에서 30분 고사용률 위험의 Brier를 검증 선택 단순 기준선 `0.067942`에서 학습 모델 `0.054683`으로 낮췄다. 두 고정 시험 반구간에서도 개선됐다. 그러나 같은 특징을 쓰는 단일 모델이 조금 더 정확했고 부분결측 시험 후보가 없어 **상태별 분리·관측 품질 입력의 고유 이득은 입증되지 않았다**. 이는 스케일 명령의 효과나 사용자 SLO 개선 결과가 아니라 판단기에 넣을 수 있는 위험 신호의 제한된 평가다.

기존 21기계의 결과를 본 뒤 고른 단일 모델을 [겹치지 않는 Philly 기계에서 후속 확인](reports/philly_machine_holdout_findings.md)했다. 신규 37기계의 완전 관측·현재 정상 시험 741,597건에서 Brier는 고정 단순 기준선 `0.089879`보다 학습 모델 `0.077783`이 13.46% 낮았다. 두 고정 반구간과 15개 날짜 모두에서 개선됐으나 37기계 중 5기계는 악화됐다. [점수 전 품질 감사](reports/philly_machine_holdout_dry_run_gate.md)와 기계 단위 재표집도 사전 기준을 통과했다. 동일 클러스터·동일 시기의 다른 식별 기계에 대한 GPU 위험 신호 결과이며 실제 스케일 조치의 효과는 알 수 없다.

그 기계별 악화를 줄이기 위해 [사전기간 결과로 확률을 교정하는 후속 연구](reports/philly_machine_calibration_findings.md)를 새 해시 구간의 기계에 시험했다. 개발 기계에서는 기계 동일 가중 Brier가 소폭 좋아졌지만 행 Brier와 AP가 악화했다. 동결 뒤 수집한 별도 33기계·599,752건에서는 행 Brier가 원래 모델 `0.109416`에서 제안법 `0.109163`으로 0.23% 낮아지는 데 그쳤다. 기계 재표집 신뢰구간에 0이 포함되고 후반 달력 구간의 손실은 높아져 사전 성공 기준에 실패했다. 이 기계별 교정법의 운영 적용은 보류한다.

그 뒤 이미 열람한 두 집합에서 개발한 **전날 기계별 보정**을 [미사용 기계 시험 전에 동결](reports/philly_day_ahead_calibration_holdout_protocol_frozen.md)하고 [별도 `[25,35)` 해시 구간에서 평가](reports/philly_day_ahead_calibration_holdout_findings.md)했다. 41기계·685,940건의 Brier는 기존 모델 `0.086988`, 전역 전날 보정 `0.086329`, 기계별 전날 보정 **`0.082766`**이었다. AP도 기존 `0.808794→0.820813`으로 높아졌고 35/41기계에서 Brier가 낮아져 사전 탐색 게이트 일곱 개를 통과했다. [저장 예측의 독립 검산](artifacts/philly_day_ahead_calibration_holdout_v1/independent_reproduction.json)은 전날 완료 결과만 사용한 예측과 점수를 재현했다. 이는 같은 Philly 클러스터의 새 기계에서 확인한 GPU 고부하 **위험 입력**이며, 실제 앱 배포·스케일 조치의 효과나 다른 서비스로의 전이는 아직 확인하지 않았다.

[사후 피드백 지연 민감도](reports/philly_feedback_delay_sensitivity.md)에서는 이미 채점한 동일 41기계에 5분·1시간·6시간·12시간 추가 정답 전달 지연을 가정했다. 6시간 Brier `0.083200`, 12시간 `0.083653`으로 원래 모델 `0.086988`보다 낮았다. 실제 데이터 전달 지연이 기록되지 않았으므로 새 검증이나 운영 지연 보장의 근거는 아니다.

[같은 자료의 상위 5% 경보량 진단](reports/philly_alert_budget_posthoc.md)에서는 고사용률 판단을 맞힌 경보가 기존 `32,683`건에서 기계별 전날 보정 `32,752`건으로 69건 늘었으나, 날짜별 개선 8일·악화 4일·동일 3일이며 날짜 재표집 구간에 0이 포함됐다. Brier 개선을 안정적인 경보 우선순위 이득으로 확대하지 않는다.

[이미 공개된 세 기계 집합의 강한 기준선 비교](reports/philly_day_ahead_strong_baselines_posthoc.md)에서는 같은 벌점·전날 정답으로 로지스틱 절편을 정확히 풀면 현재의 한 번 Newton 근사보다 Brier가 모두 소폭 낮았다. 최신 집합에서는 `0.082766→0.082749`로 차이가 작지만, 근사식 자체의 고유 정확도 우위는 주장하지 않는다.

별도 [SwissAI 유휴 모델 재도착 위험 학습](reports/swissai_idle_return_findings.md)은 5분 유휴 뒤 10분 내 요청 재도착을 예측했다. 고정 시험 845건에서 로지스틱 Brier `0.214223`이 모델별 과거 재도착률 기준선 `0.222405`보다 3.68% 낮았지만, 사전 채택 기준 5%에 못 미쳤고 적격 모델별 균등 평균은 기준선보다 나빴다. 따라서 학습기를 현재 판단 메커니즘에 추가하지 않는다. 322건은 이후 10분간 서비스 전체 요청도 없어 기록 공백과 실제 무요청을 구분할 수 없다.

[후행 기간의 고정 로컬 수축 혼합 평가](reports/swissai_idle_return_post_findings.md)에서는 1,266건/양성 443건의 Brier가 혼합 `0.223085`, 경험적 기준선 `0.225165`, 고정 로지스틱 `0.226553`이었다. 상대 개선이 각각 0.92%·1.53%로 사전 5% 기준에 미달했고, 지원 충분 5개 블록 중 둘 다 이긴 블록은 0개라 혼합기를 채택하지 않는다. 464건은 정답 창에 서비스 전체 요청도 없어 기록 공백 여부가 불확실하다. 이 결과는 재요청 신호이며 실제 VM 축소 효과가 아니다.
