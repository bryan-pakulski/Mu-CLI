local M = {}

local config = require("mucli.config")
local client = require("mucli.client")
local session = require("mucli.session")

-- Module state
M._initialized = false
M._sse_started = false

--- Derive session name from cwd basename.
--- @return string session_name
local function _auto_session_name()
  local cwd = vim.fn.getcwd()
  local basename = vim.fn.fnamemodify(cwd, ":t")
  -- Sanitize: replace non-alphanumeric with hyphens
  return basename:gsub("[^a-zA-Z0-9_-]", "-"):lower()
end

--- Ensure .mucli/sessions/ exists in cwd for local session storage.
local function _ensure_local_mucli()
  local cwd = vim.fn.getcwd()
  local mucli_dir = cwd .. "/.mucli/sessions"
  vim.fn.mkdir(mucli_dir, "p")
end

--- Internal: finish initialization after session/provider/model resolved.
--- Starts SSE, registers extension, sets keymaps.
local function _finish_init()
  -- Start SSE listener — filters by session_name, dispatches to chat buffer
  client.start_sse(function(event)
    vim.schedule(function()
      local ok, panel = pcall(require, "mucli.chat.panel")
      if ok and panel.is_open() then
        local buf = panel.get_buf()
        if buf then
          local buffer = require("mucli.chat.buffer")
          -- Ensure buffer is initialized before dispatching events
          if not buffer.is_initialized(buf) then
            buffer.init_buffer(buf)
          end
          buffer.handle_sse_event(buf, event)
        end
      end
    end)
  end)
  M._sse_started = true

  -- Register keymaps (global)
  local keymaps = config.opts.keymaps
  vim.keymap.set("n", keymaps.toggle_panel, function()
    require("mucli.chat.panel").toggle()
  end, { desc = "Toggle mucli chat panel" })
  vim.keymap.set("v", keymaps.send_visual, function()
    require("mucli.context").send_visual()
  end, { desc = "Send visual selection to mucli" })
  vim.keymap.set("n", keymaps.send_file, function()
    require("mucli.context").send_file()
  end, { desc = "Send current file to mucli" })
  vim.keymap.set("n", keymaps.interrupt, function()
    require("mucli.chat.input").handle_interrupt()
  end, { desc = "Interrupt mucli turn" })

  M._initialized = true
  vim.notify("[mucli] Initialized — session: " .. config.opts.session, vim.log.levels.INFO)
end

--- Setup function — entry point for lazy.nvim config.
--- Auto-derives session name from cwd if not set, creates .mucli/ folder,
--- and launches wizard for provider/model if not configured.
--- @param opts table User configuration options
function M.setup(opts)
  config.setup(opts)

  -- Auto-derive session name from cwd if not set
  if not config.opts.session then
    config.opts.session = _auto_session_name()
  end

  -- Ensure .mucli/sessions/ exists for local storage
  _ensure_local_mucli()

  -- Set yolo mode on session
  if config.opts.yolo ~= false then
    -- Will be applied after session is created/loaded
  end

  local provider = config.opts.provider
  local model = config.opts.model

  if provider and model then
    -- Everything configured: create/load + register
    session.create_or_load({ provider = provider, model = model })
    _set_yolo()
    session.register_extension()
    _finish_init()
  else
    -- Check backend for existing session config
    local meta = session.get_active()
    if meta and meta.provider and meta.model then
      config.opts.provider = meta.provider
      config.opts.model = meta.model
      _set_yolo()
      session.register_extension()
      _finish_init()
    else
      -- Need provider/model — run wizard (session already set)
      local wizard = require("mucli.wizard")
      wizard._pick_provider(config.opts.session, true, function()
        _set_yolo()
        _finish_init()
      end)
    end
  end
end

--- Set yolo mode on the active session via variables endpoint.
function _set_yolo()
  local session_name = config.opts.session
  local c = require("mucli.client")
  c.post_sync("/api/sessions/" .. session_name .. "/variables", { key = "yolo", value = true })
end

--- Check if plugin is initialized
--- @return boolean
function M.is_initialized()
  return M._initialized
end

--- Cleanup on plugin unload — stop SSE, close panel
function M.cleanup()
  if M._sse_started then
    client.stop_sse()
    M._sse_started = false
  end
  local ok, panel = pcall(require, "mucli.chat.panel")
  if ok and panel.is_open() then
    panel.close()
  end
end

return M