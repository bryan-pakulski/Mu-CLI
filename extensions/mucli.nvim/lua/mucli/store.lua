local M = {}

M.state = {
  connection = "disconnected",
  connection_detail = nil,
  ready = false,
  busy = false,
  status = nil,
  session = nil,
  provider = nil,
  model = nil,
  messages = {},
  current_turn = nil,
  pending_echoes = {},
  version = 0,
}

local listeners = {}

local function changed()
  M.state.version = M.state.version + 1
  for _, listener in pairs(listeners) do
    local id, fn = listener.id, listener.fn
    vim.schedule(function()
      if listeners[id] then pcall(fn, M.state) end
    end)
  end
end

local function trim()
  local max = 250
  if #M.state.messages > max then
    local remove = #M.state.messages - max
    for _ = 1, remove do table.remove(M.state.messages, 1) end
  end
end

local function history_text(role, text)
  text = tostring(text or "")
  if role == "user" then
    -- Live context is transport data, not part of the user's visible message.
    -- Keep history hydration as clean as the optimistic local echo.
    text = text:match("^(.-)\n\n## MUCLI editor context") or text
  end
  return text
end

function M.subscribe(fn)
  local id = tostring(fn) .. tostring((vim.uv or vim.loop).hrtime())
  listeners[id] = { id = id, fn = fn }
  return function() listeners[id] = nil end
end

function M.touch() changed() end

function M.set_connection(status, detail)
  M.state.connection = status
  M.state.connection_detail = detail
  changed()
end

function M.set_session(info)
  info = info or {}
  M.state.session = info.name or M.state.session
  M.state.provider = info.provider or M.state.provider
  M.state.model = info.model or M.state.model
  if info.is_busy ~= nil then M.state.busy = info.is_busy end
  M.state.ready = info.active ~= false
  changed()
end

function M.add_message(role, text, extra)
  local message = vim.tbl_extend("force", {
    role = role,
    text = tostring(text or ""),
    thinking = "",
    activities = {},
  }, extra or {})
  M.state.messages[#M.state.messages + 1] = message
  trim()
  changed()
  return message
end

function M.add_local_user(display_text, wire_text)
  wire_text = wire_text or display_text
  M.state.pending_echoes[wire_text] = (M.state.pending_echoes[wire_text] or 0) + 1
  return M.add_message("user", display_text)
end

local function assistant(turn_id)
  local current = M.state.current_turn
  if current and (not turn_id or current.turn_id == turn_id) then return current end
  current = M.add_message("assistant", "", { turn_id = turn_id })
  M.state.current_turn = current
  return current
end

local function activity(kind, label, detail)
  local current = M.state.current_turn
  if not current then
    current = M.add_message("activity", "", { activities = {} })
  end
  current.activities[#current.activities + 1] = {
    kind = kind,
    label = tostring(label or kind),
    detail = detail,
  }
  changed()
end

function M.handle(event)
  if not event or type(event) ~= "table" then return end
  local kind = event.kind
  if kind == "hello" then
    M.state.connection = "connected"
    local current = M.state.session
    for _, name in ipairs(event.busy or {}) do
      if name == current then M.state.busy = true end
    end
    changed()
  elseif kind == "user_message" then
    local text = tostring(event.text or "")
    local count = M.state.pending_echoes[text] or 0
    if count > 0 then
      M.state.pending_echoes[text] = count == 1 and nil or count - 1
    else
      M.add_message("user", text)
    end
    M.state.busy = true
  elseif kind == "assistant_start" then
    assistant(event.turn_id)
    M.state.busy = true
  elseif kind == "assistant_delta" then
    local message = assistant(event.turn_id)
    message.text = message.text .. tostring(event.text or event.delta or "")
    changed()
  elseif kind == "thinking_delta" then
    local message = assistant(event.turn_id)
    message.thinking = message.thinking .. tostring(event.text or event.delta or "")
    changed()
  elseif kind == "assistant_end" then
    M.state.current_turn = nil
    changed()
  elseif kind == "message" then
    M.add_message(event.role or "assistant", event.content or event.text or "")
  elseif kind == "tool_call" then
    activity("tool", event.tool_name or "tool", event)
  elseif kind == "tool_result" then
    activity("result", "Tool result", event.text)
  elseif kind == "diff" then
    activity("diff", "Proposed changes · " .. tostring(event.filename or "file"), event)
  elseif kind == "artifact_created" then
    local artifact = event.artifact or event
    activity("artifact", artifact.name or artifact.artifact_id or "Artifact", artifact)
  elseif kind == "info" then
    activity(kind, event.text or "Command complete", event.result)
  elseif kind == "command_result" then
    activity(kind, event.text or "Command complete", event.result)
    M.state.busy = false
    M.state.status = nil
    changed()
  elseif kind == "status_start" or kind == "status_update" then
    M.state.status = event.text or "Working"
    M.state.busy = true
    changed()
  elseif kind == "status_end" then
    M.state.status = nil
    changed()
  elseif kind == "turn_complete" then
    M.state.busy = false
    M.state.status = nil
    M.state.current_turn = nil
    changed()
  elseif kind == "error" or kind == "protocol_error" then
    M.state.busy = false
    M.state.status = nil
    M.add_message("error", event.text or event.message or "Unknown error")
  end
end

function M.load_history(payload)
  if type(payload) ~= "table" or type(payload.turns) ~= "table" then return end
  local messages = {}
  for _, turn in ipairs(payload.turns) do
    local chunks, activities, thinking = {}, {}, {}
    for _, part in ipairs(turn.parts or {}) do
      if part.type == "text" then
        chunks[#chunks + 1] = tostring(part.text or "")
      elseif part.type == "thinking" then
        thinking[#thinking + 1] = tostring(part.text or "")
      elseif part.type == "tool_call" then
        activities[#activities + 1] = { kind = "tool", label = part.tool_name or "tool", detail = part }
      elseif part.type == "tool_result" then
        activities[#activities + 1] = { kind = "result", label = (part.tool_name or "tool") .. " result", detail = part.preview }
      elseif part.type == "visualization" then
        activities[#activities + 1] = { kind = "artifact", label = "Visualization", detail = part.artifact }
      elseif part.type == "attachment" then
        local attachment = part.attachment or {}
        activities[#activities + 1] = { kind = "artifact", label = attachment.name or "Attachment", detail = attachment }
      end
    end
    if #chunks > 0 or #activities > 0 then
      messages[#messages + 1] = {
        role = turn.role or "assistant",
        text = history_text(turn.role, table.concat(chunks, "\n\n")),
        thinking = table.concat(thinking, "\n"),
        activities = activities,
      }
    end
  end
  M.state.messages = messages
  M.state.current_turn = nil
  trim()
  changed()
end

function M.reset()
  M.state.messages = {}
  M.state.current_turn = nil
  M.state.pending_echoes = {}
  changed()
end

return M
