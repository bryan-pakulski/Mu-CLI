import van from "../../../vendor/van-1.6.0.min.js";

const { div, h2, option, section, select } = van.tags;

const STATUS_ORDER = ["pending", "in_progress", "blocked", "completed", "archived"];

function normalizeStatus(value) {
  return String(value || "").trim().toLowerCase() || "pending";
}

export function BoardPreview(store, onSelectFeature) {
  const renderFeatureOptions = () => {
    const fragment = document.createDocumentFragment();
    for (const feature of store.features.val) {
      fragment.appendChild(
        option(
          {
            value: feature.feature_id || "",
            selected: String(store.selectedFeatureId.val || "") === String(feature.feature_id || ""),
          },
          feature.feature_name || feature.feature_id || "feature",
        ),
      );
    }
    return fragment;
  };

  const renderFeatures = () => store.features.val.slice(0, 10).map((feature) =>
    div({ class: "van-feature-item" },
      div({ class: "van-feature-title" }, feature.feature_name || feature.feature_id || "feature"),
      div({ class: "van-feature-meta" }, `${feature.status || "unknown"} · ${feature.directory || ""}`),
    ));

  const renderPlanLanes = () => {
    const plan = store.featurePlan.val || {};
    const phases = Array.isArray(plan.phases) ? plan.phases : [];
    const grouped = new Map(STATUS_ORDER.map((status) => [status, []]));
    for (const phase of phases) {
      const status = normalizeStatus(phase.status);
      if (!grouped.has(status)) grouped.set(status, []);
      grouped.get(status).push(phase);
    }
    return STATUS_ORDER.map((status) => {
      const items = grouped.get(status) || [];
      return div({ class: "van-lane" },
        div({ class: "van-lane-head" }, `${status.replaceAll("_", " ")} (${items.length})`),
        div({ class: "van-lane-body" },
          ...items.slice(0, 4).map((item) => div({ class: "van-lane-item" }, item.title || item.id || "task")),
          items.length > 4 ? div({ class: "van-lane-item more" }, `+${items.length - 4} more`) : "",
        ),
      );
    });
  };

  const featureListRoot = div({ class: "van-feature-list" });
  van.derive(() => {
    featureListRoot.replaceChildren(...renderFeatures());
  });
  const laneRoot = div({ class: "van-board-lanes" });
  van.derive(() => {
    laneRoot.replaceChildren(...renderPlanLanes());
  });

  return section({ class: "van-panel van-board" },
    h2("Feature Snapshot"),
    div({ class: "van-subtle" }, () => `Features: ${store.features.val.length}`),
    select({
      value: store.selectedFeatureId,
      oninput: (event) => onSelectFeature?.(event.target.value),
    },
      option({ value: "" }, "Select feature"),
      () => renderFeatureOptions(),
    ),
    featureListRoot,
    div({ class: "van-empty-note" }, () => (store.features.val.length ? "" : "No features returned from /api/features.")),
    laneRoot,
  );
}
