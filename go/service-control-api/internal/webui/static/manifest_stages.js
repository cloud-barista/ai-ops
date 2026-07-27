"use strict";

(function registerManifestStages(root) {
  const MANIFEST_STAGE_ORDER = Object.freeze([
    { key: "user_request", label: "?ъ슜???붿껌" },
    { key: "request_guard", label: "Request Guard" },
    { key: "agent_registry", label: "Agent Registry" },
    { key: "agent_dispatch", label: "Agent ?ㅽ뻾" },
    { key: "qwen_planner", label: "Qwen Planner" },
    { key: "manifest_guard", label: "Manifest Guard" },
  ]);
  const BLOCKED_STAGE_REASON = "?댁쟾 ?④퀎?먯꽌 以묐떒";
  const PENDING_STAGE_REASON = "?湲?以?";

  function buildManifestStageViewModel(run) {
    const stages = new Map((run?.stages || []).map((stage) => [stage.name, stage]));
    const blocked = (run?.stages || []).some((stage) => stage.status === "rejected");

    return MANIFEST_STAGE_ORDER.map((definition, index) => {
      const stage = stages.get(definition.key);
      return {
        index: index + 1,
        key: definition.key,
        label: definition.label,
        status: stage?.status || (blocked ? "blocked" : "pending"),
        reason: stage?.reason || (blocked ? BLOCKED_STAGE_REASON : PENDING_STAGE_REASON),
      };
    });
  }

  const manifestStages = Object.freeze({
    MANIFEST_STAGE_ORDER,
    BLOCKED_STAGE_REASON,
    PENDING_STAGE_REASON,
    buildManifestStageViewModel,
  });

  if (typeof module !== "undefined" && module.exports) module.exports = manifestStages;
  if (root) root.ManifestStages = manifestStages;
})(typeof window === "undefined" ? globalThis : window);
