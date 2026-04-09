import van from "../../../vendor/van-1.6.0.min.js";
import { MessageItem } from "./message_item.js";

const { button, div, h2, section, textarea } = van.tags;

function renderMessages(store) {
  return store.history.val.map((message) => MessageItem(message));
}

export function ChatPanel(store, actions) {
  const feedRoot = div({ class: "van-feed" });
  van.derive(() => {
    feedRoot.replaceChildren(...renderMessages(store));
  });

  const submit = (event) => {
    event?.preventDefault?.();
    actions?.sendMessage?.();
  };

  return section({ class: "van-panel van-chat" },
    h2("Chat"),
    div({ class: "van-subtle" }, () => `Messages: ${store.history.val.length}`),
    div({ class: "van-subtle" }, () => `Task: ${store.taskStatus?.val || "idle"}${store.activeTaskId?.val ? ` (${store.activeTaskId.val.slice(0, 8)})` : ""}`),
    feedRoot,
    div({ class: "van-empty-note" }, () => (store.history.val.length ? "" : "No messages in selected session yet.")),
    textarea({
      rows: 4,
      placeholder: "Send a message…",
      value: store.draftMessage,
      oninput: (event) => {
        store.draftMessage.val = event.target.value;
      },
    }),
    div({ class: "van-chat-actions" },
      button({ onclick: submit, disabled: () => !store.draftMessage?.val?.trim() || store.sending?.val }, () => (store.sending?.val ? "Sending…" : "Send")),
      button({ onclick: () => actions?.cancelActiveTask?.(), disabled: () => !store.activeTaskId?.val }, "Cancel task"),
    ),
  );
}
