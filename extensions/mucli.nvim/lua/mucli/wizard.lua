--- Interactive setup wizard for mucli neovim extension.
--- When session is not configured, prompts user for:
---   1. Session name (list existing or create new)
---   2. Provider (gemini, ollama, openai)
---   3. Ollama local/cloud (if ollama selected)
---   4. Model (fetched from provider)
--- On completion, calls back with resolved config table.
--- If session exists and has provider+model, skips prompting.

local M = {}

local config = require("mucli.config")
local client = require("mucli.client")
local session = require("mucli.session")

--- Fetch sessions list from backend (sync).
--- @return table sessions list (names)
local function _fetch_sessions()
  local resp = client.get_sync("/api/sessions")
  if not resp or resp.status >= 400 then return {} end
  local ok, parsed = pcall(vim.json.decode, resp.body or "{}")
  if not ok then return {} end
  -- API returns {sessions: [...]} or {items: [...]}
  return parsed.sessions or parsed.items or {}
end

--- Fetch session metadata (sync). Returns provider+model if set.
--- @param name string session name
--- @return table|nil {provider, model} or nil
local function _fetch_session_meta(name)
  local resp = client.get_sync("/api/sessions/active?session_name=" .. name)
  if not resp or resp.status >= 400 then return nil end
  local ok, parsed = pcall(vim.json.decode, resp.body or "{}")
  if not ok then return nil end
  return parsed
end

--- Fetch models for a provider (sync).
--- @param provider string provider name
--- @return table models list
local function _fetch_models(provider)
  local resp = client.get_sync("/api/providers/" .. (provider or "gemini") .. "/models")
  if not resp or resp.status >= 400 then return {} end
  local ok, parsed = pcall(vim.json.decode, resp.body or "{}")
  if not ok then return {} end
  return parsed.models or parsed.items or parsed or {}
end

--- Run the interactive setup wizard.
--- Called by init.lua setup() when config.opts.session is nil.
--- @param on_complete function Called with resolved config table
function M.run(on_complete)
  -- Step 1: Session selection
  local sessions = _fetch_sessions()
  local session_options = {}

  if #sessions > 0 then
    for _, s in ipairs(sessions) do
      local name = type(s) == "string" and s or (s.name or s)
      table.insert(session_options, name)
    end
  end
  table.insert(session_options, "++ Create new session ++")

  vim.ui.select(session_options, { prompt = "Select mucli session:" }, function(choice)
    if not choice then
      vim.notify("[mucli] Setup cancelled", vim.log.levels.WARN)
      return
    end

    local session_name
    if choice == "++ Create new session ++" then
      session_name = vim.fn.input("New session name: ")
      if session_name == "" then
        vim.notify("[mucli] Session name required", vim.log.levels.ERROR)
        return
      end
      -- New session: need provider + model
      M._pick_provider(session_name, false, on_complete)
    else
      session_name = choice
      -- Existing session: check if it has provider+model configured
      local meta = _fetch_session_meta(session_name)
      if meta and meta.provider and meta.model then
        -- Session already configured — don't reprompt
        vim.notify(
          string.format("[mucli] Loading session '%s' (provider=%s, model=%s)", session_name, meta.provider, meta.model),
          vim.log.levels.INFO
        )
        config.opts.session = session_name
        config.opts.provider = meta.provider
        config.opts.model = meta.model
        on_complete(config.opts)
      else
        -- Session exists but no provider/model — prompt
        M._pick_provider(session_name, true, on_complete)
      end
    end
  end)
end

