import van from "../../../vendor/van-1.6.0.min.js";

const { div, h2, section } = van.tags;

export function BoardPreview(store) {
  const renderFeatures = () => {
    const fragment = document.createDocumentFragment();
    for (const feature of store.features.val.slice(0, 10)) {
      fragment.appendChild(
        div({ class: "van-feature-item" },
          div({ class: "van-feature-title" }, feature.feature_name || feature.feature_id || "feature"),
          div({ class: "van-feature-meta" }, `${feature.status || "unknown"} · ${feature.directory || ""}`),
        ),
      );
    }
    return fragment;
  };

  return section({ class: "van-panel van-board" },
    h2("Feature Snapshot"),
    div({ class: "van-subtle" }, () => `Features: ${store.features.val.length}`),
    div({ class: "van-feature-list" }, () => renderFeatures()),
    div({ class: "van-empty-note" }, () => (store.features.val.length ? "" : "No features returned from /api/features.")),
  );
}
