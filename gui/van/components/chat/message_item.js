import van from "../../../vendor/van-1.6.0.min.js";

const { div } = van.tags;

export function MessageItem(message) {
  const role = String(message?.role || "assistant");
  const content = String(message?.content || message?.text || "");
  return div({ class: `van-message ${role === "user" ? "user" : "assistant"}` },
    div({ class: "van-message-role" }, role),
    div({ class: "van-message-content" }, content || "…"),
  );
}
