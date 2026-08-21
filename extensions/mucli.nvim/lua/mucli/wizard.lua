local M = {}

local config = require("mucli.config")
local session = require("mucli.session")
local util = require("mucli.util")

local providers = {
  { label = "OpenAI", value = "openai" },
  { label = "Gemini", value = "gemini" },
  { label = "Ollama", value = "ollama" },
}

local function connect()
  require("mucli").reconnect(function(success, err)
    if success then require("mucli.chat.panel").open(true)
    elseif err ~= "needs_configuration" then util.notify(err, vim.log.levels.ERROR) end
  end)
end

local function pick_model(provider, callback)
  util.notify("Loading " .. provider .. " models…")
  session.fetch_models(provider, function(models)
    if #models == 0 then
      vim.ui.input({ prompt = "Model name: " }, function(model)
        if model and model ~= "" then callback(model) end
      end)
      return
    end
    vim.ui.select(models, { prompt = "MUCLI model", format_item = tostring }, function(model)
      if model then callback(type(model) == "table" and (model.id or model.name) or model) end
    end)
  end)
end

function M.pick_provider(callback)
  vim.ui.select(providers, { prompt = "MUCLI provider", format_item = function(item) return item.label end }, function(provider)
    if not provider then return end
    pick_model(provider.value, function(model) callback(provider.value, model) end)
  end)
end

local function create_session()
  local default = session.auto_session_name(util.workspace_root())
  vim.ui.input({ prompt = "New MUCLI session: ", default = default }, function(name)
    if not name or name == "" then return end
    M.pick_provider(function(provider, model)
      local opts = config.get()
      opts.session, opts.provider, opts.model = name, provider, model
      connect()
    end)
  end)
end

function M.start()
  session.list(function(payload, response)
    if not payload then util.notify(response.error, vim.log.levels.ERROR); return end
    local choices = { { label = "+ Create workspace session", create = true } }
    for _, item in ipairs(payload.sessions or {}) do
      choices[#choices + 1] = {
        label = item.name,
        name = item.name,
        detail = table.concat({ item.provider or "", item.model or "" }, " · "),
      }
    end
    vim.ui.select(choices, {
      prompt = "MUCLI session",
      format_item = function(item) return item.detail and (item.label .. "  " .. item.detail) or item.label end,
    }, function(choice)
      if not choice then return end
      if choice.create then create_session(); return end
      config.get().session = choice.name
      config.get().provider, config.get().model = nil, nil
      connect()
    end)
  end)
end

function M.switch_model()
  local current_provider = require("mucli.store").state.provider or config.get().provider
  local function choose(provider)
    pick_model(provider, function(model)
      session.switch_provider(provider, model, function(result, response)
        if result then util.notify(("Using %s · %s"):format(provider, model))
        else util.notify(response.error, vim.log.levels.ERROR) end
      end)
    end)
  end
  if current_provider then choose(current_provider)
  else M.pick_provider(function(provider, model)
    session.switch_provider(provider, model, function() end)
  end) end
end

function M.switch_provider()
  M.pick_provider(function(provider, model)
    session.switch_provider(provider, model, function(result, response)
      if result then util.notify(("Using %s · %s"):format(provider, model))
      else util.notify(response.error, vim.log.levels.ERROR) end
    end)
  end)
end

function M.reconfigure()
  local choices = {
    { label = "Switch session", action = M.start },
    { label = "Switch model", action = M.switch_model },
    { label = "Switch provider", action = M.switch_provider },
  }
  vim.ui.select(choices, { prompt = "Configure MUCLI", format_item = function(item) return item.label end }, function(choice)
    if choice then choice.action() end
  end)
end

-- Compatibility shims for configurations that called proof-of-concept helpers.
M._pick_provider = function(_, _, on_complete)
  M.pick_provider(function(provider, model)
    config.get().provider, config.get().model = provider, model
    if on_complete then on_complete(config.get()) end
  end)
end
M._pick_model = function(_, _, provider, _, on_complete)
  pick_model(provider, function(model)
    config.get().provider, config.get().model = provider, model
    if on_complete then on_complete(config.get()) end
  end)
end

return M
