local M = { active = nil }

function M.begin(opts)
  if M.active then return false, "another editor request is already running" end
  M.active = {
    kind = opts.kind or "request",
    hidden = opts.hidden == true,
    on_complete = opts.on_complete,
    on_error = opts.on_error,
    text = "",
    error = nil,
  }
  return true
end

function M.fail(message)
  local request = M.active
  M.active = nil
  if request and request.on_error then
    vim.schedule(function() request.on_error(message) end)
  end
end

---Capture a specialized response. Returns true when the event should not be
---rendered into normal chat (hints/completions use compact native UI instead).
function M.handle(event)
  local request = M.active
  if not request then return false end
  local kind = event.kind
  if kind == "assistant_delta" then
    request.text = request.text .. tostring(event.text or event.delta or "")
  elseif kind == "error" then
    request.error = tostring(event.text or event.message or "Request failed")
  elseif kind == "turn_complete" then
    M.active = nil
    local callback = request.on_complete
    if callback then
      vim.schedule(function() callback(request.text, request.error, event.result) end)
    end
  end
  if not request.hidden then return false end
  return kind == "assistant_start"
    or kind == "assistant_delta"
    or kind == "assistant_end"
    or kind == "thinking_delta"
end

function M.cancel_local()
  M.active = nil
end

return M
