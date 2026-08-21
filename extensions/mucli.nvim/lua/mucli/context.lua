local M = { items = {}, last_selection = nil }

local config = require("mucli.config")
local util = require("mucli.util")

local function editor_window()
  local ok, panel = pcall(require, "mucli.chat.panel")
  if ok and panel.editor_window then return panel.editor_window() end
  return vim.api.nvim_get_current_win()
end

local function editor_buffer()
  local win = editor_window()
  return win and vim.api.nvim_win_is_valid(win) and vim.api.nvim_win_get_buf(win) or vim.api.nvim_get_current_buf()
end

local function emit()
  local ok, panel = pcall(require, "mucli.chat.panel")
  if ok and panel.refresh then panel.refresh() end
end

function M.capture_selection(start_line, end_line, buf)
  buf = buf or editor_buffer()
  if not vim.api.nvim_buf_is_valid(buf) then return nil end
  local first, last
  local start_col, end_col = 1, nil
  if start_line and end_line then
    first, last = start_line, end_line
  else
    local a, b = vim.fn.getpos("'<"), vim.fn.getpos("'>")
    if a[2] == 0 or b[2] == 0 then return nil end
    first, last = a[2], b[2]
    start_col, end_col = a[3], b[3]
    if first > last or (first == last and start_col > end_col) then
      first, last, start_col, end_col = last, first, end_col, start_col
    end
  end
  local path = vim.api.nvim_buf_get_name(buf)
  if not config.get().workspace.allow_secret_paths and util.is_secret_path(path) then
    return nil
  end
  local lines = vim.api.nvim_buf_get_lines(buf, first - 1, last, false)
  if #lines == 0 then return nil end
  if not start_line then
    lines[1] = lines[1]:sub(math.max(1, start_col))
    if #lines == 1 then
      local width = math.max(0, (end_col or #lines[1]) - start_col + 1)
      lines[1] = lines[1]:sub(1, width)
    elseif end_col and end_col < 2147483647 then
      lines[#lines] = lines[#lines]:sub(1, end_col)
    end
  end
  return {
    type = "selection", path = path, relative_path = util.relative(path),
    start_line = first, end_line = last, content = table.concat(lines, "\n"),
    filetype = vim.bo[buf].filetype, changedtick = vim.api.nvim_buf_get_changedtick(buf),
  }
end

local function put(item)
  local identity = table.concat({ item.type or "", item.path or "", item.start_line or "", item.end_line or "" }, ":")
  item.id = vim.fn.sha256(identity):sub(1, 12)
  if item.type == "selection" then M.last_selection = vim.deepcopy(item) end
  for index, current in ipairs(M.items) do
    if current.id == item.id then M.items[index] = item; emit(); return item end
  end
  M.items[#M.items + 1] = item
  emit()
  return item
end

function M.stage(item)
  if type(item) ~= "table" then return nil end
  return put(vim.deepcopy(item))
end

function M.latest_selection()
  for index = #M.items, 1, -1 do
    if M.items[index].type == "selection" then return vim.deepcopy(M.items[index]) end
  end
  if M.last_selection then return vim.deepcopy(M.last_selection) end
  return M.capture_selection()
end

function M.add_selection(start_line, end_line, buf)
  local selection = M.capture_selection(start_line, end_line, buf)
  if not selection or selection.content == "" then
    util.notify("No visual selection available", vim.log.levels.WARN)
    return nil
  end
  put(selection)
  util.notify(("Added %s:%d-%d to context"):format(selection.relative_path, selection.start_line, selection.end_line))
  return selection
end

function M.add_file(buf)
  buf = buf or editor_buffer()
  local path = vim.api.nvim_buf_get_name(buf)
  if path == "" or vim.bo[buf].buftype ~= "" then
    util.notify("The active editor buffer is not a file", vim.log.levels.WARN)
    return nil
  end
  if not config.get().workspace.allow_secret_paths and util.is_secret_path(path) then
    util.notify("Secret-path policy blocked this file from editor context", vim.log.levels.ERROR)
    return nil
  end
  local content, truncated = util.truncate(util.buffer_text(buf), config.get().context.max_file_chars)
  local item = put({
    type = "file", path = path, relative_path = util.relative(path), content = content,
    filetype = vim.bo[buf].filetype, changedtick = vim.api.nvim_buf_get_changedtick(buf),
    modified = vim.bo[buf].modified, truncated = truncated,
  })
  util.notify("Added " .. item.relative_path .. " to context")
  return item
end

function M.add_diagnostics(buf)
  buf = buf or editor_buffer()
  local diagnostics = vim.diagnostic.get(buf)
  local path = vim.api.nvim_buf_get_name(buf)
  if not config.get().workspace.allow_secret_paths and util.is_secret_path(path) then
    util.notify("Secret-path policy blocked this file from editor context", vim.log.levels.ERROR)
    return nil
  end
  if #diagnostics == 0 then util.notify("No diagnostics in the active buffer"); return nil end
  local lines = {}
  for _, diagnostic in ipairs(diagnostics) do
    lines[#lines + 1] = ("L%d:%d [%s] %s"):format(
      diagnostic.lnum + 1, (diagnostic.col or 0) + 1,
      diagnostic.source or "diagnostic", tostring(diagnostic.message):gsub("\n", " ")
    )
  end
  local item = put({
    type = "diagnostics", path = path, relative_path = util.relative(path),
    content = table.concat(lines, "\n"), count = #diagnostics,
  })
  util.notify(("Added %d diagnostics to context"):format(#diagnostics))
  return item
end

function M.clear()
  M.items = {}
  M.last_selection = nil
  emit()
  util.notify("Cleared staged context")
end

function M.remove(id)
  for index, item in ipairs(M.items) do
    if item.id == id then table.remove(M.items, index); emit(); return item end
  end
end

function M.consume(ids)
  local wanted = {}
  for _, id in ipairs(ids or {}) do wanted[id] = true end
  if next(wanted) == nil then return end
  local kept = {}
  for _, item in ipairs(M.items) do
    if not wanted[item.id] then kept[#kept + 1] = item end
  end
  M.items = kept
  emit()
end

function M.summary()
  local values = {}
  for _, item in ipairs(M.items) do
    local label = item.relative_path or item.type
    if item.type == "selection" then label = label .. (":%d-%d"):format(item.start_line, item.end_line) end
    values[#values + 1] = { id = item.id, type = item.type, label = label }
  end
  return values
end

function M.labels()
  local values = {}
  for _, item in ipairs(M.summary()) do values[#values + 1] = item.label end
  return values
end

local function render_item(item)
  local header = ("### %s `%s`"):format(item.type:gsub("^%l", string.upper), item.relative_path or "")
  if item.start_line then header = header .. (" (lines %d-%d)"):format(item.start_line, item.end_line) end
  if item.changedtick then header = header .. (" · changedtick %d%s"):format(item.changedtick, item.modified and " · unsaved" or "") end
  if item.type == "diagnostics" then return header .. "\n" .. item.content end
  return header .. "\n" .. util.code_fence(item.content, item.filetype)
end

local function automatic_items()
  if not config.get().context.automatic then return {} end
  local buf = editor_buffer()
  if not vim.api.nvim_buf_is_valid(buf) or vim.bo[buf].buftype ~= "" then return {} end
  local path = vim.api.nvim_buf_get_name(buf)
  if path == "" then return {} end
  local workspace = config.get().workspace
  if not workspace.allow_secret_paths and util.is_secret_path(path) then return {} end
  if not workspace.allow_outside and not util.is_within(path, util.workspace_root()) then
    return {}
  end
  local win = editor_window()
  local cursor = vim.api.nvim_win_get_cursor(win)
  local radius = math.floor(config.get().context.cursor_lines / 2)
  local count = vim.api.nvim_buf_line_count(buf)
  local first = math.max(1, cursor[1] - radius)
  local last = math.min(count, cursor[1] + radius)
  local content = table.concat(vim.api.nvim_buf_get_lines(buf, first - 1, last, false), "\n")
  local items = {{
    type = "active buffer", path = path, relative_path = util.relative(path),
    start_line = first, end_line = last, content = content, filetype = vim.bo[buf].filetype,
    changedtick = vim.api.nvim_buf_get_changedtick(buf), modified = vim.bo[buf].modified,
  }}
  if config.get().context.include_diagnostics then
    local diagnostics = vim.diagnostic.get(buf)
    if #diagnostics > 0 then
      local values = {}
      for _, d in ipairs(diagnostics) do
        values[#values + 1] = ("L%d:%d [%s] %s"):format(d.lnum + 1, (d.col or 0) + 1, d.source or "diagnostic", tostring(d.message):gsub("\n", " "))
      end
      items[#items + 1] = { type = "diagnostics", relative_path = util.relative(path), content = table.concat(values, "\n") }
    end
  end
  return items, { path = util.relative(path), line = cursor[1], column = cursor[2] }
end

function M.compose(prompt)
  local blocks = {}
  local staged_ids = {}
  local root = util.workspace_root()
  blocks[#blocks + 1] = "## MUCLI editor context"
  blocks[#blocks + 1] = "Workspace: `" .. root .. "`"
  blocks[#blocks + 1] = "Editor buffer snapshots may be unsaved and override filesystem content."
  for _, item in ipairs(M.items) do
    local allowed = config.get().workspace.allow_secret_paths
      or not item.path or not util.is_secret_path(item.path)
    if allowed then
      blocks[#blocks + 1] = render_item(item)
      staged_ids[#staged_ids + 1] = item.id
    end
  end
  local automatic, cursor = automatic_items()
  for _, item in ipairs(automatic) do blocks[#blocks + 1] = render_item(item) end
  if config.get().context.include_open_buffers then
    local open = {}
    for _, buf in ipairs(vim.api.nvim_list_bufs()) do
      local path = vim.api.nvim_buf_get_name(buf)
      local allowed = config.get().workspace.allow_outside
        or util.is_within(path, root)
      allowed = allowed and (config.get().workspace.allow_secret_paths
        or not util.is_secret_path(path))
      if vim.api.nvim_buf_is_loaded(buf) and path ~= ""
        and vim.bo[buf].buftype == "" and allowed then
        open[#open + 1] = ("- `%s`%s · changedtick %d"):format(util.relative(path), vim.bo[buf].modified and " (unsaved)" or "", vim.api.nvim_buf_get_changedtick(buf))
      end
    end
    if #open > 0 then blocks[#blocks + 1] = "### Open buffers\n" .. table.concat(open, "\n") end
  end
  local context_text = table.concat(blocks, "\n\n")
  context_text = util.truncate(context_text, config.get().context.max_chars)
  local wire = tostring(prompt or "") .. "\n\n" .. context_text
  local staged_count = #staged_ids
  return wire, {
    root = root, cursor = cursor, staged_count = staged_count,
    staged_ids = staged_ids,
  }
end

function M.picker()
  local choices = {
    { label = "Visual selection", action = M.add_selection },
    { label = "Active file", action = M.add_file },
    { label = "Diagnostics", action = M.add_diagnostics },
    { label = "Clear staged context", action = M.clear },
  }
  vim.ui.select(choices, { prompt = "MUCLI context", format_item = function(item) return item.label end }, function(choice)
    if choice then choice.action() end
  end)
end

return M
