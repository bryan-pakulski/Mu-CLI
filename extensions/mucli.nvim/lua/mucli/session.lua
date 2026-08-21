local M = {}

local config = require("mucli.config")
local client = require("mucli.client")

--- Validate that session is configured. Errors if nil.
--- @return string session name
function M.require_session()
  if not config.opts or not config.opts.session then
    error("[mucli] No session configured. Set `session` in setup():\n" ..
      "require('mucli').setup({ session = 'my-session' })")
  end
  return config.opts.session
end

--- Get active session metadata
--- @param callback function|nil Called with session info table
--- @return table|nil session info (sync mode)
function M.get_active(callback)
  local session_name = M.require_session()
  if callback then
    client.get("/api/sessions/active?session_name=" .. session_name, function(resp)
      local ok, parsed = pcall(vim.json.decode, resp.body or "{}")
      callback(ok and parsed or nil)
    end)
  else
    local resp = client.get_sync("/api/sessions/active?session_name=" .. session_name)
    local ok, parsed = pcall(vim.json.decode, resp.body or "{}")
    return ok and parsed or nil
  end
end

--- List all sessions
--- @param callback function|nil Called with sessions list
--- @return table|nil sessions (sync mode)
function M.list_sessions(callback)
  if callback then
    client.get("/api/sessions", function(resp)
      local ok, parsed = pcall(vim.json.decode, resp.body or "{}")
      callback(ok and parsed or nil)
    end)
  else
    local resp = client.get_sync("/api/sessions")
    local ok, parsed = pcall(vim.json.decode, resp.body or "{}")
    return ok and parsed or nil
  end
end

--- Create or load a session with provider+model
--- @param opts table {provider?, model?}
--- @param callback function|nil
--- @return table|nil response (sync mode)
function M.create_or_load(opts, callback)
  local session_name = M.require_session()
  opts = opts or {}
  local body = {
    name = session_name,
    provider = opts.provider or config.opts.provider,
    model = opts.model or config.opts.model,
    activate = true,
  }
  if callback then
    client.post("/api/sessions/" .. session_name .. "/load", body, function(resp)
      local ok, parsed = pcall(vim.json.decode, resp.body or "{}")
      callback(ok and parsed or nil, resp.status)
    end)
  else
    local resp = client.post_sync("/api/sessions/" .. session_name .. "/load", body)
    local ok, parsed = pcall(vim.json.decode, resp.body or "{}")
    return ok and parsed or nil, resp.status
  end
end

--- Fetch available models for a specific provider
--- @param provider string Provider name (gemini, ollama, openai)
--- @param callback function|nil Called with model list
--- @return table|nil models (sync mode)
function M.fetch_models(provider, callback)
  provider = provider or config.opts.provider or "gemini"
  local path = "/api/providers/" .. provider .. "/models"
  if callback then
    client.get(path, function(resp)
      local ok, parsed = pcall(vim.json.decode, resp.body or "{}")
      callback(ok and (parsed.models or parsed.items or parsed) or nil)
    end)
  else
    local resp = client.get_sync(path)
    local ok, parsed = pcall(vim.json.decode, resp.body or "{}")
    return ok and (parsed.models or parsed.items or parsed) or nil
  end
end

--- Switch provider/model on session
--- @param provider string New provider
--- @param model string New model
--- @param callback function|nil
--- @return table|nil response (sync mode)
function M.switch_model(provider, model, callback)
  local session_name = M.require_session()
  local body = { provider = provider, model = model }
  if callback then
    client.post("/api/sessions/" .. session_name .. "/load", body, function(resp)
      local ok, parsed = pcall(vim.json.decode, resp.body or "{}")
      callback(ok and parsed or nil, resp.status)
    end)
  else
    local resp = client.post_sync("/api/sessions/" .. session_name .. "/load", body)
    local ok, parsed = pcall(vim.json.decode, resp.body or "{}")
    return ok and parsed or nil, resp.status
  end
end

--- Register neovim extension on session via /api/extensions/register.
--- Sends tool definitions, system prompt, and version to the backend.
--- @param callback function|nil
--- @return table|nil response (sync mode)
function M.register_extension(callback)
  local session_name = M.require_session()
  local tools = require("mucli.tools")
  local body = {
    extension_id = "neovim",
    version = "1.0.0",
    tools = tools.TOOL_DEFINITIONS,
    system_prompt = tools.EXTENSION_SYSTEM_PROMPT,
    tool_prefix = "nvim_",
    session_name = session_name,
  }
  if callback then
    client.post("/api/extensions/register", body, function(resp)
      local ok, parsed = pcall(vim.json.decode, resp.body or "{}")
      callback(ok and parsed or nil, resp.status)
    end)
  else
    local resp = client.post_sync("/api/extensions/register", body)
    local ok, parsed = pcall(vim.json.decode, resp.body or "{}")
    return ok and parsed or nil, resp.status
  end
end

return M