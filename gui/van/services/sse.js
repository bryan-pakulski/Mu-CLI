export function createSseClient({ apiBaseState, onEvent, onOpen, onError }) {
  let eventSource = null;

  const open = () => {
    if (eventSource) return;
    const base = String(apiBaseState.val || "").replace(/\/$/, "");
    eventSource = new EventSource(`${base}/api/events`);

    eventSource.onopen = () => onOpen?.();
    eventSource.onerror = (error) => onError?.(error);
    eventSource.onmessage = (event) => onEvent?.({ event: "message", data: event.data });

    const names = [
      "stream.open",
      "task.updated",
      "task.started",
      "task.completed",
      "task.error",
      "approval.pending",
      "approval.resolved",
    ];
    for (const name of names) {
      eventSource.addEventListener(name, (evt) => onEvent?.({ event: name, data: evt.data }));
    }
  };

  const close = () => {
    if (!eventSource) return;
    eventSource.close();
    eventSource = null;
  };

  return { open, close };
}
