local M = {}

local config = require("mucli.config")
local sse = require("mucli.sse")

local stream = {
  process = nil,
  parser = nil,
  enabled = false,
  generation = 0,
  delay = 500,
  callback = nil,
  status_callback = nil,
}

local function endpoint(path)
  return config.get().host .. path
end

local function headers()
  local result = { "Accept: application/json" }
  local token = config.get().token
  if token and token ~= "" then result[#result + 1] = "Authorization: Bearer " .. token end
  return result
end

local function curl_args(method, path, body, streaming)
  local args = {
    "curl", "--silent", "--show-error", "--location",
    "--connect-timeout", "3", "--max-time", streaming and "0" or "180",
    "--request", method,
  }
  if streaming then
    vim.list_extend(args, { "--no-buffer", "--header", "Accept: text/event-stream" })
  else
    vim.list_extend(args, { "--write-out", "\n%{http_code}" })
  end
  for _, header in ipairs(headers()) do
    vim.list_extend(args, { "--header", header })
  end
  if body ~= nil then
    vim.list_extend(args, { "--header", "Content-Type: application/json", "--data-binary", "@-" })
  end
  args[#args + 1] = endpoint(path)
  return args
end

function M._parse_http(stdout, code, stderr)
  stdout = stdout or ""
  local body, status = stdout:match("^(.*)\n(%d%d%d)$")
  status = tonumber(status) or 0
  if not body then body = stdout end
  local parsed
  if body ~= "" then
    local ok, value = pcall(vim.json.decode, body)
    if ok then parsed = value end
  end
  local ok = code == 0 and status >= 200 and status < 300
  local transport_error = stderr and stderr:gsub("%s+$", "") or ""
  if transport_error == "" then
    transport_error = status > 0 and ("HTTP " .. status) or ("curl exited with code " .. tostring(code))
  end
  return {
    ok = ok,
    status = status,
    body = body,
    json = parsed,
    error = ok and nil or ((parsed and parsed.detail) or transport_error),
  }
end

function M.request(method, path, body, callback)
  local payload = body ~= nil and vim.json.encode(body) or nil
  local process = vim.system(curl_args(method, path, body, false), {
    text = true,
    stdin = payload,
  }, function(result)
    vim.schedule(function()
      if callback then callback(M._parse_http(result.stdout, result.code, result.stderr)) end
    end)
  end)
  return process
end

function M.request_sync(method, path, body, timeout)
  local payload = body ~= nil and vim.json.encode(body) or nil
  local result = vim.system(curl_args(method, path, body, false), {
    text = true,
    stdin = payload,
  }):wait(timeout or 5000)
  return M._parse_http(result.stdout, result.code, result.stderr)
end

function M.get(path, callback) return M.request("GET", path, nil, callback) end
function M.post(path, body, callback) return M.request("POST", path, body or {}, callback) end
function M.put(path, body, callback) return M.request("PUT", path, body or {}, callback) end
function M.get_sync(path, timeout) return M.request_sync("GET", path, nil, timeout) end
function M.post_sync(path, body, timeout) return M.request_sync("POST", path, body or {}, timeout) end

local function set_stream_status(status, detail)
  if stream.status_callback then
    vim.schedule(function() stream.status_callback(status, detail) end)
  end
end

function M._connect_sse(generation)
  if not stream.enabled or generation ~= stream.generation then return end
  set_stream_status("connecting")
  stream.parser = sse.Parser.new(function(event)
    stream.delay = 500
    vim.schedule(function()
      if stream.enabled and generation == stream.generation and stream.callback then
        stream.callback(event)
      end
    end)
  end)

  local stderr = {}
  stream.process = vim.system(curl_args("GET", "/api/events", nil, true), {
    text = true,
    stdout = function(err, data)
      if err then return end
      if data and stream.parser then stream.parser:feed(data) end
    end,
    stderr = function(_, data)
      if data then stderr[#stderr + 1] = data end
    end,
  }, function(result)
    stream.process = nil
    if not stream.enabled or generation ~= stream.generation then return end
    if stream.parser then stream.parser:finish() end
    set_stream_status("disconnected", table.concat(stderr):gsub("%s+$", ""))
    local delay = stream.delay
    stream.delay = math.min(stream.delay * 2, 30000)
    vim.defer_fn(function() M._connect_sse(generation) end, delay)
  end)
end

function M.start_sse(callback, status_callback)
  M.stop_sse()
  stream.enabled = true
  stream.callback = callback
  stream.status_callback = status_callback
  stream.delay = 500
  stream.generation = stream.generation + 1
  M._connect_sse(stream.generation)
end

function M.stop_sse()
  stream.enabled = false
  stream.generation = stream.generation + 1
  if stream.process then
    pcall(stream.process.kill, stream.process, 15)
    stream.process = nil
  end
  stream.parser = nil
end

function M.is_sse_running()
  return stream.enabled and stream.process ~= nil
end

return M
