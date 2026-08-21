local M = {}

local client = require("mucli.client")
local config = require("mucli.config")
local store = require("mucli.store")
local util = require("mucli.util")

M.EXTENSION_ID = "neovim"
M.VERSION = "2.0.0"
M.client_id = nil
M.heartbeat = nil
M.registered_session = nil

local function q(value) return util.encode_query(value) end
local function session_name() return config.get().session end

function M.auto_session_name(root)
  local base = vim.fn.fnamemodify(root, ":t"):gsub("[^%w_-]", "-"):lower()
  base = base ~= "" and base or "workspace"
  return ("nvim-%s-%s"):format(base, vim.fn.sha256(root):sub(1, 8))
end

function M.configure_identity()
  local opts = config.get()
  local root = util.workspace_root()
  opts.workspace.root = root
  if not opts.session or opts.session == "" then opts.session = M.auto_session_name(root) end
  if not M.client_id then
    M.client_id = "nvim-" .. util.uuid(root .. tostring(vim.v.servername or ""))
  end
  store.state.session = opts.session
  return opts.session
end

function M.get_active(callback)
  M.configure_identity()
  client.get("/api/sessions/active?session_name=" .. q(session_name()), function(response)
    callback(response.ok and response.json or nil, response)
  end)
end

function M.list(callback)
  client.get("/api/sessions", function(response)
    callback(response.ok and response.json or nil, response)
  end)
end

