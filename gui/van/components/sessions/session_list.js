import van from "../../../vendor/van-1.6.0.min.js";

const { button, h2, li, section, ul } = van.tags;

function renderSessionItems(store, api) {
  const fragment = document.createDocumentFragment();
  for (const name of store.sessions.val) {
    fragment.appendChild(
      li(
        button(
          {
            class: () => (name === store.currentSession.val ? "van-active" : ""),
            onclick: async () => {
              store.currentSession.val = name;
              const history = await api.history(name);
              store.history.val = history?.history || [];
            },
          },
          name,
        ),
      ),
    );
  }
  return fragment;
}

export function SessionList(store, api) {
  return section({ class: "van-panel" },
    h2("Sessions"),
    div({ class: "van-subtle" }, () => `Count: ${store.sessions.val.length}`),
    ul(() => renderSessionItems(store, api)),
    div({ class: "van-empty-note" }, () => (store.sessions.val.length ? "" : "No sessions found from /api/sessions.")),
  );
}
