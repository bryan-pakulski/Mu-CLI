import van from "../../../vendor/van-1.6.0.min.js";
import { MessageItem } from "./message_item.js";

const { div, h2, section, textarea } = van.tags;

export function ChatPanel(store) {
  return section({ class: "van-panel van-chat" },
    h2("Chat (Read-Only Preview)"),
    div({ class: "van-feed" }, () => store.history.val.map(MessageItem)),
    textarea({ disabled: true, rows: 4, placeholder: "Composer migration starts in Phase 3." }),
  );
}
