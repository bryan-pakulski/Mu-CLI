import van from "../../../vendor/van-1.6.0.min.js";
import { MessageItem } from "./message_item.js";

const { div, h2, section, textarea } = van.tags;

function renderMessages(store) {
  return store.history.val.map((message) => MessageItem(message));
}

export function ChatPanel(store) {
  const feedRoot = div({ class: "van-feed" });
  van.derive(() => {
    feedRoot.replaceChildren(...renderMessages(store));
  });

  return section({ class: "van-panel van-chat" },
    h2("Chat (Read-Only Preview)"),
    div({ class: "van-subtle" }, () => `Messages: ${store.history.val.length}`),
    feedRoot,
    div({ class: "van-empty-note" }, () => (store.history.val.length ? "" : "No messages in selected session yet.")),
    textarea({ disabled: true, rows: 4, placeholder: "Composer migration starts in Phase 3." }),
  );
}
