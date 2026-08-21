local M = {
  namespace = vim.api.nvim_create_namespace("mucli_completion"),
  suggestion = nil,
}

local config = require("mucli.config")
local util = require("mucli.util")

function M.parse_response(text)
  local completion = tostring(text or ""):match("<mucli%-completion>(.-)</mucli%-completion>")
  if not completion then return nil, "The model did not return a MUCLI completion payload" end
  completion = completion:gsub("^\n", ""):gsub("\n$", "")
  if completion == "" then return nil, "MUCLI returned an empty completion" end
  return completion
end

local function editor()
  local win = require("mucli.chat.panel").editor_window()
  if not win then return nil end
  return win, vim.api.nvim_win_get_buf(win)
end

function M.clear()
  if M.suggestion and vim.api.nvim_buf_is_valid(M.suggestion.buf) then
    vim.api.nvim_buf_clear_namespace(M.suggestion.buf, M.namespace, 0, -1)
  end
  M.suggestion = nil
end

local function display(suggestion)
  M.clear()
  M.suggestion = suggestion
  local lines = vim.split(suggestion.text, "\n", { plain = true })
  local options = {
    id = 1,
    virt_text = { { lines[1], "MucliGhostText" } },
    virt_text_pos = "inline",
    hl_mode = "combine",
  }
  if #lines > 1 then
    options.virt_lines = {}
    for index = 2, #lines do options.virt_lines[#options.virt_lines + 1] = { { lines[index], "MucliGhostText" } } end
  end
  vim.api.nvim_buf_set_extmark(suggestion.buf, M.namespace, suggestion.row, suggestion.col, options)
end

function M.request()
  if not config.get().completion.enabled then util.notify("Inline completion is disabled", vim.log.levels.WARN); return end
  local win, buf = editor()
  if not win or vim.bo[buf].buftype ~= "" then util.notify("Open a file before requesting a completion", vim.log.levels.WARN); return end
  local path = vim.api.nvim_buf_get_name(buf)
  if not config.get().workspace.allow_secret_paths and util.is_secret_path(path) then
    util.notify("Secret-path policy blocked completion for this file", vim.log.levels.ERROR)
    return
  end
  local cursor = vim.api.nvim_win_get_cursor(win)
  local row, col = cursor[1] - 1, cursor[2]
  local count = vim.api.nvim_buf_line_count(buf)
  local radius = math.floor(config.get().completion.context_lines / 2)
  local first, last = math.max(0, row - radius), math.min(count, row + radius + 1)
  local lines = vim.api.nvim_buf_get_lines(buf, first, last, false)
  local current = lines[row - first + 1] or ""
  local before, after = current:sub(1, col), current:sub(col + 1)
  lines[row - first + 1] = before .. "<MUCLI_CURSOR>" .. after
  local source = util.truncate(table.concat(lines, "\n"), config.get().completion.max_source_chars)
  local prompt = ([=[
Complete the code at <MUCLI_CURSOR>. Return only text that should be inserted at
the cursor; do not repeat text before or after it, do not edit files, and do not
include Markdown. Prefer the smallest idiomatic completion (at most 12 lines).
The final answer must be exactly:
<mucli-completion>INSERTED TEXT</mucli-completion>

File: %s
Filetype: %s
Context begins at line %d:
%s
]=]):format(util.relative(path), vim.bo[buf].filetype, first + 1, source)
  local tick = vim.api.nvim_buf_get_changedtick(buf)
  require("mucli.conversation").ephemeral(prompt, {
    kind = "completion", status = "Generating inline completion…",
    on_complete = function(text, err)
      if err then util.notify(err, vim.log.levels.ERROR); return end
      if not vim.api.nvim_buf_is_valid(buf) or vim.api.nvim_buf_get_changedtick(buf) ~= tick then
        util.notify("Buffer changed while completion was generated; result discarded", vim.log.levels.WARN)
        return
      end
      local value, parse_err = M.parse_response(text)
      if not value then util.notify(parse_err, vim.log.levels.ERROR); return end
      display({ buf = buf, row = row, col = col, tick = tick, text = value })
    end,
    on_error = function(err) util.notify(err or "Completion failed", vim.log.levels.ERROR) end,
  })
end

local function insert(text, remaining)
  local suggestion = M.suggestion
  if not suggestion then return false end
  if not vim.api.nvim_buf_is_valid(suggestion.buf) or vim.api.nvim_buf_get_changedtick(suggestion.buf) ~= suggestion.tick then
    M.clear()
    return false
  end
  local lines = vim.split(text, "\n", { plain = true })
  vim.api.nvim_buf_clear_namespace(suggestion.buf, M.namespace, 0, -1)
  vim.api.nvim_buf_set_text(suggestion.buf, suggestion.row, suggestion.col, suggestion.row, suggestion.col, lines)
  local end_row = suggestion.row + #lines - 1
  local end_col = #lines == 1 and (suggestion.col + #lines[1]) or #lines[#lines]
  local win = require("mucli.chat.panel").editor_window()
  if win and vim.api.nvim_win_is_valid(win) and vim.api.nvim_win_get_buf(win) == suggestion.buf then
    vim.api.nvim_win_set_cursor(win, { end_row + 1, end_col })
  end
  if remaining and remaining ~= "" then
    display({
      buf = suggestion.buf, row = end_row, col = end_col,
      tick = vim.api.nvim_buf_get_changedtick(suggestion.buf), text = remaining,
    })
  else
    M.suggestion = nil
  end
  return true
end

function M.accept() return insert(M.suggestion and M.suggestion.text or "") end

function M.accept_word()
  if not M.suggestion then return false end
  local word = M.suggestion.text:match("^%s*[%w_]+[%p]*") or M.suggestion.text:match("^[^%s]+")
  if not word then return M.accept() end
  return insert(word, M.suggestion.text:sub(#word + 1))
end

local function cursor_matches(suggestion)
  local win = vim.fn.bufwinid(suggestion.buf)
  if win == -1 then return false end
  local cursor = vim.api.nvim_win_get_cursor(win)
  if cursor[1] - 1 ~= suggestion.row then return false end
  if cursor[2] == suggestion.col then return true end

  -- Normal mode cannot place its cursor on the insertion cell just beyond
  -- end-of-line. Treat the final byte as that same logical anchor.
  local line = (vim.api.nvim_buf_get_lines(
    suggestion.buf, suggestion.row, suggestion.row + 1, false
  )[1] or "")
  return suggestion.col == #line
    and suggestion.col > 0
    and cursor[2] == suggestion.col - 1
end

function M.setup()
  local group = vim.api.nvim_create_augroup("MucliCompletion", { clear = true })
  vim.api.nvim_create_autocmd({ "TextChanged", "TextChangedI" }, {
    group = group,
    callback = function(args)
      if M.suggestion and args.buf == M.suggestion.buf and vim.api.nvim_buf_get_changedtick(args.buf) ~= M.suggestion.tick then M.clear() end
    end,
  })
  vim.api.nvim_create_autocmd("BufLeave", {
    group = group,
    callback = function(args)
      if M.suggestion and args.buf == M.suggestion.buf then M.clear() end
    end,
  })
  vim.api.nvim_create_autocmd({ "CursorMoved", "CursorMovedI" }, {
    group = group,
    callback = function(args)
      if M.suggestion and args.buf == M.suggestion.buf and not cursor_matches(M.suggestion) then M.clear() end
    end,
  })
end

return M
