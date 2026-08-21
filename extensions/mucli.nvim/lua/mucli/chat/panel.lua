local M = {}

local config = require("mucli.config")
local render = require("mucli.chat.render")
local store = require("mucli.store")

local state = {
  chat_buf = nil, input_buf = nil, chat_win = nil, input_win = nil,
  editor_win = nil, unsubscribe = nil,
}

local function valid_win(win) return win and vim.api.nvim_win_is_valid(win) end
local function valid_buf(buf) return buf and vim.api.nvim_buf_is_valid(buf) end

local function plugin_buffer(buf)
  if not valid_buf(buf) then return false end
  local name = vim.api.nvim_buf_get_name(buf)
  return name:match("^mucli://") ~= nil
end

local function scratch(name, filetype, modifiable)
  local buf = vim.api.nvim_create_buf(false, true)
  pcall(vim.api.nvim_buf_set_name, buf, name)
  vim.bo[buf].buftype = "nofile"
  vim.bo[buf].bufhidden = "hide"
  vim.bo[buf].swapfile = false
  vim.bo[buf].modeline = false
  vim.bo[buf].filetype = filetype
  vim.bo[buf].modifiable = modifiable
  return buf
end

function M.editor_window()
  if valid_win(state.editor_win) and not plugin_buffer(vim.api.nvim_win_get_buf(state.editor_win)) then return state.editor_win end
  local current = vim.api.nvim_get_current_win()
  if not plugin_buffer(vim.api.nvim_win_get_buf(current)) then state.editor_win = current; return current end
  for _, win in ipairs(vim.api.nvim_tabpage_list_wins(0)) do
    if not plugin_buffer(vim.api.nvim_win_get_buf(win)) then state.editor_win = win; return win end
  end
  return nil
end

local function width()
  local opts = config.get().window
  local available = math.max(20, vim.o.columns - 20)
  local minimum = math.min(opts.min_width, available)
  return math.max(minimum, math.min(opts.width, opts.max_width, available))
end

local function win_options(win, input)
  vim.wo[win].number = false
  vim.wo[win].relativenumber = false
  vim.wo[win].signcolumn = "no"
  vim.wo[win].foldcolumn = "0"
  vim.wo[win].wrap = not input
  vim.wo[win].linebreak = not input
  vim.wo[win].cursorline = true
  vim.wo[win].winfixwidth = true
  vim.wo[win].statuscolumn = ""
  if not input then vim.wo[win].conceallevel = 2 end
end

local function status_icon()
  return ({ connected = "●", connecting = "◌", disconnected = "○" })[store.state.connection] or "○"
end

function M.refresh()
  if valid_buf(state.chat_buf) then render.render(state.chat_buf, state.chat_win) end
  if valid_win(state.chat_win) then
    local identity = store.state.session or "not configured"
    local model = store.state.model and (" · " .. store.state.model) or ""
    vim.wo[state.chat_win].winbar = (" MUCLI  %s %s%s"):format(status_icon(), identity, model)
  end
  if valid_win(state.input_win) then
    local labels = require("mucli.context").labels()
    local context = #labels > 0 and ("context: " .. table.concat(labels, " · ")) or "automatic context"
    vim.wo[state.input_win].winbar = " COMPOSE  " .. context .. "  ·  C-s send  C-a context  C-c stop"
  end
end

local function ensure_subscription()
  if state.unsubscribe then return end
  state.unsubscribe = store.subscribe(function() M.refresh() end)
end

local function watch_windows()
  local group = vim.api.nvim_create_augroup("MucliPanelWindows", { clear = true })
  for _, win in ipairs({ state.chat_win, state.input_win }) do
    vim.api.nvim_create_autocmd("WinClosed", {
      group = group,
      pattern = tostring(win),
      callback = function()
        vim.schedule(function()
          if state.chat_win and state.input_win and not M.is_open() then M.close() end
        end)
      end,
    })
  end
