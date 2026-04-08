import van from "../../../vendor/van-1.6.0.min.js";

const { div, h2, h3, section } = van.tags;

export function ActivityPanel(store) {
  const renderTasks = () => {
    const fragment = document.createDocumentFragment();
    for (const task of store.tasks.val.slice(0, 8)) {
      fragment.appendChild(
        div({ class: "van-activity-item" },
          div({ class: "van-activity-title" }, `${task.kind || "task"} · ${task.status || "unknown"}`),
          div({ class: "van-activity-meta" }, String(task.task_id || "").slice(0, 16)),
        ),
      );
    }
    return fragment;
  };

  const renderApprovals = () => {
    const fragment = document.createDocumentFragment();
    for (const approval of store.approvals.val.slice(0, 5)) {
      fragment.appendChild(
        div({ class: "van-activity-item" },
          div({ class: "van-activity-title" }, approval.tool_name || "approval"),
          div({ class: "van-activity-meta" }, `task: ${String(approval.task_id || "n/a").slice(0, 16)}`),
        ),
      );
    }
    return fragment;
  };

  return section({ class: "van-panel van-activity" },
    h2("Activity Snapshot"),
    div({ class: "van-activity-status" }, () => `Approvals: ${store.approvals.val.length} · Tasks: ${store.tasks.val.length}`),
    div({ class: "van-subtle" }, () => {
      const model = store.runtime.val?.model || "unknown";
      return `Runtime model: ${model}`;
    }),
    h3("Recent tasks"),
    div({ class: "van-activity-list" }, () => renderTasks()),
    div({ class: "van-empty-note" }, () => (store.tasks.val.length ? "" : "No tasks returned from /api/tasks.")),
    h3("Pending approvals"),
    div({ class: "van-activity-list" }, () => renderApprovals()),
    div({ class: "van-empty-note" }, () => (store.approvals.val.length ? "" : "No pending approvals.")),
  );
}
