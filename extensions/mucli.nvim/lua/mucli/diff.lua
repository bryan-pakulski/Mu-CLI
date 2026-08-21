--- lua/mucli/diff.lua — Side-by-side diff view with accept/reject hunk keymaps.
-- Uses native neovim diff mode. No Diffview dependency.
-- parse_unified_diff() parses @@ hunk headers into structured table.
-- open_diff_view() creates tabpage with two scratch buffers in diff mode.

local M = {}

local state = {
  tabpage = nil,
  orig_buf = nil,
  mod_buf = nil,
  filepath = nil,
  orig_win = nil,
  mod_win = nil,
}

--- Parse unified diff text into structured hunks table.
--- @param diff_text string: unified diff text
--- @return table: hunks array, each with {old_start, old_count, new_start, new_count, added[], removed[]}
function M.parse_unified_diff(diff_text)
  local hunks = {}
  local current_hunk = nil

  for line in diff_text:gmatch("[^\r\n]+") do
    -- Match hunk header: @@ -old_start,old_count +new_start,new_count @@
    local old_start, old_count, new_start, new_count = line:match("^%-%-%- %-(%d+),?(%d*) %+(%d+),?(%d*) %-%-%-")
    if old_start then
      if current_hunk then
        table.insert(hunks, current_hunk)
      end
      current_hunk = {
        old_start = tonumber(old_start),
        old_count = tonumber(old_count) or 1,
        new_start = tonumber(new_start),
        new_count = tonumber(new_count) or 1,
        added = {},
        removed = {},
      }
    elseif current_hunk then
      if line:sub(1, 1) == "+" then
        table.insert(current_hunk.added, line:sub(2))
      elseif line:sub(1, 1) == "-" then
        table.insert(current_hunk.removed, line:sub(2))
      elseif line:sub(1, 1) == " " then
        -- context line, ignore
      end
    end
  end

  if current_hunk then
    table.insert(hunks, current_hunk)
  end

  return hunks
end

--- Apply hunks to original lines to produce modified lines.
--- @param original_lines table: array of original file lines
--- @param hunks table: parsed hunks from parse_unified_diff
--- @return table: modified lines array
function M.apply_hunks(original_lines, hunks)
  local result = {}
  local current_idx = 1

  for _, hunk in ipairs(hunks) do
    -- Copy lines before hunk
    while current_idx < hunk.old_start do
      table.insert(result, original_lines[current_idx])
      current_idx = current_idx + 1
    end
    -- Skip removed lines
    current_idx = current_idx + hunk.old_count
    -- Add inserted lines
    for _, added_line in ipairs(hunk.added) do
      table.insert(result, added_line)
    end
  end

  -- Copy remaining lines
  while current_idx <= #original_lines do
    table.insert(result, original_lines[current_idx])
    current_idx = current_idx + 1
  end

  return result
end

--- Open side-by-side diff view in new tabpage.
--- @param filepath string: path to the file being diffed
--- @param original_lines table: original file lines
--- @param modified_lines table: proposed modified lines
function M.open_diff_view(filepath, original_lines, modified_lines)
  -- Close existing diff tab if open
  M.close_diff_view()

  -- Create new tabpage
  vim.cmd("tabnew")
  state.tabpage = vim.api.nvim_get_current_tabpage()

  -- Create original buffer (left side)
  state.orig_buf = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_buf_set_lines(state.orig_buf, 0, -1, false, original_lines)
  vim.api.nvim_buf_set_name(state.orig_buf, filepath .. " (original)")
  vim.api.nvim_set_option_value("buftype", "nofile", { buf = state.orig_buf })
  vim.api.nvim_set_option_value("modifiable", false, { buf = state.orig_buf })
  vim.api.nvim_set_option_value("filetype", "diff", { buf = state.orig_buf })

  state.orig_win = vim.api.nvim_get_current_win()
  vim.api.nvim_win_set_buf(state.orig_win, state.orig_buf)

  -- Create modified buffer (right side)
  vim.cmd("vsplit")
  state.mod_buf = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_buf_set_lines(state.mod_buf, 0, -1, false, modified_lines)
  vim.api.nvim_buf_set_name(state.mod_buf, filepath .. " (modified)")
  vim.api.nvim_set_option_value("buftype", "nofile", { buf = state.mod_buf })
  vim.api.nvim_set_option_value("modifiable", false, { buf = state.mod_buf })
  vim.api.nvim_set_option_value("filetype", "diff", { buf = state.mod_buf })

  state.mod_win = vim.api.nvim_get_current_win()
  vim.api.nvim_win_set_buf(state.mod_win, state.mod_buf)

  -- Enable diff mode on both windows
  vim.api.nvim_set_option_value("diff", true, { win = state.orig_win })
  vim.api.nvim_set_option_value("diff", true, { win = state.mod_win })
  vim.api.nvim_set_option_value("wrap", false, { win = state.orig_win })
  vim.api.nvim_set_option_value("wrap", false, { win = state.mod_win })

  -- Store filepath for accept
  state.filepath = filepath

  -- Set buffer-local keymaps on modified buffer
  local config = require("mucli.config")
  local km = config.opts and config.opts.keymaps or {}
  local accept = km.accept_hunk or "<leader>da"
  local reject = km.reject_hunk or "<leader>dr"

  vim.keymap.set("n", accept, function()
    M.accept_hunk()
  end, { buffer = state.mod_buf, desc = "Accept diff hunk" })

  vim.keymap.set("n", reject, function()
    M.reject_hunk()
  end, { buffer = state.mod_buf, desc = "Reject diff hunk" })

  vim.keymap.set("n", "q", function()
    M.close_diff_view()
  end, { buffer = state.mod_buf, desc = "Close diff view" })

  vim.keymap.set("n", "q", function()
    M.close_diff_view()
  end, { buffer = state.orig_buf, desc = "Close diff view" })
