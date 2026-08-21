local M = {
  namespace = vim.api.nvim_create_namespace("mucli_hints"),
  items = {},
}

local config = require("mucli.config")
local util = require("mucli.util")

local severity = {
  error = vim.diagnostic.severity.ERROR,
  warning = vim.diagnostic.severity.WARN,
  warn = vim.diagnostic.severity.WARN,
  info = vim.diagnostic.severity.INFO,
  hint = vim.diagnostic.severity.HINT,
}

function M.parse_response(text)
  text = tostring(text or "")
  local payload = text:match("<mucli%-hints>%s*(.-)%s*</mucli%-hints>")
    or text:match("```json%s*(.-)%s*```")
  if not payload then return nil, "The model did not return a MUCLI hints payload" end
  local ok, decoded = pcall(vim.json.decode, payload)
  if not ok or type(decoded) ~= "table" or type(decoded.hints) ~= "table" then
    return nil, "The MUCLI hints payload was invalid JSON"
  end
  return decoded.hints
end

local function editor_buffer()
  local win = require("mucli.chat.panel").editor_window()
  return win and vim.api.nvim_win_get_buf(win) or vim.api.nvim_get_current_buf()
end

local function publish(buf, items)
  local count = vim.api.nvim_buf_line_count(buf)
  local diagnostics, normalized = {}, {}
  for index, item in ipairs(items or {}) do
    if index > config.get().hints.max_items then break end
    local line = util.clamp(tonumber(item.line) or 1, 1, math.max(1, count))
    local ending = util.clamp(tonumber(item.end_line) or line, line, math.max(line, count))
    local message = tostring(item.message or item.title or "Suggestion")
    local value = {
      line = line, end_line = ending, severity = tostring(item.severity or "hint"):lower(),
      title = tostring(item.title or "Improvement"), message = message,
      suggestion = tostring(item.suggestion or ""), code = item.code,
    }
    normalized[#normalized + 1] = value
    diagnostics[#diagnostics + 1] = {
      lnum = line - 1, end_lnum = ending - 1, col = math.max(0, tonumber(item.column) or 0),
      severity = severity[value.severity] or severity.hint,
      message = value.title .. ": " .. value.message, source = "MUCLI", code = value.code,
    }
  end
  M.items[buf] = normalized
  vim.diagnostic.set(M.namespace, buf, diagnostics, {})
  util.notify(("Published %d editor hint%s"):format(#diagnostics, #diagnostics == 1 and "" or "s"))
end

local function source_for(buf, first, last)
  local count = vim.api.nvim_buf_line_count(buf)
  first, last = first or 1, last or count
  first, last = util.clamp(first, 1, math.max(1, count)), util.clamp(last, first, math.max(first, count))
  local lines = vim.api.nvim_buf_get_lines(buf, first - 1, last, false)
  local numbered = {}
  for index, line in ipairs(lines) do numbered[#numbered + 1] = ("%6d | %s"):format(first + index - 1, line) end
  local text = table.concat(numbered, "\n")
  text = util.truncate(text, config.get().hints.max_source_chars)
  return text, first, last
end

function M.analyze(first, last)
  if not config.get().hints.enabled then util.notify("Hints are disabled", vim.log.levels.WARN); return end
  local buf = editor_buffer()
  local path = vim.api.nvim_buf_get_name(buf)
  if path == "" or vim.bo[buf].buftype ~= "" then util.notify("Open a file before requesting hints", vim.log.levels.WARN); return end
  if not config.get().workspace.allow_secret_paths and util.is_secret_path(path) then
    util.notify("Secret-path policy blocked hint analysis for this file", vim.log.levels.ERROR)
    return
  end
  local source
  source, first, last = source_for(buf, first, last)
  local prompt = ([=[
Review this live Neovim buffer for concrete correctness, maintainability,
performance, security and clarity improvements. Do not edit files and do not
call mutation tools. Return at most %d high-signal findings. The final answer
must contain exactly one payload using this shape (no prose outside it):

<mucli-hints>{"hints":[{"line":1,"end_line":1,"severity":"warning","title":"Short title","message":"Why this matters","suggestion":"Specific next action"}]}</mucli-hints>

File: %s
Filetype: %s
Changedtick: %d
Reviewed range: %d-%d

%s
]=]):format(
    config.get().hints.max_items, util.relative(path), vim.bo[buf].filetype,
    vim.api.nvim_buf_get_changedtick(buf), first, last, source
  )
  local tick = vim.api.nvim_buf_get_changedtick(buf)
  require("mucli.conversation").ephemeral(prompt, {
    kind = "hints", status = "Analyzing " .. util.relative(path) .. " for editor hints…",
    on_complete = function(text, err)
      if err then util.notify(err, vim.log.levels.ERROR); return end
      if not vim.api.nvim_buf_is_valid(buf) or vim.api.nvim_buf_get_changedtick(buf) ~= tick then
        util.notify("Buffer changed while hints were generated; stale results were discarded", vim.log.levels.WARN)
        return
      end
      local hints, parse_err = M.parse_response(text)
      if not hints then util.notify(parse_err, vim.log.levels.ERROR); return end
      publish(buf, hints)
    end,
    on_error = function(err) util.notify(err or "Hint analysis failed", vim.log.levels.ERROR) end,
  })
end

function M.clear(buf)
  buf = buf or editor_buffer()
  M.items[buf] = nil
  vim.diagnostic.reset(M.namespace, buf)
end

local function item_at_cursor(buf)
  local win = require("mucli.chat.panel").editor_window()
  local line = vim.api.nvim_win_get_cursor(win or 0)[1]
  local best, distance
  for _, item in ipairs(M.items[buf] or {}) do
    local d = line < item.line and item.line - line or line > item.end_line and line - item.end_line or 0
    if not distance or d < distance then best, distance = item, d end
  end
  return best
end

function M.action()
  local buf = editor_buffer()
  local item = item_at_cursor(buf)
  if not item then util.notify("No MUCLI hint in this buffer", vim.log.levels.WARN); return end
  local choices = { "Explain in chat", "Fix this", "Dismiss" }
  vim.ui.select(choices, { prompt = item.title .. " — " .. item.message }, function(choice)
    if choice == "Explain in chat" then
      require("mucli.conversation").send(("Explain this review finding at line %d and recommend the best response: %s. Suggested action: %s")
        :format(item.line, item.message, item.suggestion))
    elseif choice == "Fix this" then
      require("mucli.conversation").send(("Fix this review finding at line %d: %s. Suggested action: %s. Preserve existing behavior and show me the diff for approval.")
        :format(item.line, item.message, item.suggestion))
    elseif choice == "Dismiss" then
      local kept = {}
      for _, candidate in ipairs(M.items[buf] or {}) do if candidate ~= item then kept[#kept + 1] = candidate end end
      publish(buf, kept)
    end
  end)
end

function M.next()
  if vim.diagnostic.jump then vim.diagnostic.jump({ count = 1, namespace = M.namespace })
  else vim.diagnostic.goto_next({ namespace = M.namespace }) end
end

function M.previous()
  if vim.diagnostic.jump then vim.diagnostic.jump({ count = -1, namespace = M.namespace })
  else vim.diagnostic.goto_prev({ namespace = M.namespace }) end
end

vim.diagnostic.config({
  virtual_text = config.get().hints.virtual_text and { prefix = "μ", spacing = 2 } or false,
  signs = true, underline = false, severity_sort = true,
}, M.namespace)

return M
