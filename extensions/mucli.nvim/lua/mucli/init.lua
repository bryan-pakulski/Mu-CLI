local M = { initialized = false, connecting = false }

local config = require("mucli.config")
local store = require("mucli.store")
local util = require("mucli.util")

local function highlights()
  local groups = {
    MucliTitle = { link = "Title" },
    MucliUser = { link = "DiagnosticInfo" },
    MucliAssistant = { link = "DiagnosticOk" },
    MucliWorking = { link = "DiagnosticWarn" },
    MucliDiff = { link = "DiffChange" },
    MucliGhostText = { link = "Comment" },
  }
  for name, value in pairs(groups) do vim.api.nvim_set_hl(0, name, vim.tbl_extend("force", { default = true }, value)) end
end

local function map(mode, lhs, rhs, desc)
  if not lhs or lhs == "" or lhs == false then return end
  vim.keymap.set(mode, lhs, rhs, { silent = true, desc = desc })
end

local function keymaps()
  local keys = config.get().keymaps
  map("n", keys.toggle, function() M.with_ready(require("mucli.chat.panel").toggle) end, "MUCLI: toggle editor")
  map("n", keys.ask, function() M.with_ready(require("mucli.conversation").ask) end, "MUCLI: ask")
  map("n", keys.actions, function() M.with_ready(require("mucli.actions").open) end, "MUCLI: code actions")
  map("v", keys.actions, function()
    M.with_ready(require("mucli.actions").open_visual)
  end, "MUCLI: selection actions")
  map("v", keys.add_selection, function()
    require("mucli.context").add_selection()
  end, "MUCLI: add selection context")
  map("n", keys.add_file, require("mucli.context").add_file, "MUCLI: add file context")
  map("n", keys.hints, function() M.with_ready(require("mucli.hints").analyze) end, "MUCLI: analyze hints")
  map({ "n", "i" }, keys.complete, function() M.with_ready(require("mucli.completion").request) end, "MUCLI: inline completion")
  map({ "n", "i" }, keys.accept_completion, require("mucli.completion").accept, "MUCLI: accept completion")
  map({ "n", "i" }, keys.dismiss_completion, require("mucli.completion").clear, "MUCLI: dismiss completion")
  map("n", keys.interrupt, require("mucli.chat.input").interrupt, "MUCLI: interrupt")
  map("n", keys.next_hint, require("mucli.hints").next, "MUCLI: next hint")
  map("n", keys.prev_hint, require("mucli.hints").previous, "MUCLI: previous hint")
end

local function autocmds()
  local group = vim.api.nvim_create_augroup("Mucli", { clear = true })
  vim.api.nvim_create_autocmd("ColorScheme", { group = group, callback = highlights })
  vim.api.nvim_create_autocmd("VimResized", { group = group, callback = function() require("mucli.chat.panel").resize() end })
  vim.api.nvim_create_autocmd("VimLeavePre", { group = group, callback = M.cleanup })
end

function M.reconnect(callback)
  if M.connecting then if callback then callback(false, "connection already in progress") end; return end
  M.connecting = true
  store.state.ready = false
  local session = require("mucli.session")
  local previous = session.registered_session
  session.configure_identity()
  if previous and previous ~= config.get().session then session.unregister(previous) end
  require("mucli.events").start()
  session.ensure(function(info, err)
    if not info then
      M.connecting = false
      store.set_connection("disconnected", err)
      if callback then callback(false, err) end
      return
    end
    session.register(function(_, response)
      M.connecting = false
      if not response.ok then
        store.set_connection("disconnected", response.error)
        if callback then callback(false, response.error) end
        return
      end
      session.set_variable("yolo", config.get().yolo == true, function() end)
      session.set_variable(
        "security_allow_secret_paths",
        config.get().workspace.allow_secret_paths == true,
        function() end
      )
      session.load_history()
      store.state.ready = true
      store.touch()
      vim.api.nvim_exec_autocmds("User", { pattern = "MucliReady", modeline = false })
      if callback then callback(true) end
    end)
  end)
end

function M.with_ready(action)
  if store.state.ready then action(); return end
  M.reconnect(function(success, err)
    if success then action()
    elseif err == "needs_configuration" then require("mucli.wizard").start()
    else util.notify("Cannot connect: " .. tostring(err), vim.log.levels.ERROR) end
  end)
end

function M.setup(opts)
  if vim.fn.has("nvim-0.10") ~= 1 then error("mucli.nvim requires Neovim 0.10+") end
  config.setup(opts)
  require("mucli.session").configure_identity()
  highlights()
  keymaps()
  autocmds()
  require("mucli.completion").setup()
  M.initialized = true
  if config.get().auto_connect then
    vim.schedule(function()
      M.reconnect(function(success, err)
        if not success and err ~= "needs_configuration" then
          store.set_connection("disconnected", err)
        end
      end)
    end)
  end
  return M
end

function M.ensure_setup()
  if not M.initialized then M.setup({}) end
  return M
end

function M.is_initialized() return M.initialized end

function M.statusline()
  local icon = store.state.connection == "connected" and "●" or "○"
  local busy = store.state.busy and " working" or ""
  return ("MUCLI %s%s"):format(icon, busy)
end

function M.cleanup()
  if not M.initialized then return end
  require("mucli.session").unregister()
  require("mucli.events").stop()
  require("mucli.completion").clear()
  require("mucli.chat.panel").cleanup()
  M.initialized = false
end

return M