--- Step 2: Provider selection
--- @param session_name string
--- @param is_existing boolean true if session already exists (load vs create)
--- @param on_complete function
function M._pick_provider(session_name, is_existing, on_complete)
  local providers = {
    { label = "Gemini",  value = "gemini",  desc = "Google Gemini models" },
    { label = "Ollama",  value = "ollama",  desc = "Local daemon or Ollama cloud" },
    { label = "OpenAI",  value = "openai",  desc = "OpenAI API models" },
  }

  vim.ui.select(providers, {
    prompt = "Select provider:",
    format_item = function(item)
      return item.label .. " — " .. item.desc
    end,
  }, function(choice)
    if not choice then
      vim.notify("[mucli] Setup cancelled", vim.log.levels.WARN)
      return
    end

    if choice.value == "ollama" then
      -- Step 3a: Ollama local/cloud
      M._pick_ollama_mode(session_name, is_existing, choice.value, on_complete)
    else
      -- Step 3b: Fetch models for gemini/openai
      M._pick_model(session_name, is_existing, choice.value, nil, on_complete)
    end
  end)
end

--- Step 3a: Ollama local vs cloud selection
--- @param session_name string
--- @param is_existing boolean
--- @param provider string "ollama"
--- @param on_complete function
function M._pick_ollama_mode(session_name, is_existing, provider, on_complete)
  local modes = {
    { label = "Local", value = "local", desc = "Use OLLAMA_HOST or local Ollama daemon" },
    { label = "Cloud", value = "cloud", desc = "Use ollama.com with an API key" },
  }

  vim.ui.select(modes, {
    prompt = "Ollama connection:",
    format_item = function(item)
      return item.label .. " — " .. item.desc
    end,
  }, function(choice)
    if not choice then
      vim.notify("[mucli] Setup cancelled", vim.log.levels.WARN)
      return
    end

    -- Store ollama mode in config
    config.opts.ollama_mode = choice.value

    -- For cloud, prompt for API key if not in env
    if choice.value == "cloud" then
      local api_key = vim.fn.input("Ollama API key (or press Enter to use OLLAMA_API_KEY env): ")
      if api_key ~= "" then
        config.opts.ollama_api_key = api_key
      end
    end

    -- Fetch models with selected mode
    M._pick_model(session_name, is_existing, provider, choice.value, on_complete)
  end)
end

--- Step 4: Model selection
--- @param session_name string
--- @param is_existing boolean
--- @param provider string
--- @param ollama_mode string|nil "local" or "cloud" for ollama
--- @param on_complete function
function M._pick_model(session_name, is_existing, provider, ollama_mode, on_complete)
  vim.notify("[mucli] Fetching available models for " .. provider .. "...", vim.log.levels.INFO)

  local models = _fetch_models(provider)
  if not models or #models == 0 then
    -- No models available — prompt manually
    local model_name = vim.fn.input("Enter model name manually for " .. provider .. ": ")
    if model_name == "" then
      vim.notify("[mucli] Model name required", vim.log.levels.ERROR)
      return
    end
    M._finalize(session_name, is_existing, provider, model_name, ollama_mode, on_complete)
    return
  end

  vim.ui.select(models, {
    prompt = "Select model:",
    format_item = function(item)
      if type(item) == "string" then return item end
      return item.name or item.id or tostring(item)
    end,
  }, function(choice)
    if not choice then
      vim.notify("[mucli] Setup cancelled", vim.log.levels.WARN)
      return
    end

    local model_name = type(choice) == "string" and choice or (choice.name or choice.id or tostring(choice))
    M._finalize(session_name, is_existing, provider, model_name, ollama_mode, on_complete)
  end)
end

--- Finalize: save config, create/load session, register extension
--- @param session_name string
--- @param is_existing boolean
--- @param provider string
--- @param model string
--- @param ollama_mode string|nil
--- @param on_complete function
function M._finalize(session_name, is_existing, provider, model, ollama_mode, on_complete)
  -- Update config
  config.opts.session = session_name
  config.opts.provider = provider
  config.opts.model = model
  if ollama_mode then
    config.opts.ollama_mode = ollama_mode
  end

  -- Create or load session on backend
  local body = {
    name = session_name,
    provider = provider,
    model = model,
    activate = true,
  }
  if ollama_mode then
    body.ollama_mode = ollama_mode
  end

  local endpoint = is_existing
    and ("/api/sessions/" .. session_name .. "/load")
    or "/api/sessions"

  client.post(endpoint, body, function(resp)
    vim.schedule(function()
      if not resp or resp.status >= 400 then
        vim.notify(
          "[mucli] Failed to " .. (is_existing and "load" or "create") .. " session: "
            .. tostring(resp and resp.status or "no response"),
          vim.log.levels.ERROR
        )
        return
      end

      vim.notify(
        string.format("[mucli] Session '%s' ready (provider=%s, model=%s)", session_name, provider, model),
        vim.log.levels.INFO
      )

      -- Register extension
      session.register_extension(function(ext_resp)
        vim.schedule(function()
          if ext_resp and ext_resp.status and ext_resp.status < 400 then
            vim.notify("[mucli] Neovim extension registered", vim.log.levels.INFO)
          else
            vim.notify("[mucli] Extension registration failed (non-fatal)", vim.log.levels.WARN)
          end
          on_complete(config.opts)
        end)
      end)
    end)
  end)