end

function M.open(focus)
  if M.is_open() then
    if focus ~= false and valid_win(state.input_win) then
      vim.api.nvim_set_current_win(state.input_win)
      vim.cmd("startinsert")
    end
    return
  end
  local current = vim.api.nvim_get_current_win()
  if not plugin_buffer(vim.api.nvim_win_get_buf(current)) then state.editor_win = current end
  local opts = config.get().window
  local command = opts.position == "left" and ("topleft %dvnew"):format(width()) or ("botright %dvnew"):format(width())
  vim.cmd(command)
  state.chat_win = vim.api.nvim_get_current_win()
  if not valid_buf(state.chat_buf) then state.chat_buf = scratch("mucli://chat", "markdown", false) end
  vim.api.nvim_win_set_buf(state.chat_win, state.chat_buf)
  win_options(state.chat_win, false)

  local input_height = math.min(opts.input_height, math.max(3, vim.o.lines - 8))
  vim.cmd(("belowright %dnew"):format(input_height))
  state.input_win = vim.api.nvim_get_current_win()
  if not valid_buf(state.input_buf) then state.input_buf = scratch("mucli://composer", "markdown", true) end
  vim.api.nvim_win_set_buf(state.input_win, state.input_buf)
  win_options(state.input_win, true)
  require("mucli.chat.input").setup(state.input_buf)
  watch_windows()
  vim.keymap.set("n", "q", M.close, { buffer = state.chat_buf, silent = true, desc = "Close MUCLI" })
  vim.keymap.set("n", "i", M.focus_input, { buffer = state.chat_buf, silent = true, desc = "Focus MUCLI composer" })
  vim.keymap.set("n", "<CR>", function()
    if not render.activate(state.chat_buf, vim.api.nvim_win_get_cursor(0)[1]) then M.focus_input() end
  end, { buffer = state.chat_buf, silent = true, desc = "Open MUCLI item or composer" })
  ensure_subscription()
  M.refresh()
  if focus == false and valid_win(state.editor_win) then vim.api.nvim_set_current_win(state.editor_win)
  else M.focus_input() end
end

function M.close()
  if valid_win(state.input_win) then pcall(vim.api.nvim_win_close, state.input_win, true) end
  if valid_win(state.chat_win) then pcall(vim.api.nvim_win_close, state.chat_win, true) end
  state.input_win, state.chat_win = nil, nil
  if valid_win(state.editor_win) then pcall(vim.api.nvim_set_current_win, state.editor_win) end
end

function M.toggle()
  if M.is_open() then M.close() else M.open(true) end
end

function M.focus_input()
  if not M.is_open() then M.open(true); return end
  if valid_win(state.input_win) then
    vim.api.nvim_set_current_win(state.input_win)
    local count = vim.api.nvim_buf_line_count(state.input_buf)
    vim.api.nvim_win_set_cursor(state.input_win, { math.max(1, count), 0 })
    vim.cmd("startinsert")
  end
end

function M.resize()
  if not M.is_open() then return end
  local target_width = width()
  local target_height = math.min(config.get().window.input_height, math.max(3, vim.o.lines - 8))
  pcall(vim.api.nvim_win_set_width, state.chat_win, target_width)
  pcall(vim.api.nvim_win_set_width, state.input_win, target_width)
  pcall(vim.api.nvim_win_set_height, state.input_win, target_height)
  M.refresh()
end

function M.is_open() return not not (valid_win(state.chat_win) and valid_win(state.input_win)) end
function M.get_buf() return valid_buf(state.chat_buf) and state.chat_buf or nil end
function M.get_input_buf() return valid_buf(state.input_buf) and state.input_buf or nil end
function M.get_win() return valid_win(state.chat_win) and state.chat_win or nil end

function M.cleanup()
  M.close()
  if state.unsubscribe then state.unsubscribe(); state.unsubscribe = nil end
end

return M
