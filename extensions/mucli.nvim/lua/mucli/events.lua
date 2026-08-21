local M = { started = false, listeners = {} }

local client = require("mucli.client")
local config = require("mucli.config")
local requests = require("mucli.requests")
local store = require("mucli.store")

function M.subscribe(fn)
  local id = tostring(fn) .. tostring((vim.uv or vim.loop).hrtime())
  M.listeners[id] = fn
  return function() M.listeners[id] = nil end
end

local function publish(event)
  for _, listener in pairs(M.listeners) do pcall(listener, event) end
end

function M.handle(event)
  if type(event) ~= "table" then return end
  local ours = config.get().session
  if event.session_name and ours and event.session_name ~= ours then return end

  if event.kind == "extension_tool_call" then
    require("mucli.tools").handle_tool_call(event)
  elseif event.kind == "prompt" or event.kind == "prompt_cancelled" or event.kind == "prompt_resolved" then
    require("mucli.prompts").handle(event)
  elseif event.kind == "diff" then
    require("mucli.diff").capture_event(event)
  elseif event.kind == "history_refresh" then
    vim.defer_fn(function() require("mucli.session").load_history() end, 100)
  end

  local suppress = requests.handle(event)
  if not suppress then store.handle(event) end
  publish(event)
end

function M.start()
  if M.started then return end
  M.started = true
  client.start_sse(M.handle, function(status, detail)
    store.set_connection(status, detail)
  end)
end

function M.stop()
  M.started = false
  client.stop_sse()
  store.set_connection("disconnected")
end

return M