local function attach_workspace(info, callback)
  local root = util.workspace_root()
  local workspaces = vim.deepcopy((info and info.workspaces) or {})
  for _, workspace in ipairs(workspaces) do
    if util.normalize_path(workspace) == root then
      callback(info)
      return
    end
  end
  workspaces[#workspaces + 1] = root
  client.put("/api/sessions/" .. q(session_name()) .. "/workspace", { workspaces = workspaces }, function(response)
    if not response.ok then
      util.notify("Could not attach workspace: " .. tostring(response.error), vim.log.levels.WARN)
    end
    M.get_active(function(active) callback(active or info) end)
  end)
end

local function align_provider(info, callback)
  local opts = config.get()
  if not opts.provider or not opts.model
    or (info.provider == opts.provider and info.model == opts.model) then
    callback(info)
    return
  end
  client.post("/api/providers/switch", {
    provider = opts.provider,
    model = opts.model,
    session_name = session_name(),
  }, function(response)
    if not response.ok then
      callback(nil, response.error)
      return
    end
    M.get_active(function(active) callback(active or info) end)
  end)
end

local function finish_ready(callback)
  M.get_active(function(info, response)
    if not info or not info.active then
      callback(nil, (response and response.error) or "session did not become active")
      return
    end
    align_provider(info, function(aligned, align_error)
      if not aligned then callback(nil, align_error); return end
      attach_workspace(aligned, function(attached)
        store.set_session(attached)
        callback(attached)
      end)
    end)
  end)
end

local function load_existing(callback)
  local opts = config.get()
  local body = {}
  if opts.provider and opts.model then
    body.provider = opts.provider
    body.model = opts.model
  end
  client.post("/api/sessions/" .. q(session_name()) .. "/load", body, function(response)
    if not response.ok then callback(nil, response.error); return end
    finish_ready(callback)
  end)
end

local function create_new(callback)
  local opts = config.get()
  if not opts.provider or not opts.model then
    callback(nil, "needs_configuration")
    return
  end
  client.post("/api/sessions", {
    name = session_name(),
    provider = opts.provider,
    model = opts.model,
    activate = true,
    session_type = "workspace",
    workspace = util.workspace_root(),
  }, function(response)
    if response.status == 409 then load_existing(callback); return end
    if not response.ok then callback(nil, response.error); return end
    finish_ready(callback)
  end)
end

function M.ensure(callback)
  M.configure_identity()
  M.get_active(function(info)
    if info and info.active then
      align_provider(info, function(aligned, align_error)
        if not aligned then callback(nil, align_error); return end
        attach_workspace(aligned, function(attached)
          store.set_session(attached)
          callback(attached)
        end)
      end)
      return
    end
    M.list(function(payload, response)
      if not payload then callback(nil, response.error); return end
      local exists = false
      for _, item in ipairs(payload.sessions or {}) do
        if item.name == session_name() then exists = true; break end
      end
      if exists then load_existing(callback) else create_new(callback) end
    end)
  end)
end

function M.register(callback)
  local tools = require("mucli.tools")
  local body = {
    extension_id = M.EXTENSION_ID,
    client_id = M.client_id,
    session_name = session_name(),
    version = M.VERSION,
    tool_prefix = "nvim_",
    tools = tools.DEFINITIONS,
    system_prompt = tools.SYSTEM_PROMPT,
    capabilities = {
      "chat", "context", "diagnostics", "diff_review", "inline_hints",
      "inline_completion", "unsaved_buffers", "ephemeral_requests",
      "interactive_prompts",
    },
  }
  client.post("/api/extensions/register", body, function(response)
    if response.ok then
      M.registered_session = session_name()
      M.start_heartbeat()
    end
    if callback then callback(response.ok and response.json or nil, response) end
  end)
end

function M.start_heartbeat()
  M.stop_heartbeat()
  M.heartbeat = (vim.uv or vim.loop).new_timer()
  M.heartbeat:start(30000, 30000, vim.schedule_wrap(function()
    client.post("/api/extensions/" .. M.EXTENSION_ID .. "/heartbeat", {
      client_id = M.client_id,
      session_name = session_name(),
    }, function(response)
      if response.status == 404 then
        M.stop_heartbeat()
        require("mucli").reconnect(function(success, err)
          if not success and err ~= "needs_configuration" then
            util.notify("Could not restore the editor session: " .. tostring(err), vim.log.levels.WARN)
          end
        end)
      elseif response.status == 409 then
        M.stop_heartbeat()
        M.registered_session = nil
        util.notify(
          "This session was attached by another Neovim client; run :MucliSetup to take control",
          vim.log.levels.WARN
        )
      end
    end)
  end))
end

function M.stop_heartbeat()
  if M.heartbeat then
    pcall(M.heartbeat.stop, M.heartbeat)
    pcall(M.heartbeat.close, M.heartbeat)
    M.heartbeat = nil
  end
end

function M.unregister(target_session)
  M.stop_heartbeat()
  target_session = target_session or M.registered_session or config.get().session
  if not M.client_id or not target_session then return end
  local path = ("/api/extensions/%s/unregister?session_name=%s&client_id=%s")
    :format(M.EXTENSION_ID, q(target_session), q(M.client_id))
  client.post(path, {}, function() end)
  if target_session == M.registered_session then M.registered_session = nil end
end

function M.set_variable(key, value, callback)
  local path = "/api/variables/" .. q(key) .. "?session_name=" .. q(session_name())
  client.post(path, { value = value }, callback)
end

function M.switch_provider(provider, model, callback)
  client.post("/api/providers/switch", {
    provider = provider,
    model = model,
    session_name = session_name(),
  }, function(response)
    if response.ok then
      config.get().provider = provider
      config.get().model = model
      store.set_session({ provider = provider, model = model })
    end
    if callback then callback(response.ok and response.json or nil, response) end
  end)
end

function M.fetch_models(provider, callback)
  local path = "/api/providers/" .. q(provider) .. "/models?session_name=" .. q(session_name())
  client.get(path, function(response)
    callback(response.ok and ((response.json or {}).models or {}) or {}, response)
  end)
end

function M.load_history(callback)
  local path = "/api/sessions/current/history?limit_turns=120&session_name=" .. q(session_name())
  client.get(path, function(response)
    if response.ok and response.json then store.load_history(response.json) end
    if callback then callback(response.ok and response.json or nil, response) end
  end)
end

function M.interrupt(callback)
  client.post("/api/chat/interrupt", { session_name = session_name() }, function(response)
    if callback then callback(response.ok and response.json or nil, response) end
  end)
end

return M