end

--- Reconfigure session, provider, or model interactively.
--- Called by :MucliConfig command.
function M.reconfigure()
  vim.ui.select({
    { label = "Switch session", value = "session" },
    { label = "Switch provider", value = "provider" },
    { label = "Switch model",    value = "model" },
    { label = "Full setup wizard", value = "wizard" },
  }, {
    prompt = "MuCLI configuration:",
    format_item = function(item) return item.label end,
  }, function(choice)
    if not choice then return end

    if choice.value == "session" then
      M._reconfigure_session()
    elseif choice.value == "provider" then
      M._reconfigure_provider()
    elseif choice.value == "model" then
      M._reconfigure_model()
    elseif choice.value == "wizard" then
      config.opts.session = nil
      M.run(function(opts)
        vim.notify("[mucli] Reconfiguration complete", vim.log.levels.INFO)
      end)
    end
  end)
end

function M._reconfigure_session()
  local sessions = _fetch_sessions()
  if #sessions == 0 then
    vim.notify("[mucli] No saved sessions found", vim.log.levels.WARN)
    return
  end
  local names = {}
  for _, s in ipairs(sessions) do
    table.insert(names, type(s) == "string" and s or (s.name or s))
  end

  vim.ui.select(names, { prompt = "Switch to session:" }, function(choice)
    if not choice then return end
    config.opts.session = choice
    local meta = _fetch_session_meta(choice)
    if meta and meta.provider then config.opts.provider = meta.provider end
    if meta and meta.model then config.opts.model = meta.model end
    client.post("/api/sessions/" .. choice .. "/load", { activate = true }, function(resp)
      vim.schedule(function()
        if resp and resp.status < 400 then
          vim.notify("[mucli] Switched to session: " .. choice, vim.log.levels.INFO)
        else
          vim.notify("[mucli] Failed to switch session", vim.log.levels.ERROR)
        end
      end)
    end)
  end)
end

function M._reconfigure_provider()
  M._pick_provider(config.opts.session or "default", config.opts.session ~= nil, function(opts)
    vim.notify("[mucli] Provider switched to: " .. tostring(opts.provider), vim.log.levels.INFO)
  end)
end

function M._reconfigure_model()
  local provider = config.opts.provider
  if not provider then
    vim.notify("[mucli] No provider configured. Run :MucliConfig first.", vim.log.levels.WARN)
    return
  end

  local models = _fetch_models(provider)
  if not models or #models == 0 then
    vim.notify("[mucli] No models available for " .. provider, vim.log.levels.WARN)
    return
  end

  vim.ui.select(models, {
    prompt = "Select model:",
    format_item = function(item)
      return type(item) == "string" and item or (item.name or item.id or tostring(item))
    end,
  }, function(choice)
    if not choice then return end
    local model_name = type(choice) == "string" and choice or (choice.name or choice.id or tostring(choice))
    config.opts.model = model_name
    session.switch_model(provider, model_name, function(resp)
      vim.schedule(function()
        if resp and resp.status < 400 then
          vim.notify("[mucli] Model switched to: " .. model_name, vim.log.levels.INFO)
        else
          vim.notify("[mucli] Failed to switch model", vim.log.levels.ERROR)
        end
      end)
    end)
  end)
end

return M