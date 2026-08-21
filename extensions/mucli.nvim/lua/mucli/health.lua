local M = {}

local function api(name)
  return vim.health[name] or vim.health["report_" .. name]
end

function M.check()
  local start, ok, warn, err, info = api("start"), api("ok"), api("warn"), api("error"), api("info")
  start("MUCLI Neovim")
  if vim.fn.has("nvim-0.10") == 1 then ok("Neovim 0.10+ API available")
  else err("Neovim 0.10 or newer is required") end
  if vim.fn.executable("curl") == 1 then ok("curl is available for HTTP/SSE transport")
  else err("curl is required") end

  local cfg = require("mucli.config").get()
  info("Server: " .. cfg.host)
  info("Workspace: " .. require("mucli.util").workspace_root())
  if cfg.session then info("Session: " .. cfg.session) else warn("Session identity has not been configured") end

  if vim.fn.executable("curl") == 1 then
    local response = require("mucli.client").get_sync("/healthz", 3000)
    if response.ok then ok("MUCLI server is reachable")
    else err("MUCLI server is not reachable: " .. tostring(response.error)) end
    if response.ok and cfg.session then
      local path = "/api/extensions?session_name=" .. require("mucli.util").encode_query(cfg.session)
      local registered = require("mucli.client").get_sync(path, 3000)
      local found = false
      for _, extension in ipairs((registered.json or {}).extensions or {}) do
        if extension.extension_id == "neovim" and extension.connected ~= false then found = true; break end
      end
      if found then ok("Neovim editor tools are registered with the session")
      else warn("Extension is not registered yet; open :Mucli or run :MucliSetup") end
    end
  end
end

M.check_health = M.check

return M
