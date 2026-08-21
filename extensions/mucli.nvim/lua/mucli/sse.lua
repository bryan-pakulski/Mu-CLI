local M = {}

local Parser = {}
Parser.__index = Parser

function Parser.new(on_event, decode)
  return setmetatable({
    buffer = "",
    pending_cr = false,
    on_event = on_event,
    decode = decode or vim.json.decode,
  }, Parser)
end

function Parser:_dispatch(block)
  local event_name = "message"
  local data = {}
  for line in (block .. "\n"):gmatch("(.-)\n") do
    if line:sub(1, 1) ~= ":" then
      local colon = line:find(":", 1, true)
      local field = colon and line:sub(1, colon - 1) or line
      local value = colon and line:sub(colon + 1) or ""
      if value:sub(1, 1) == " " then value = value:sub(2) end
      if field == "event" then
        event_name = value
      elseif field == "data" then
        data[#data + 1] = value
      end
    end
  end
  if #data == 0 then return end
  local raw = table.concat(data, "\n")
  local ok, payload = pcall(self.decode, raw)
  if not ok then
    payload = { kind = "protocol_error", message = tostring(payload), raw = raw }
  elseif type(payload) ~= "table" then
    payload = { kind = event_name, data = payload }
  end
  self.on_event(payload, event_name)
end

function Parser:feed(chunk)
  if not chunk or chunk == "" then return end
  if self.pending_cr then
    chunk = "\r" .. chunk
    self.pending_cr = false
  end
  if chunk:sub(-1) == "\r" then
    chunk = chunk:sub(1, -2)
    self.pending_cr = true
  end
  self.buffer = self.buffer .. chunk:gsub("\r\n", "\n"):gsub("\r", "\n")
  while true do
    local boundary = self.buffer:find("\n\n", 1, true)
    if not boundary then break end
    local block = self.buffer:sub(1, boundary - 1)
    self.buffer = self.buffer:sub(boundary + 2)
    if block ~= "" then self:_dispatch(block) end
  end
end

function Parser:finish()
  if self.pending_cr then
    self.buffer = self.buffer .. "\n"
    self.pending_cr = false
  end
  if self.buffer ~= "" then
    self:_dispatch(self.buffer)
    self.buffer = ""
  end
end

M.Parser = Parser

return M
