local M = { namespace = vim.api.nvim_create_namespace("mucli_chat"), actions = {} }

local store = require("mucli.store")

local role = {
  user = { label = "YOU", highlight = "MucliUser" },
  assistant = { label = "MUCLI", highlight = "MucliAssistant" },
  error = { label = "ERROR", highlight = "DiagnosticError" },
  activity = { label = "ACTIVITY", highlight = "Comment" },
}

local function append_text(lines, text)
  local values = vim.split(tostring(text or ""), "\n", { plain = true })
  for _, line in ipairs(values) do lines[#lines + 1] = line end
end

local function add_mark(marks, line, group, start_col, end_col)
  marks[#marks + 1] = { line = line, group = group, start_col = start_col or 0, end_col = end_col or -1 }
end

function M.build(state)
  state = state or store.state
  local lines, marks, actions = {}, {}, {}
  if #state.messages == 0 then
    lines = {
      "", "  MUCLI is ready", "",
      "  Ask a question, select code for a focused action, or run :MucliActions.",
      "", "  C-s  send message", "  C-a  add context", "  C-c  interrupt",
    }
    add_mark(marks, 1, "MucliTitle")
    add_mark(marks, 3, "Comment")
  else
    for _, message in ipairs(state.messages) do
      local descriptor = role[message.role] or role.assistant
      if #lines > 0 then lines[#lines + 1] = "" end
      local header_line = #lines
      lines[#lines + 1] = descriptor.label .. "  ─────────────────────────────"
      add_mark(marks, header_line, descriptor.highlight)
      if message.thinking and message.thinking ~= "" then
        local thought_line = #lines
        lines[#lines + 1] = "  ▸ reasoning streamed"
        add_mark(marks, thought_line, "Comment")
      end
      for _, item in ipairs(message.activities or {}) do
        local activity_line = #lines
        local prefix = item.kind == "diff" and "  Δ " or item.kind == "artifact" and "  ◇ " or "  ▸ "
        lines[#lines + 1] = prefix .. tostring(item.label or item.kind)
        add_mark(marks, activity_line, item.kind == "diff" and "MucliDiff" or "Comment")
        if item.kind == "diff" then actions[activity_line + 1] = function() require("mucli.diff").open_last() end end
      end
      if message.text and message.text ~= "" then append_text(lines, message.text) end
      if message.context_receipt then
        local receipt = message.context_receipt
        local live = receipt.live or {}
        local path = live.path and live.path ~= "" and live.path or "no live file"
        local range = live.start_line and (":" .. live.start_line .. "-" .. live.end_line) or ""
        local receipt_line = #lines
        lines[#lines + 1] = ("  ◉ context · %s%s · pinned %d · turn %d · ~%d tokens%s"):format(
          path, range, receipt.pinned_count or 0, receipt.turn_count or 0,
          receipt.approx_tokens or 0,
          receipt.truncated and " · truncated" or ""
        )
        add_mark(marks, receipt_line, receipt.truncated and "DiagnosticWarn" or "Comment")
        actions[receipt_line + 1] = function()
          require("mucli.context_panel").inspect_receipt(receipt)
        end
      end
    end
  end
  if state.busy then
    lines[#lines + 1] = ""
    local status_line = #lines
    lines[#lines + 1] = "  ● " .. tostring(state.status or "MUCLI is working…")
    add_mark(marks, status_line, "MucliWorking")
  end
  return lines, marks, actions
end

function M.render(buf, win)
  if not buf or not vim.api.nvim_buf_is_valid(buf) then return end
  local old_count = vim.api.nvim_buf_line_count(buf)
  local follow = true
  local old_cursor
  if win and vim.api.nvim_win_is_valid(win) then
    old_cursor = vim.api.nvim_win_get_cursor(win)
    follow = old_cursor[1] >= old_count - 4
  end
  local lines, marks, actions = M.build(store.state)
  vim.bo[buf].modifiable = true
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
  vim.api.nvim_buf_clear_namespace(buf, M.namespace, 0, -1)
  for _, mark in ipairs(marks) do
    vim.api.nvim_buf_add_highlight(buf, M.namespace, mark.group, mark.line, mark.start_col, mark.end_col)
  end
  vim.bo[buf].modifiable = false
  M.actions[buf] = actions
  if win and vim.api.nvim_win_is_valid(win) then
    if follow then
      vim.api.nvim_win_set_cursor(win, { math.max(1, #lines), 0 })
    elseif old_cursor then
      vim.api.nvim_win_set_cursor(win, { math.min(old_cursor[1], math.max(1, #lines)), old_cursor[2] })
    end
  end
end

function M.activate(buf, line)
  local action = M.actions[buf] and M.actions[buf][line]
  if action then action(); return true end
  return false
end

return M
