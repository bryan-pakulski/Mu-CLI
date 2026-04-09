import van from "../../../vendor/van-1.6.0.min.js";

const { button, div, h2, li, section, ul } = van.tags;

function renderSessionItems(store, api) {
  return store.sessions.val.map((name) =>
      li(
        { class: "van-session-item" },
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

export function SessionList(store, api) {
  const listRoot = ul();
  van.derive(() => {
    listRoot.replaceChildren(
      ...renderSessionItems(store, api),
    );
  });

  return section({ class: "van-panel" },
    h2("Sessions"),
    div({ class: "van-subtle" }, () => `Count: ${store.sessions.val.length}`),
    listRoot,
    div({ class: "van-empty-note" }, () => (store.sessions.val.length ? "" : "No sessions found from /api/sessions.")),
  );
}
