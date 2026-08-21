local M = {}

local config = require("mucli.config")

-- Module state
local state = {
  bufnr = nil,
  winid = nil,
}

--- Create scratch buffer for chat panel
--- @return integer bufnr
local function create_buf()
  local buf = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_set_option_value("buftype", "nofile", { buf = buf })
  vim.api.nvim_set_option_value("swapfile", false, { buf = buf })
  vim.api.nvim_set_option_value("filetype", "mucli-chat", { buf = buf })
  vim.api.nvim_set_option_value("bufhidden", "hide", { buf = buf })
  vim.api.nvim_set_option_value("modifiable", true, { buf = buf })
  return buf
end

--- Set window-local options on panel window
--- @param winid integer
local function set_win_options(winid)
  vim.api.nvim_set_option_value("wrap", true, { win = winid })
  vim.api.nvim_set_option_value("cursorline", true, { win = winid })
  vim.api.nvim_set_option_value("number", false, { win = winid })
  vim.api.nvim_set_option_value("relativenumber", false, { win = winid })
  vim.api.nvim_set_option_value("signcolumn", "no", { win = winid })
end

--- Open panel — vertical split with configured width/position
local function open_panel()
  local win_config = config.opts.window or {}
  local width = win_config.width or 60
  local position = win_config.position or "right"

  local cmd
  if position == "left" then
    cmd = "topleft " .. width .. "vsplit"
  else
    cmd = "botright " .. width .. "vsplit"
  end
  vim.cmd(cmd)

  state.winid = vim.api.nvim_get_current_win()

  -- Create or reuse buffer
  if state.bufnr and vim.api.nvim_buf_is_valid(state.bufnr) then
    vim.api.nvim_win_set_buf(state.winid, state.bufnr)
  else
    state.bufnr = create_buf()
    vim.api.nvim_win_set_buf(state.winid, state.bufnr)
  end

  -- Set window-local options (wrap, cursorline, etc.)
  set_win_options(state.winid)

  -- Set up input keymaps on buffer
  local ok, input = pcall(require, "mucli.chat.input")
  if ok then
    input.setup_input_keymaps(state.bufnr)
  end

  -- Initialize buffer content (SSE already started by init.lua)
  local ok2, buffer = pcall(require, "mucli.chat.buffer")
  if ok2 and buffer.init_buffer then
    buffer.init_buffer(state.bufnr)
  end
end

--- Close panel window, preserve buffer
local function close_panel()
  if state.winid and vim.api.nvim_win_is_valid(state.winid) then
    vim.api.nvim_win_close(state.winid, false)
  end
  state.winid = nil
end

--- Toggle panel open/close
function M.toggle()
  if M.is_open() then
    close_panel()
  else
    open_panel()
  end
end

--- Close panel window
function M.close()
  close_panel()
end

--- Check if panel is open
--- @return boolean
function M.is_open()
  return state.winid ~= nil and vim.api.nvim_win_is_valid(state.winid)
end

--- Get chat buffer id
--- @return integer|nil
function M.get_buf()
  if state.bufnr and vim.api.nvim_buf_is_valid(state.bufnr) then
    return state.bufnr
  end
  return nil
end

--- Get window id
--- @return integer|nil
function M.get_win()
  return state.winid
end

return M