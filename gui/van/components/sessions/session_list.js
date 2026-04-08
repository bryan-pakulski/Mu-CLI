import van from "../../../vendor/van-1.6.0.min.js";

const { button, h2, li, section, ul } = van.tags;

export function SessionList(store, api) {
  return section({ class: "van-panel" },
    h2("Sessions"),
    ul(() => store.sessions.val.map((name) => li(
      button({
        class: () => (name === store.currentSession.val ? "van-active" : ""),
        onclick: async () => {
          store.currentSession.val = name;
          const history = await api.history(name);
          store.history.val = history?.history || [];
        },
      }, name),
    ))),
  );
}
