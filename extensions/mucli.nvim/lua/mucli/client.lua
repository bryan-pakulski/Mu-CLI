local M = {}

local curl = require("plenary.curl")
local config = require("mucli.config")

-- SSE stream state
M._sse_job_id = nil
M._sse_callback = nil
M._sse_buffer = ""
M._sse_reconnect_delay = 1  -- seconds, doubles on each failure
M._sse_max_reconnect_delay = 30
M._sse_should_reconnect = false

--- Build full URL from path
--- @param path string API path (e.g. "/healthz")
--- @return string Full URL
local function url(path)
  return config.opts.host .. path
end

--- POST request via plenary.curl
--- @param path string API path
--- @param body table|nil Request body (will be JSON-encoded)
--- @param callback function|nil Called with {status, body} on completion
function M.post(path, body, callback)
  curl.post(url(path), {
    headers = { content_type = "application/json" },
    body = vim.json.encode(body or {}),
    callback = function(response)
      if callback then
        vim.schedule(function()
          callback({
            status = response.status,
            body = response.body,
          })
        end)
      end
    end,
  })
end

--- GET request via plenary.curl
--- @param path string API path
--- @param callback function|nil Called with {status, body} on completion
function M.get(path, callback)
  curl.get(url(path), {
    callback = function(response)
      if callback then
        vim.schedule(function()
          callback({
            status = response.status,
            body = response.body,
          })
        end)
      end
    end,
  })
end

--- POST request (synchronous, returns response)
--- @param path string API path
--- @param body table|nil Request body
--- @return table {status, body}
function M.post_sync(path, body)
  local response = curl.post(url(path), {
    headers = { content_type = "application/json" },
    body = vim.json.encode(body or {}),
    sync = true,
  })
  return {
    status = response.status,
    body = response.body,
  }
end

--- GET request (synchronous, returns response)
--- @param path string API path
--- @return table {status, body}
function M.get_sync(path)
  local response = curl.get(url(path), { sync = true })
  return {
    status = response.status,
    body = response.body,
  }
end

--- Parse a single SSE line and dispatch to callback
--- SSE format: "event: message\ndata: {json}"
--- @param line string
local function parse_sse_line(line)
  if line == "" then
    return  -- blank line = event boundary
  end
  if line:sub(1, 6) == "event:" then
    return  -- event type line, not needed — we parse from data
  end
  if line:sub(1, 5) == "data:" then
    local json_str = line:sub(7)  -- skip "data: "
    if json_str and #json_str > 0 then
      local ok, parsed = pcall(vim.json.decode, json_str)
      if ok and M._sse_callback then
        vim.schedule(function()
          M._sse_callback(parsed)
        end)
      end
    end
  end
end

--- Process buffered SSE data — splits on newlines, dispatches complete lines
local function process_sse_buffer()
  while true do
    local nl_pos = M._sse_buffer:find("\n")
    if not nl_pos then break end
    local line = M._sse_buffer:sub(1, nl_pos - 1)
    M._sse_buffer = M._sse_buffer:sub(nl_pos + 1)
    parse_sse_line(line)
  end
end

--- Start SSE stream connection to /api/events
--- @param callback function Called with parsed event data (JSON table)
function M.start_sse(callback)
  M._sse_callback = callback
  M._sse_should_reconnect = true
  M._connect_sse()
end

--- Internal: connect to SSE endpoint via background job
function M._connect_sse()
  if not M._sse_should_reconnect then return end

  -- Use curl with streaming via job
  local sse_url = url("/api/events")
  M._sse_buffer = ""

  -- Use vim.fn.jobstart for SSE streaming
  local cmd = { "curl", "-s", "-N", "--no-buffer", sse_url }
  M._sse_job_id = vim.fn.jobstart(cmd, {
    stdout_buffered = false,
    on_stdout = function(_, data, _)
      if data then
        for _, line in ipairs(data) do
          if line and #line > 0 then
            M._sse_buffer = M._sse_buffer .. line .. "\n"
          end
        end
        process_sse_buffer()
      end
    end,
    on_exit = function(_, exit_code, _)
      M._sse_job_id = nil
      if M._sse_should_reconnect and exit_code ~= 0 then
        -- Reconnect with backoff
        vim.defer_fn(function()
          M._connect_sse()
        end, M._sse_reconnect_delay * 1000)
        M._sse_reconnect_delay = math.min(
          M._sse_reconnect_delay * 2,
          M._sse_max_reconnect_delay
        )
      end
    end,
  })

  -- Reset reconnect delay on successful connection
  M._sse_reconnect_delay = 1
end

--- Stop SSE stream
function M.stop_sse()
  M._sse_should_reconnect = false
  if M._sse_job_id then
    vim.fn.jobstop(M._sse_job_id)
    M._sse_job_id = nil
  end
  M._sse_callback = nil
  M._sse_buffer = ""
end

--- Check if SSE is running
--- @return boolean
function M.is_sse_running()
  return M._sse_job_id ~= nil
end

return M