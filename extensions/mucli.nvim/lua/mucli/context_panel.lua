local M = {
  buf = nil,
  win = nil,
  actions = {},
  namespace = vim.api.nvim_create_namespace("mucli_context_panel"),
}

local context = require("mucli.context")
local config = require("mucli.config")
local editor = require("mucli.editor")
local util = require("mucli.util")

local function valid_buf(buf) return buf and vim.api.nvim_buf_is_valid(buf) end
local function valid_win(win) return win and vim.api.nvim_win_is_valid(win) end

local function scratch(name, filetype)
  local buf = vim.api.nvim_create_buf(false, true)
  pcall(vim.api.nvim_buf_set_name, buf, name)
  vim.bo[buf].buftype = "nofile"
  vim.bo[buf].bufhidden = "wipe"
  vim.bo[buf].swapfile = false
  vim.bo[buf].modeline = false
  vim.bo[buf].filetype = filetype
  return buf
end

local function dimensions()
  local width = math.min(86, math.max(46, vim.o.columns - 12))
  local height = math.min(28, math.max(12, vim.o.lines - 10))
  return width, height
end

local function item_line(item)
  local flags = {}
  if item.blocked then flags[#flags + 1] = "blocked" end
  if item.modified then flags[#flags + 1] = "unsaved" end
  if item.stale then flags[#flags + 1] = "changed" end
  local suffix = #flags > 0 and (" · " .. table.concat(flags, ", ")) or ""
  return ("  %s  %s · %d chars%s"):format(
    (item.stale or item.blocked) and "!" or "•",
    item.label, item.chars or 0, suffix
  )
end

function M.build()
  local lines, actions = {
    " MUCLI CONTEXT",
    " Live editor state is recomputed when you send. Pinned anchors persist; turn items are consumed.",
    "",
    " LIVE NOW",
  }, {}
  local live = editor.describe()
  if not config.get().context.automatic then
    lines[#lines + 1] = "  ○ Automatic live context is disabled"
  elseif live and live.path and live.path ~= "" then
    lines[#lines + 1] = ("  • %s:%d-%d · cursor %d:%d%s"):format(
      util.relative(live.path), live.viewport.start_line, live.viewport.end_line,
      live.cursor.line, live.cursor.column,
      live.modified and " · unsaved" or ""
    )
  else
    lines[#lines + 1] = "  ○ No live file buffer"
  end

  for _, scope in ipairs({ "turn", "pinned" }) do
    local items = context.summary(scope)
    lines[#lines + 1] = ""
    lines[#lines + 1] = (" %s (%d)"):format(scope == "turn" and "THIS TURN" or "PINNED", #items)
    if #items == 0 then
      lines[#lines + 1] = "  ○ none"
    else
      for _, item in ipairs(items) do
        lines[#lines + 1] = item_line(item)
        actions[#lines] = item
      end
    end
  end

  local payload = context.build()
  local budget = payload.budget or {}
  lines[#lines + 1] = ""
  lines[#lines + 1] = (" BUDGET  ~%d tokens · %d/%d chars%s"):format(
    budget.approx_tokens or 0,
    budget.included_chars or 0,
    budget.max_chars or 0,
    budget.truncated and (" · " .. tostring(budget.excluded_count or 0) .. " truncated/excluded") or ""
  )
  lines[#lines + 1] = ""
  lines[#lines + 1] = " <CR> jump   d remove   r refresh   a add   i inspect"
  lines[#lines + 1] = " t clear turn   p clear pins   q close"
  return lines, actions
end

function M.refresh()
  if not valid_buf(M.buf) then return end
  local lines, actions = M.build()
  vim.bo[M.buf].modifiable = true
  vim.api.nvim_buf_set_lines(M.buf, 0, -1, false, lines)
  vim.api.nvim_buf_clear_namespace(M.buf, M.namespace, 0, -1)
  for index, line in ipairs(lines) do
    local group
    if index == 1 then group = "MucliTitle"
    elseif line:match("^ LIVE NOW") then group = "DiagnosticInfo"
    elseif line:match("^ THIS TURN") then group = "MucliWorking"
    elseif line:match("^ PINNED") then group = "Identifier"
    elseif line:match("^ BUDGET") then
      group = line:find("truncated", 1, true) and "DiagnosticWarn" or "Comment"
    elseif line:match("^ <CR>") or line:match("^ t clear")
      or line:match("^ Live editor state") then
      group = "Comment"
    elseif line:match("^  !") then group = "DiagnosticWarn" end
    if group then
      vim.api.nvim_buf_add_highlight(
        M.buf, M.namespace, group, index - 1, 0, -1
      )
    end
  end
  vim.bo[M.buf].modifiable = false
  M.actions = actions
end

local function current_item()
  if not valid_win(M.win) then return nil end
  return M.actions[vim.api.nvim_win_get_cursor(M.win)[1]]
end

function M.close()
  if valid_win(M.win) then pcall(vim.api.nvim_win_close, M.win, true) end
  M.win = nil
end

local function configure_keymaps(buf)
  local opts = { buffer = buf, silent = true, nowait = true }
  vim.keymap.set("n", "q", M.close, vim.tbl_extend("force", opts, { desc = "Close MUCLI context" }))
  vim.keymap.set("n", "<Esc>", M.close, opts)
  vim.keymap.set("n", "<CR>", function()
    local item = current_item()
    if item then M.close(); context.jump(item.id) end
  end, vim.tbl_extend("force", opts, { desc = "Jump to MUCLI context" }))
  vim.keymap.set("n", "d", function()
    local item = current_item()
    if item then context.remove(item.id) end
  end, vim.tbl_extend("force", opts, { desc = "Remove MUCLI context" }))
  vim.keymap.set("n", "r", function()
    local item = current_item()
    context.refresh_item(item and item.id or nil)
  end, vim.tbl_extend("force", opts, { desc = "Refresh MUCLI context" }))
  vim.keymap.set("n", "a", context.picker, vim.tbl_extend("force", opts, { desc = "Add MUCLI context" }))
  vim.keymap.set("n", "t", context.clear_turn, vim.tbl_extend("force", opts, { desc = "Clear turn context" }))
  vim.keymap.set("n", "p", context.clear_pinned, vim.tbl_extend("force", opts, { desc = "Clear pinned context" }))
  vim.keymap.set("n", "i", function() M.inspect() end, vim.tbl_extend("force", opts, { desc = "Inspect MUCLI context payload" }))
end

function M.open(focus)
  if valid_win(M.win) then
    M.refresh()
    if focus ~= false then vim.api.nvim_set_current_win(M.win) end
    return
  end
  if not valid_buf(M.buf) then
    M.buf = scratch("mucli://context", "mucli-context")
    configure_keymaps(M.buf)
  end
  local width, height = dimensions()
  M.win = vim.api.nvim_open_win(M.buf, focus ~= false, {
    relative = "editor",
    row = math.max(1, math.floor((vim.o.lines - height) / 2) - 1),
    col = math.max(1, math.floor((vim.o.columns - width) / 2)),
    width = width,
    height = height,
    style = "minimal",
    border = "rounded",
    title = " MUCLI Context ",
    title_pos = "center",
    zindex = 55,
  })
  vim.wo[M.win].cursorline = true
  vim.wo[M.win].wrap = false
  M.refresh()
end

function M.toggle()
  if valid_win(M.win) then M.close() else M.open(true) end
end

local function inspect_value(name, value)
  local buf = scratch("mucli://" .. name .. "-" .. util.uuid(name), "lua")
  local lines = vim.split(vim.inspect(value), "\n", { plain = true })
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
  vim.bo[buf].modifiable = false
  local width, height = dimensions()
  local win = vim.api.nvim_open_win(buf, true, {
    relative = "editor",
    row = math.max(1, math.floor((vim.o.lines - height) / 2) - 1),
    col = math.max(1, math.floor((vim.o.columns - width) / 2)),
    width = width,
    height = height,
    style = "minimal",
    border = "rounded",
    title = " MUCLI Context Inspector ",
    title_pos = "center",
    zindex = 60,
  })
  vim.wo[win].wrap = false
  vim.keymap.set("n", "q", function() if valid_win(win) then vim.api.nvim_win_close(win, true) end end, { buffer = buf, silent = true })
  vim.keymap.set("n", "<Esc>", function() if valid_win(win) then vim.api.nvim_win_close(win, true) end end, { buffer = buf, silent = true })
end

function M.inspect()
  local payload = context.build()
  inspect_value("context-inspect", payload)
end

function M.inspect_receipt(receipt)
  inspect_value("context-receipt", receipt or {})
end

return M
