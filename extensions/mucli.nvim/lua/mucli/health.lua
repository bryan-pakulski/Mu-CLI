--- lua/mucli/health.lua — Health check for mucli neovim extension.
-- Verifies: plenary.curl available, mucli server reachable, session configured,
-- neovim extension enabled on session.

local M = {}

function M.check_health()
  local results = {}

  -- 1. plenary.curl available
  local ok_curl, _ = pcall(require, "plenary.curl")
  if ok_curl then
    table.insert(results, { name = "plenary.curl", status = "ok", msg = "plenary.curl loaded" })
  else
    table.insert(results, { name = "plenary.curl", status = "fail", msg = "plenary.curl not found — install plenary.nvim" })
  end

  -- 2. Config + session configured
  local config_ok, config = pcall(require, "mucli.config")
  if not config_ok or not config.opts then
    table.insert(results, { name = "config", status = "fail", msg = "mucli.config not loaded — call require('mucli').setup() first" })
    return results
  end

  if not config.opts.session then
    table.insert(results, { name = "session", status = "fail", msg = "session not configured — set session in setup() opts" })
  else
    table.insert(results, { name = "session", status = "ok", msg = "session: " .. config.opts.session })
  end

  -- 3. mucli server reachable (GET /healthz)
  local client_ok, client = pcall(require, "mucli.client")
  if not client_ok then
    table.insert(results, { name = "server", status = "fail", msg = "mucli.client not loaded" })
    return results
  end

  local reached = false
  client.get("/healthz", function(resp)
    if resp and resp.status < 400 then
      reached = true
    end
  end)
  -- Synchronous wait (up to 3s in blocking mode for health check)
  vim.wait(3000, function() return reached end, 50)
  if reached then
    table.insert(results, { name = "server", status = "ok", msg = "mucli server reachable at " .. (config.opts.host or "localhost:30311") })
  else
    table.insert(results, { name = "server", status = "fail", msg = "mucli server not reachable — start mucli with --gui" })
  end

  -- 4. neovim extension registered
  local session_ok, session = pcall(require, "mucli.session")
  if not session_ok then
    table.insert(results, { name = "extension_registered", status = "fail", msg = "mucli.session not loaded" })
    return results
  end

  local registered = false
  local client_ok, client = pcall(require, "mucli.client")
  if client_ok then
    client.get("/api/extensions", function(resp)
      if resp and resp.status == 200 then
        local ok, data = pcall(vim.json.decode, resp.body or "{}")
        if ok and data then
          for _, ext in ipairs(data.extensions or {}) do
            if ext.extension_id == "neovim" then
              registered = true
              break
            end
          end
        end
      end
    end)
  end
  vim.wait(3000, function() return registered end, 50)
  if registered then
    table.insert(results, { name = "extension_registered", status = "ok", msg = "neovim extension registered" })
  else
    table.insert(results, { name = "extension_registered", status = "warn", msg = "neovim extension not registered — check /api/extensions" })
  end

  return results
end

--- Print health check results to messages
function M.report()
  local results = M.check_health()
  for _, r in ipairs(results) do
    local level = vim.log.levels.INFO
    if r.status == "fail" then level = vim.log.levels.ERROR
    elseif r.status == "warn" then level = vim.log.levels.WARN end
    vim.notify(string.format("[mucli] %s: %s — %s", r.name, r.status, r.msg), level)
  end
end

return M