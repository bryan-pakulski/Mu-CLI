import van from "../vendor/van-1.6.0.min.js";

export function createStore() {
  return {
    apiBase: van.state(localStorage.getItem("mucli_gui_api_base") || "http://127.0.0.1:8765"),
    status: van.state("Booting"),
    sessions: van.state([]),
    currentSession: van.state(""),
    history: van.state([]),
    latestEvent: van.state("No events yet"),
    connected: van.state(false),
  };
}
