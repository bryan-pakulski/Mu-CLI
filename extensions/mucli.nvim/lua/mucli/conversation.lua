local M = {}

local client = require("mucli.client")
local config = require("mucli.config")
local context = require("mucli.context")
local requests = require("mucli.requests")
local store = require("mucli.store")
local util = require("mucli.util")

function M.send(prompt, opts)
  opts = opts or {}
  prompt = tostring(prompt or ""):gsub("^%s+", ""):gsub("%s+$", "")
  if prompt == "" then return false end
  if store.state.busy then
    util.notify("This session already has a turn in progress", vim.log.levels.WARN)
    return false
  end

  local wire, context_metadata = prompt, nil
  if opts.context ~= false and not prompt:match("^/") then
    wire, context_metadata = context.compose(prompt)
  end
  if opts.on_complete or opts.on_error then
    local started, err = requests.begin({
      kind = opts.kind, hidden = opts.hidden, on_complete = opts.on_complete, on_error = opts.on_error,
    })
    if not started then util.notify(err, vim.log.levels.WARN); return false end
  end

  store.add_local_user(opts.display_text or prompt, wire)
  store.state.busy = true
  store.touch()
  if opts.open_panel ~= false then require("mucli.chat.panel").open(false) end

  client.post("/api/chat/send", { text = wire, session_name = config.get().session }, function(response)
    if response.ok then
      if context_metadata and config.get().context.clear_staged_after_send then
        context.consume(context_metadata.staged_ids)
      end
      return
    end
    store.state.busy = false
    store.add_message("error", tostring(response.error or "Failed to send message"))
    requests.fail(response.error)
  end)
  return true
end

function M.ask(prompt)
  if prompt and prompt ~= "" then return M.send(prompt) end
  vim.ui.input({ prompt = "Ask MUCLI: " }, function(value)
    if value and value ~= "" then M.send(value) end
  end)
end

function M.ephemeral(prompt, opts)
  opts = opts or {}
  prompt = tostring(prompt or "")
  if prompt == "" then return false end
  if store.state.busy then
    util.notify("This session already has a turn in progress", vim.log.levels.WARN)
    return false
  end
  local session = require("mucli.session")
  store.state.busy = true
  store.state.status = opts.status or "Running editor analysis…"
  store.touch()
  local path = "/api/extensions/" .. session.EXTENSION_ID .. "/request"
  client.post(path, {
    client_id = session.client_id,
    session_name = config.get().session,
    kind = opts.kind or "editor",
    prompt = prompt,
  }, function(response)
    store.state.busy = false
    store.state.status = nil
    store.touch()
    if not response.ok then
      if opts.on_error then opts.on_error(response.error) end
      return
    end
    if opts.on_complete then opts.on_complete((response.json or {}).text or "", nil, response.json) end
  end)
  return true
end

function M.send_selection(prompt, start_line, end_line)
  local item = context.add_selection(start_line, end_line)
  if not item then return end
  if prompt and prompt ~= "" then M.send(prompt); return end
  vim.ui.input({ prompt = "Ask about selection: " }, function(value)
    if value and value ~= "" then M.send(value) end
  end)
end

function M.send_file(prompt)
  if not context.add_file() then return end
  if prompt and prompt ~= "" then M.send(prompt); return end
  vim.ui.input({ prompt = "Ask about file: " }, function(value)
    if value and value ~= "" then M.send(value) end
  end)
end

return M
