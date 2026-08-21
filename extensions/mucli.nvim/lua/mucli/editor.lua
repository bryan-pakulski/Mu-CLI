local M = {
  last_win = nil,
  by_tab = {},
  views = {},
  listeners = {},
  configured = false,
  last_mode = "n",
}

local function valid_win(win)
  return win and vim.api.nvim_win_is_valid(win)
end

function M.is_plugin_buffer(buf)
  if not buf or not vim.api.nvim_buf_is_valid(buf) then return true end
  local name = vim.api.nvim_buf_get_name(buf)
  return name:match("^mucli://") ~= nil
end

function M.is_editor_window(win)
  if not valid_win(win) then return false end
  local buf = vim.api.nvim_win_get_buf(win)
  return not M.is_plugin_buffer(buf) and vim.bo[buf].buftype == ""
end

local function capture_view(win)
  if not M.is_editor_window(win) then return nil end
  local ok, value = pcall(vim.api.nvim_win_call, win, function()
    local cursor = vim.api.nvim_win_get_cursor(0)
    return {
      top_line = math.max(1, vim.fn.line("w0")),
      bottom_line = math.max(1, vim.fn.line("w$")),
      cursor_line = cursor[1],
      cursor_column = cursor[2],
      left_column = (vim.fn.winsaveview() or {}).leftcol or 0,
    }
  end)
  return ok and value or nil
end

local function view_key(value)
  if not value then return "" end
  return table.concat({
    value.top_line or 0, value.bottom_line or 0,
    value.cursor_line or 0, value.cursor_column or 0,
    value.left_column or 0,
  }, ":")
end

local function emit()
  for _, listener in pairs(M.listeners) do
    vim.schedule(function() pcall(listener) end)
  end
end

function M.subscribe(listener)
  local id = tostring(listener) .. tostring((vim.uv or vim.loop).hrtime())
  M.listeners[id] = listener
  return function() M.listeners[id] = nil end
end

function M.track(win)
  win = win or vim.api.nvim_get_current_win()
  if not M.is_editor_window(win) then return false end
  local tab = vim.api.nvim_win_get_tabpage(win)
  local before_win = M.last_win
  local before_view = view_key(M.views[win])
  M.last_win = win
  M.by_tab[tab] = win
  M.views[win] = capture_view(win)
  if win == vim.api.nvim_get_current_win() then
    M.last_mode = (vim.api.nvim_get_mode() or {}).mode or M.last_mode
  end
  if before_win ~= win or before_view ~= view_key(M.views[win]) then emit() end
  return true
end

function M.window()
  local current = vim.api.nvim_get_current_win()
  if M.is_editor_window(current) then
    M.track(current)
    return current
  end

  local tab = vim.api.nvim_get_current_tabpage()
  local candidate = M.by_tab[tab]
  if M.is_editor_window(candidate) then return candidate end

  for _, win in ipairs(vim.api.nvim_tabpage_list_wins(tab)) do
    if M.is_editor_window(win) then
      M.track(win)
      return win
    end
  end
  return nil
end

function M.view(win)
  win = win or M.window()
  if not M.is_editor_window(win) then return nil end
  local value = capture_view(win)
  if value then M.views[win] = value end
  return value
end

function M.snapshot()
  local win = M.window()
  if not win then return nil end
  local buf = vim.api.nvim_win_get_buf(win)
  local view = M.view(win)
  if not view then return nil end
  local count = vim.api.nvim_buf_line_count(buf)
  local first = math.max(1, math.min(view.top_line, count))
  local last = math.max(first, math.min(view.bottom_line, count))
  return {
    win = win,
    buf = buf,
    path = vim.api.nvim_buf_get_name(buf),
    filetype = vim.bo[buf].filetype,
    modified = vim.bo[buf].modified,
    changedtick = vim.api.nvim_buf_get_changedtick(buf),
    mode = M.last_mode,
    cursor = { line = view.cursor_line, column = view.cursor_column },
    viewport = {
      start_line = first,
      end_line = last,
      content = table.concat(
        vim.api.nvim_buf_get_lines(buf, first - 1, last, false), "\n"
      ),
    },
  }
end

function M.describe()
  local snapshot = M.snapshot()
  if not snapshot then return nil end
  snapshot.viewport.content = nil
  return snapshot
end

local function refresh_surfaces()
  local ok_panel, panel = pcall(require, "mucli.chat.panel")
  if ok_panel and panel.refresh then panel.refresh() end
  local ok_context, context_panel = pcall(require, "mucli.context_panel")
  if ok_context and context_panel.refresh then context_panel.refresh() end
end

function M.setup()
  if M.configured then return end
  M.configured = true
  local group = vim.api.nvim_create_augroup("MucliEditorState", { clear = true })
  vim.api.nvim_create_autocmd({ "WinEnter", "BufEnter", "WinLeave", "BufLeave", "WinScrolled" }, {
    group = group,
    callback = function()
      M.track(vim.api.nvim_get_current_win())
      vim.schedule(refresh_surfaces)
    end,
  })
  M.track(vim.api.nvim_get_current_win())
end

function M.reset()
  M.last_win = nil
  M.by_tab = {}
  M.views = {}
  M.last_mode = "n"
end

return M
