import van from "../../vendor/van-1.6.0.min.js";
import { StatusHeader } from "./layout/status_header.js";
import { SessionList } from "./sessions/session_list.js";
import { ChatPanel } from "./chat/chat_panel.js";
import { BoardPreview } from "./board/board_preview.js";
import { ActivityPanel } from "./activity/activity_panel.js";

const { div, main } = van.tags;

export function AppShell(store, api, onSelectFeature, actions, onRefresh) {
  return main({ class: "van-shell" },
    StatusHeader(store, onRefresh),
    div({ class: "van-layout" },
      SessionList(store, api),
      div({ class: "van-main-col" },
        ChatPanel(store, actions),
        BoardPreview(store, onSelectFeature),
      ),
      ActivityPanel(store, actions),
    ),
  );
}