end

--- Accept hunk: write modified lines to actual file, update real buffer if open, close diff tab.
function M.accept_hunk()
  if not state.mod_buf or not state.filepath then
    vim.notify("[mucli] No diff view open", vim.log.levels.WARN)
    return
  end

  local modified_lines = vim.api.nvim_buf_get_lines(state.mod_buf, 0, -1, false)
  local filepath = state.filepath

  -- Write to file
  local f = io.open(filepath, "w")
  if not f then
    vim.notify("[mucli] Failed to write to " .. filepath, vim.log.levels.ERROR)
    return
  end
  for _, line in ipairs(modified_lines) do
    f:write(line .. "\n")
  end
  f:close()

  -- Update real buffer if it's open
  for _, buf in ipairs(vim.api.nvim_list_bufs()) do
    local name = vim.api.nvim_buf_get_name(buf)
    if name == filepath then
      vim.api.nvim_buf_set_lines(buf, 0, -1, false, modified_lines)
      vim.api.nvim_buf_call(buf, function()
        vim.cmd("checktime")
      end)
      break
    end
  end

  vim.notify("[mucli] Accepted: " .. filepath, vim.log.levels.INFO)
  M.close_diff_view()
end

--- Reject hunk: close diff tab without changes.
function M.reject_hunk()
  if not state.tabpage then
    vim.notify("[mucli] No diff view open", vim.log.levels.WARN)
    return
  end
  vim.notify("[mucli] Rejected diff", vim.log.levels.INFO)
  M.close_diff_view()
end

--- Close diff tab and clean up.
function M.close_diff_view()
  if state.tabpage and vim.api.nvim_tabpage_is_valid(state.tabpage) then
    vim.api.nvim_set_current_tabpage(state.tabpage)
    vim.cmd("tabclose")
  end
  state.tabpage = nil
  state.orig_buf = nil
  state.mod_buf = nil
  state.filepath = nil
  state.orig_win = nil
  state.mod_win = nil
end

--- Check if diff view is open.
function M.is_open()
  return state.tabpage ~= nil and vim.api.nvim_tabpage_is_valid(state.tabpage)
end

--- Show diff for a file given original content and a unified diff.
--- @param filepath string: file path
--- @param diff_text string: unified diff text
function M.show_diff(filepath, diff_text)
  -- Read original file content
  local f = io.open(filepath, "r")
  if not f then
    vim.notify("[mucli] Cannot read " .. filepath, vim.log.levels.ERROR)
    return
  end
  local original_lines = {}
  for line in f:lines() do
    table.insert(original_lines, line)
  end
  f:close()

  local hunks = M.parse_unified_diff(diff_text)
  local modified_lines = M.apply_hunks(original_lines, hunks)
  M.open_diff_view(filepath, original_lines, modified_lines)
end

--- Detect and show diff from an SSE event (artifact_created or assistant response with diff).
--- Extracts filepath and diff text from event metadata, captures original
--- content, and opens diff view.
--- @param event table: SSE event with {kind, name/text, filepath?, diff_text?, content?}
function M.detect_and_show_diff(event)
  -- Extract diff text from event
  local diff_text = event.diff_text or event.diff or event.content or ""
  if type(diff_text) ~= "string" or #diff_text == 0 then
    return
  end

  -- Check if it looks like a unified diff (contains @@ hunk markers)
  if not diff_text:match("@@") then
    return
  end

  -- Extract filepath from event metadata
  local filepath = event.filepath or event.file_path or event.path or event.name
  if not filepath or filepath == "artifact" then
    -- Try to extract filepath from diff header (--- a/path +++ b/path)
    local old_file = diff_text:match("^%-%-%- a/(%S+)") or diff_text:match("^%-%-%- (%S+)")
    if old_file then
      filepath = old_file:gsub("^a/", "")
    end
  end

  if not filepath then
    vim.notify("[mucli] Diff detected but no filepath found", vim.log.levels.WARN)
    return
  end

  -- Check if file exists (original content must be captured)
  local f = io.open(filepath, "r")
  if not f then
    vim.notify("[mucli] Cannot read original file: " .. filepath, vim.log.levels.WARN)
    return
  end
  f:close()

  -- Open diff view
  M.show_diff(filepath, diff_text)
end

return M