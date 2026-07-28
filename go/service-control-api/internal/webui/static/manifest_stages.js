"use strict";

(function registerManifestStages(root) {
  const MANIFEST_STAGE_ORDER = Object.freeze([
    { key: "app_upload", label: "앱 업로드" },
    { key: "package_build", label: "패키지 생성" },
    { key: "app_registration", label: "앱 등록" },
    { key: "user_request", label: "사용자 요청" },
    { key: "request_guard", label: "Request Guard" },
    { key: "agent_registry", label: "Agent Registry" },
    { key: "agent_dispatch", label: "Agent 실행" },
    { key: "qwen_planner", label: "Qwen Planner" },
    { key: "manifest_guard", label: "Manifest Guard" },
  ]);
  const BLOCKED_STAGE_REASON = "이전 단계에서 중단";
  const PENDING_STAGE_REASON = "대기 중";
  const SKIPPED_STAGE_REASON = "기존 앱 사용";
  const APPLICATION_STAGE_KEYS = new Set(["app_upload", "package_build", "app_registration"]);

  function buildManifestStageViewModel(run) {
    const stages = new Map((run?.stages || []).map((stage) => [stage.name, stage]));
    const blocked = (run?.stages || []).some((stage) => stage.status === "rejected");
    const existingAppRun = Boolean(
      run?.run_id &&
      run?.request?.app_version_id &&
      !run?.application?.package &&
      !run?.application?.registration,
    );

    return MANIFEST_STAGE_ORDER.map((definition, index) => {
      const stage = stages.get(definition.key);
      const skipped = !stage && existingAppRun && APPLICATION_STAGE_KEYS.has(definition.key);
      return {
        index: index + 1,
        key: definition.key,
        label: definition.label,
        status: stage?.status || (skipped ? "skipped" : (blocked ? "blocked" : "pending")),
        reason: stage?.reason || (skipped ? SKIPPED_STAGE_REASON : (blocked ? BLOCKED_STAGE_REASON : PENDING_STAGE_REASON)),
      };
    });
  }

  const manifestStages = Object.freeze({
    MANIFEST_STAGE_ORDER,
    BLOCKED_STAGE_REASON,
    PENDING_STAGE_REASON,
    SKIPPED_STAGE_REASON,
    buildManifestStageViewModel,
  });

  if (typeof module !== "undefined" && module.exports) module.exports = manifestStages;
  if (root) root.ManifestStages = manifestStages;
})(typeof window === "undefined" ? globalThis : window);
