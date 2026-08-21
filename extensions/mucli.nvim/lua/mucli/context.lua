local M = {
  turn_items = {},
  pinned_items = {},
  namespace = vim.api.nvim_create_namespace("mucli_context_anchors"),
  sequence = 0,
}

local config = require("mucli.config")
local editor = require("mucli.editor")
local util = require("mucli.util")

local function emit()
  local ok_panel, panel = pcall(require, "mucli.chat.panel")
  if ok_panel and panel.refresh then panel.refresh() end
  local ok_context, context_panel = pcall(require, "mucli.context_panel")
  if ok_context and context_panel.refresh then context_panel.refresh() end
end

local function editor_buffer()
  local win = editor.window()
  return win and vim.api.nvim_win_is_valid(win)
      and vim.api.nvim_win_get_buf(win)
    or vim.api.nvim_get_current_buf()
end

local function path_allowed(path)
  local workspace = config.get().workspace
  if not path or path == "" then return false end
  if not workspace.allow_secret_paths and util.is_secret_path(path) then return false end
  return workspace.allow_outside or util.is_within(path, util.workspace_root())
end

local function clamp_column(buf, row, column)
  local line = vim.api.nvim_buf_get_lines(buf, row, row + 1, false)[1] or ""
  return util.clamp(tonumber(column) or 0, 0, #line)
end

local function inclusive_end_column(buf, row, column)
  local line = vim.api.nvim_buf_get_lines(buf, row, row + 1, false)[1] or ""
  column = util.clamp(tonumber(column) or 0, 0, #line)
  if column >= #line then return #line end
  local character = vim.fn.strcharpart(line:sub(column + 1), 0, 1)
  return math.min(#line, column + math.max(1, #character))
end

local function extract_text(buf, range, selection_mode)
  if not range or not vim.api.nvim_buf_is_valid(buf) then return "" end
  local line_count = vim.api.nvim_buf_line_count(buf)
  local start_row = util.clamp(range.start_row or 0, 0, math.max(0, line_count - 1))
  local end_row = util.clamp(range.end_row or start_row, start_row, math.max(start_row, line_count - 1))
  local start_col = clamp_column(buf, start_row, range.start_col or 0)
  local end_col = clamp_column(buf, end_row, range.end_col or 0)
  if selection_mode == "block" then
    local values = {}
    local left, right = math.min(start_col, end_col), math.max(start_col, end_col)
    for row = start_row, end_row do
      local line = vim.api.nvim_buf_get_lines(buf, row, row + 1, false)[1] or ""
      values[#values + 1] = line:sub(left + 1, math.min(#line, right))
    end
    return table.concat(values, "\n")
  end
  local ok, values = pcall(
    vim.api.nvim_buf_get_text,
    buf,
    start_row,
    start_col,
    end_row,
    end_col,
    {}
  )
  return ok and table.concat(values, "\n") or ""
end

local function selection_positions()
  local mode = vim.fn.mode()
  local visual = mode == "v" or mode == "V" or mode == "\22"
  local a = visual and vim.fn.getpos("v") or vim.fn.getpos("'<")
  local b = visual and vim.fn.getpos(".") or vim.fn.getpos("'>")
  local selection_mode = visual and mode or vim.fn.visualmode()
  if a[2] == 0 or b[2] == 0 then return nil end
  return a, b, selection_mode
end

function M.capture_selection(start_line, end_line, buf)
  local first, last, start_col, end_col, selection_mode
  if start_line and end_line then
    buf = buf or editor_buffer()
    first, last = start_line, end_line
    selection_mode = "line"
  else
    local a, b, mode = selection_positions()
    if not a then return nil end
    if not buf then
      local mark_buf = tonumber(a[1]) or 0
      buf = mark_buf > 0 and mark_buf or editor_buffer()
    end
    first, last = a[2], b[2]
    start_col, end_col = math.max(0, a[3] - 1), math.max(0, b[3] - 1)
    if first > last or (first == last and start_col > end_col) then
      first, last, start_col, end_col = last, first, end_col, start_col
    end
    selection_mode = mode == "V" and "line" or mode == "\22" and "block" or "character"
  end
  if not buf or not vim.api.nvim_buf_is_valid(buf) then return nil end
  local count = vim.api.nvim_buf_line_count(buf)
  first = util.clamp(tonumber(first) or 1, 1, math.max(1, count))
  last = util.clamp(tonumber(last) or first, first, math.max(first, count))
  local start_row, end_row = first - 1, last - 1
  if selection_mode == "line" then
    start_col = 0
    end_col = #(vim.api.nvim_buf_get_lines(buf, end_row, end_row + 1, false)[1] or "")
  else
    start_col = clamp_column(buf, start_row, start_col or 0)
    end_col = selection_mode == "character"
        and inclusive_end_column(buf, end_row, end_col or 0)
      or clamp_column(buf, end_row, (end_col or 0) + 1)
  end
  local path = vim.api.nvim_buf_get_name(buf)
  if not path_allowed(path) then return nil end
  local range = {
    start_row = start_row,
    start_col = start_col,
    end_row = end_row,
    end_col = end_col,
  }
  local content = extract_text(buf, range, selection_mode)
  if content == "" then return nil end
  local tick = vim.api.nvim_buf_get_changedtick(buf)
  return {
    type = "selection",
    path = path,
    relative_path = util.relative(path),
    start_line = first,
    end_line = last,
    start_column = start_col,
    end_column = end_col,
    content = content,
    filetype = vim.bo[buf].filetype,
    captured_changedtick = tick,
    changedtick = tick,
    modified = vim.bo[buf].modified,
    selection_mode = selection_mode,
    _buf = buf,
    _range = range,
  }
end

local function delete_anchor(item)
  if item and item._buf and item._extmark and vim.api.nvim_buf_is_valid(item._buf) then
    pcall(vim.api.nvim_buf_del_extmark, item._buf, M.namespace, item._extmark)
  end
end

local function attach_anchor(item)
  local range, buf = item._range, item._buf
  if item.type ~= "selection" or not range or not buf
    or not vim.api.nvim_buf_is_valid(buf) then return end
  local ok, mark = pcall(vim.api.nvim_buf_set_extmark, buf, M.namespace, range.start_row, range.start_col, {
    end_row = range.end_row,
    end_col = range.end_col,
    right_gravity = false,
    end_right_gravity = true,
    strict = false,
  })
  if ok then item._extmark = mark end
end

local function collection(scope)
  return scope == "pinned" and M.pinned_items or M.turn_items
end

local function put(item, scope)
  scope = scope == "pinned" and "pinned" or "turn"
  item = vim.deepcopy(item)
  item.scope = scope
  M.sequence = M.sequence + 1
  item._sequence = M.sequence
  local identity_parts = { scope, item.type or "", item.path or "" }
  if item.type == "selection" then
    vim.list_extend(identity_parts, {
      item.start_line or "", item.end_line or "",
      item.start_column or "", item.end_column or "",
    })
  end
  local identity = table.concat(identity_parts, ":")
  item.id = item.id or vim.fn.sha256(identity):sub(1, 12)
  local values = collection(scope)
  for index, current in ipairs(values) do
    if current.id == item.id then
      delete_anchor(current)
      values[index] = item
      attach_anchor(item)
      emit()
      return item, false
    end
  end
  values[#values + 1] = item
  attach_anchor(item)
  emit()
  return item, true
end

function M.stage(item, scope)
  if type(item) ~= "table" then return nil end
  return put(item, scope or "turn")
end

local function resolve_item(item)
  local resolved = vim.deepcopy(item)
  local buf = item._buf
  if (not buf or not vim.api.nvim_buf_is_valid(buf)) and item.path then
    buf = util.find_buffer(item.path)
  end
  if item.type == "selection" and buf and vim.api.nvim_buf_is_valid(buf) then
    local range = item._range and vim.deepcopy(item._range) or nil
    if item._extmark then
      local mark = vim.api.nvim_buf_get_extmark_by_id(buf, M.namespace, item._extmark, { details = true })
      if mark and #mark >= 3 and mark[1] >= 0 then
        local details = mark[3] or {}
        range = {
          start_row = mark[1], start_col = mark[2],
          end_row = details.end_row or mark[1],
          end_col = details.end_col or mark[2],
        }
      end
    end
    if range then
      resolved._range = range
      resolved.start_line = range.start_row + 1
      resolved.end_line = range.end_row + 1
      resolved.start_column = range.start_col
      resolved.end_column = range.end_col
      resolved.content = extract_text(buf, range, item.selection_mode)
    end
    resolved.changedtick = vim.api.nvim_buf_get_changedtick(buf)
    resolved.modified = vim.bo[buf].modified
    resolved.stale = resolved.changedtick ~= (item.captured_changedtick or resolved.changedtick)
  elseif item.type == "file" and buf and vim.api.nvim_buf_is_valid(buf) then
    resolved.content, resolved.truncated = util.truncate(
      util.buffer_text(buf), config.get().context.max_file_chars
    )
    resolved.changedtick = vim.api.nvim_buf_get_changedtick(buf)
    resolved.modified = vim.bo[buf].modified
    resolved.stale = resolved.changedtick ~= (item.captured_changedtick or resolved.changedtick)
  end
  return resolved
end

local function serialise(item)
  local value = resolve_item(item)
  return {
    id = value.id, scope = value.scope, type = value.type,
    path = value.relative_path or util.relative(value.path),
    filetype = value.filetype, start_line = value.start_line,
    end_line = value.end_line, start_column = value.start_column,
    end_column = value.end_column, content = value.content or "",
    captured_changedtick = value.captured_changedtick,
    changedtick = value.changedtick, modified = value.modified == true,
    stale = value.stale == true, truncated = value.truncated == true,
  }
end

function M.add_selection(start_line, end_line, buf, opts)
  if type(buf) == "table" and opts == nil then opts, buf = buf, nil end
  opts = opts or {}
  local selection = M.capture_selection(start_line, end_line, buf)
  if not selection then
    util.notify("No current visual selection is available", vim.log.levels.WARN)
    return nil
  end
  local scope = opts.scope or "pinned"
  local item, added = put(selection, scope)
  util.notify(("%s %s:%d-%d · %d %s context item(s)"):format(
    added and (scope == "pinned" and "Pinned" or "Added") or "Refreshed",
    item.relative_path, item.start_line, item.end_line,
    #collection(scope), scope
  ))
  return item
end

function M.add_file(buf, opts)
  if type(buf) == "table" and opts == nil then opts, buf = buf, nil end
  opts = opts or {}
  buf = buf or editor_buffer()
  if not buf or not vim.api.nvim_buf_is_valid(buf) then return nil end
  local path = vim.api.nvim_buf_get_name(buf)
  if path == "" or vim.bo[buf].buftype ~= "" then
    util.notify("The active editor buffer is not a file", vim.log.levels.WARN)
    return nil
  end
  if not path_allowed(path) then
    util.notify("Workspace policy blocked this file from editor context", vim.log.levels.ERROR)
    return nil
  end
  local content, truncated = util.truncate(util.buffer_text(buf), config.get().context.max_file_chars)
  local tick = vim.api.nvim_buf_get_changedtick(buf)
  local scope = opts.scope or "pinned"
  local item = put({
    type = "file", path = path, relative_path = util.relative(path),
    start_line = 1, end_line = vim.api.nvim_buf_line_count(buf),
    content = content, filetype = vim.bo[buf].filetype,
    captured_changedtick = tick, changedtick = tick,
    modified = vim.bo[buf].modified, truncated = truncated, _buf = buf,
  }, scope)
  util.notify((scope == "pinned" and "Pinned " or "Added ") .. item.relative_path .. " as " .. scope .. " context")
  return item
end

function M.add_diagnostics(buf, opts)
  if type(buf) == "table" and opts == nil then opts, buf = buf, nil end
  opts = opts or {}
  buf = buf or editor_buffer()
  if not buf or not vim.api.nvim_buf_is_valid(buf) then return nil end
  local path = vim.api.nvim_buf_get_name(buf)
  if not path_allowed(path) then
    util.notify("Workspace policy blocked diagnostics for this file", vim.log.levels.ERROR)
    return nil
  end
  local diagnostics = vim.diagnostic.get(buf)
  if #diagnostics == 0 then util.notify("No diagnostics in the active buffer"); return nil end
  local lines = {}
  for _, diagnostic in ipairs(diagnostics) do
    lines[#lines + 1] = ("L%d:%d [%s] %s"):format(
      diagnostic.lnum + 1, (diagnostic.col or 0) + 1,
      diagnostic.source or "diagnostic",
      tostring(diagnostic.message):gsub("\n", " ")
    )
  end
  local scope = opts.scope or "pinned"
  local item = put({
    type = "diagnostics", path = path, relative_path = util.relative(path),
    start_line = 1, end_line = vim.api.nvim_buf_line_count(buf),
    content = table.concat(lines, "\n"), count = #diagnostics,
  }, scope)
  util.notify(("Added %d diagnostics as %s context"):format(#diagnostics, scope))
  return item
end

local function clear_values(values)
  for _, item in ipairs(values) do delete_anchor(item) end
  for index = #values, 1, -1 do table.remove(values, index) end
end

function M.clear(scope)
  if scope == "turn" then clear_values(M.turn_items)
  elseif scope == "pinned" then clear_values(M.pinned_items)
  else clear_values(M.turn_items); clear_values(M.pinned_items) end
  emit()
  util.notify(scope and ("Cleared " .. scope .. " context") or "Cleared all editor context")
end

function M.clear_turn() M.clear("turn") end
function M.clear_pinned() M.clear("pinned") end

function M.remove(id)
  for _, values in ipairs({ M.turn_items, M.pinned_items }) do
    for index, item in ipairs(values) do
      if item.id == id then
        delete_anchor(item)
        table.remove(values, index)
        emit()
        return item
      end
    end
  end
end

function M.refresh_item(id)
  for _, values in ipairs({ M.turn_items, M.pinned_items }) do
    for index, item in ipairs(values) do
      if not id or item.id == id then
        local resolved = resolve_item(item)
        item.content = resolved.content
        item.start_line = resolved.start_line
        item.end_line = resolved.end_line
        item.start_column = resolved.start_column
        item.end_column = resolved.end_column
        item.changedtick = resolved.changedtick
        item.captured_changedtick = resolved.changedtick
        item.modified = resolved.modified
        item.truncated = resolved.truncated
        values[index] = item
        if id then emit(); return item end
      end
    end
  end
  emit()
end

function M.consume(ids)
  local wanted = {}
  for _, id in ipairs(ids or {}) do wanted[id] = true end
  if next(wanted) == nil then return end
  for index = #M.turn_items, 1, -1 do
    local item = M.turn_items[index]
    if wanted[item.id] then
      delete_anchor(item)
      table.remove(M.turn_items, index)
    end
  end
  emit()
end

local function item_summary(item)
  local value = resolve_item(item)
  local label = value.relative_path or value.type
  if value.start_line then label = label .. (":%d-%d"):format(value.start_line, value.end_line) end
  return {
    id = value.id, scope = value.scope, type = value.type, label = label,
    path = value.relative_path, start_line = value.start_line,
    end_line = value.end_line, stale = value.stale == true,
    modified = value.modified == true,
    blocked = value.path and not path_allowed(value.path) or false,
    chars = #(value.content or ""),
  }
end

function M.summary(scope)
  local result = {}
  local groups = scope and { collection(scope) } or { M.turn_items, M.pinned_items }
  for _, values in ipairs(groups) do
    for _, item in ipairs(values) do result[#result + 1] = item_summary(item) end
  end
  return result
end

function M.labels()
  local result = {}
  for _, item in ipairs(M.summary()) do
    result[#result + 1] = (item.scope == "pinned" and "pin " or "turn ") .. item.label
  end
  return result
end

function M.latest_selection()
  local mode = vim.fn.mode()
  if mode == "v" or mode == "V" or mode == "\22" then
    local active = M.capture_selection()
    if active then return serialise(active) end
  end
  local latest
  for _, values in ipairs({ M.turn_items, M.pinned_items }) do
    for _, item in ipairs(values) do
      if item.type == "selection"
        and (not latest or (item._sequence or 0) > (latest._sequence or 0)) then
        latest = item
      end
    end
  end
  return latest and serialise(latest) or nil
end

function M.context_items()
  local result = {}
  for _, item in ipairs(M.turn_items) do result[#result + 1] = serialise(item) end
  for _, item in ipairs(M.pinned_items) do result[#result + 1] = serialise(item) end
  return result
end

local function live_context()
  if not config.get().context.automatic then return nil end
  local snapshot = editor.snapshot()
  if not snapshot or not path_allowed(snapshot.path) then return nil end
  snapshot.path = util.relative(snapshot.path)
  snapshot.win, snapshot.buf = nil, nil
  return snapshot
end

local function live_diagnostics(live)
  if not live or not config.get().context.include_diagnostics then return {} end
  local snapshot = editor.snapshot()
  if not snapshot or not snapshot.buf then return {} end
  local severity = { [1] = "error", [2] = "warning", [3] = "info", [4] = "hint" }
  local result = {}
  for _, diagnostic in ipairs(vim.diagnostic.get(snapshot.buf)) do
    result[#result + 1] = {
      path = live.path, line = diagnostic.lnum + 1,
      column = diagnostic.col or 0,
      severity = severity[diagnostic.severity] or "info",
      source = diagnostic.source,
      message = tostring(diagnostic.message):gsub("\n", " "),
    }
  end
  return result
end

local function open_buffers()
  if not config.get().context.include_open_buffers then return {} end
  local result = {}
  for _, buf in ipairs(vim.api.nvim_list_bufs()) do
    local path = vim.api.nvim_buf_get_name(buf)
    if vim.api.nvim_buf_is_loaded(buf) and vim.bo[buf].buftype == "" and path_allowed(path) then
      result[#result + 1] = {
        path = util.relative(path), filetype = vim.bo[buf].filetype,
        modified = vim.bo[buf].modified,
        changedtick = vim.api.nvim_buf_get_changedtick(buf),
        line_count = vim.api.nvim_buf_line_count(buf),
      }
    end
  end
  return result
end

local function fit_content(text, remaining)
  text = tostring(text or "")
  if #text <= remaining then return text, #text, false end
  if remaining <= 0 then return "", 0, true end
  local suffix = "\n… [truncated by MUCLI context budget]"
  if remaining <= #suffix then return text:sub(1, remaining), remaining, true end
  return text:sub(1, remaining - #suffix) .. suffix, remaining, true
end

local function budget_items(items, remaining, excluded)
  local included = 0
  for _, item in ipairs(items) do
    local fitted, used, truncated = fit_content(item.content, remaining)
    item.content = fitted
    item.truncated = item.truncated or truncated
    remaining = math.max(0, remaining - used)
    included = included + used
    if truncated then excluded[#excluded + 1] = item.id or item.path or item.type end
  end
  return remaining, included
end

function M.receipt(payload)
  payload = payload or {}
  local live, items, stale = payload.live, {}, 0
  for _, group in ipairs({ payload.turn or {}, payload.pinned or {} }) do
    for _, item in ipairs(group) do
      if item.stale then stale = stale + 1 end
      items[#items + 1] = {
        id = item.id, scope = item.scope, type = item.type, path = item.path,
        start_line = item.start_line, end_line = item.end_line,
        modified = item.modified, stale = item.stale,
        truncated = item.truncated, chars = #(item.content or ""),
      }
    end
  end
  return {
    version = 2, revision = payload.revision,
    live = live and {
      path = live.path,
      start_line = live.viewport and live.viewport.start_line,
      end_line = live.viewport and live.viewport.end_line,
      cursor = live.cursor, modified = live.modified,
      changedtick = live.changedtick,
      truncated = live.viewport and live.viewport.truncated,
    } or nil,
    turn_count = #(payload.turn or {}), pinned_count = #(payload.pinned or {}),
    diagnostics_count = #(payload.diagnostics or {}),
    open_buffers_count = #(payload.open_buffers or {}), stale_count = stale,
    items = items,
    included_chars = payload.budget and payload.budget.included_chars or 0,
    approx_tokens = payload.budget and payload.budget.approx_tokens or 0,
    truncated = payload.budget and payload.budget.truncated or false,
    excluded_count = payload.budget and payload.budget.excluded_count or 0,
  }
end

function M.build()
  local maximum = math.max(0, tonumber(config.get().context.max_chars) or 48000)
  local remaining, included, excluded = maximum, 0, {}
  local turn, pinned, turn_ids = {}, {}, {}
  for _, item in ipairs(M.turn_items) do
    turn_ids[#turn_ids + 1] = item.id
    if not item.path or path_allowed(item.path) then
      turn[#turn + 1] = serialise(item)
    else
      excluded[#excluded + 1] = item.id or "blocked turn context"
    end
  end
  for _, item in ipairs(M.pinned_items) do
    if not item.path or path_allowed(item.path) then
      pinned[#pinned + 1] = serialise(item)
    else
      excluded[#excluded + 1] = item.id or "blocked pinned context"
    end
  end
  local used
  remaining, used = budget_items(turn, remaining, excluded); included = included + used

  local live = live_context()
  if live and live.viewport then
    local fitted, live_used, truncated = fit_content(live.viewport.content, remaining)
    live.viewport.content, live.viewport.truncated = fitted, truncated
    remaining = math.max(0, remaining - live_used); included = included + live_used
    if truncated then excluded[#excluded + 1] = "live viewport" end
  end

  remaining, used = budget_items(pinned, remaining, excluded); included = included + used

  local diagnostics = live_diagnostics(live)
  for _, diagnostic in ipairs(diagnostics) do
    local fitted, diagnostic_used, truncated = fit_content(diagnostic.message, remaining)
    diagnostic.message = fitted
    remaining = math.max(0, remaining - diagnostic_used)
    included = included + diagnostic_used
    if truncated then excluded[#excluded + 1] = "diagnostic" end
  end

  local payload = {
    version = 2, source = "neovim",
    revision = util.uuid(table.concat({ util.workspace_root(), included, #turn, #pinned }, ":")),
    workspace = util.workspace_root(),
    captured_at = os.date("!%Y-%m-%dT%H:%M:%SZ"),
    live = live, turn = turn, pinned = pinned,
    diagnostics = diagnostics, open_buffers = open_buffers(),
    budget = {
      max_chars = maximum, included_chars = included,
      approx_tokens = math.ceil(included / 4),
      truncated = #excluded > 0, excluded_count = #excluded,
      excluded = excluded,
    },
  }
  return payload, { turn_ids = turn_ids, receipt = M.receipt(payload) }
end

-- Proof-of-concept compatibility: v2 never appends context transport data to
-- user prose.
function M.compose(prompt)
  local payload, metadata = M.build()
  return tostring(prompt or ""), metadata, payload
end

function M.status()
  if not config.get().context.automatic then
    return ("LIVE OFF · PINNED %d · TURN %d"):format(#M.pinned_items, #M.turn_items)
  end
  local live = editor.describe()
  local label = "NO LIVE FILE"
  if live and live.path ~= "" and path_allowed(live.path) then
    label = ("LIVE %s:%d-%d"):format(
      util.relative(live.path), live.viewport.start_line, live.viewport.end_line
    )
  end
  return label .. (" · PINNED %d · TURN %d"):format(#M.pinned_items, #M.turn_items)
end

function M.jump(id)
  local target
  for _, values in ipairs({ M.turn_items, M.pinned_items }) do
    for _, item in ipairs(values) do if item.id == id then target = resolve_item(item); break end end
    if target then break end
  end
  if not target or not target.path then return false end
  local win = editor.window()
  if not win then return false end
  vim.api.nvim_set_current_win(win)
  vim.cmd("edit " .. vim.fn.fnameescape(target.path))
  vim.api.nvim_win_set_cursor(win, { target.start_line or 1, target.start_column or 0 })
  vim.cmd("normal! zz")
  return true
end

function M.picker()
  local choices = {
    { label = "Pin last visual selection", action = M.add_selection },
    { label = "Pin active file", action = M.add_file },
    { label = "Pin active diagnostics", action = M.add_diagnostics },
    { label = "Open context drawer", action = function() require("mucli.context_panel").open(true) end },
    { label = "Clear turn-only context", action = M.clear_turn },
    { label = "Clear pinned context", action = M.clear_pinned },
  }
  vim.ui.select(choices, {
    prompt = "MUCLI context",
    format_item = function(item) return item.label end,
  }, function(choice) if choice then choice.action() end end)
end

return M
