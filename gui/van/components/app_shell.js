import van from "../../vendor/van-1.6.0.min.js";
import { StatusHeader } from "./layout/status_header.js";
import { SessionList } from "./sessions/session_list.js";
import { ChatPanel } from "./chat/chat_panel.js";

const { div, main } = van.tags;

export function AppShell(store, api, onRefresh) {
  return main({ class: "van-shell" },
    StatusHeader(store, onRefresh),
    div({ class: "van-layout" },
      SessionList(store, api),
      ChatPanel(store),
    ),
  );
}
