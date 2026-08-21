local M = {}

local util = require("mucli.util")

local state = {
  tab = nil, original_buf = nil, proposed_buf = nil, original_win = nil, proposed_win = nil,
  proposals = {}, index = 1, callback = nil, decided = false, return_tab = nil,
  can_approve = true, block_reason = nil,
}
M.last = {}
M.pending_events = {}

local function valid_tab(tab) return tab and vim.api.nvim_tabpage_is_valid(tab) end

local function wipe(buf)
  if buf and vim.api.nvim_buf_is_valid(buf) then pcall(vim.api.nvim_buf_delete, buf, { force = true }) end
end

local function reset_windows()
  wipe(state.original_buf)
  wipe(state.proposed_buf)
  state.original_buf, state.proposed_buf = nil, nil
end

local function check_conflict(proposal)
  local buf = util.find_buffer(proposal.path)
  if not buf then return false end
  if proposal.changedtick then
    return vim.api.nvim_buf_get_changedtick(buf) ~= proposal.changedtick
  end
  if not vim.bo[buf].modified then return false end
  local current = util.buffer_text(buf):gsub("\n$", "")
  local original = tostring(proposal.original or ""):gsub("\n$", "")
  return current ~= original
end

local function buffer(name, content, filetype)
  local buf = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_buf_set_name(buf, name)
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, util.lines(content))
  vim.bo[buf].buftype = "nofile"
  vim.bo[buf].bufhidden = "wipe"
  vim.bo[buf].swapfile = false
  vim.bo[buf].filetype = filetype
  vim.bo[buf].modifiable = false
  return buf
end

local function set_keys(buf)
  local opts = { buffer = buf, silent = true }
  vim.keymap.set("n", "a", M.accept, vim.tbl_extend("force", opts, { desc = "Approve MUCLI change" }))
  vim.keymap.set("n", "r", M.reject, vim.tbl_extend("force", opts, { desc = "Reject MUCLI change" }))
  vim.keymap.set("n", "e", M.explain, vim.tbl_extend("force", opts, { desc = "Reject with feedback" }))
  vim.keymap.set("n", "]d", M.next, vim.tbl_extend("force", opts, { desc = "Next MUCLI file" }))
  vim.keymap.set("n", "[d", M.previous, vim.tbl_extend("force", opts, { desc = "Previous MUCLI file" }))
  vim.keymap.set("n", "q", M.reject, vim.tbl_extend("force", opts, { desc = "Reject and close" }))
end

local function render()
  if not valid_tab(state.tab) then return end
  local proposal = state.proposals[state.index]
  reset_windows()
  local relative = util.relative(proposal.path)
  local filetype = util.current_filetype(proposal.path)
  state.original_buf = buffer("mucli://diff/original/" .. relative, proposal.original, filetype)
  state.proposed_buf = buffer("mucli://diff/proposed/" .. relative, proposal.proposed, filetype)
  vim.api.nvim_win_set_buf(state.original_win, state.original_buf)
  vim.api.nvim_win_set_buf(state.proposed_win, state.proposed_buf)
  for _, win in ipairs({ state.original_win, state.proposed_win }) do
    vim.wo[win].diff = true
    vim.wo[win].wrap = false
    vim.wo[win].number = true
    vim.wo[win].cursorline = true
  end
  local position = ("%d/%d"):format(state.index, #state.proposals)
  vim.wo[state.original_win].winbar = " MUCLI · ORIGINAL · " .. relative .. " · " .. position
  local warning = proposal.conflict and " · CONFLICT: unsaved buffer differs" or ""
  if not state.can_approve then warning = warning .. " · APPROVAL BLOCKED" end
  vim.wo[state.proposed_win].winbar = " MUCLI · PROPOSED · a approve · r reject · e feedback · ]d next" .. warning
  set_keys(state.original_buf)
  set_keys(state.proposed_buf)
  vim.api.nvim_set_current_win(state.proposed_win)
end

function M.open(proposals, callback, opts)
  opts = opts or {}
  if not proposals or #proposals == 0 then
    if callback then callback("reject", "No renderable modifications were supplied") end
    return
  end
  if valid_tab(state.tab) then
    local previous_callback = state.callback
    M.close(false)
    state.callback, state.proposals = nil, {}
    if previous_callback then previous_callback("reject", "Diff review was superseded by a newer proposal") end
  end
  state.return_tab = vim.api.nvim_get_current_tabpage()
  state.proposals = proposals
  state.index = 1
  state.callback = callback
  state.decided = false
  state.can_approve = opts.can_approve ~= false
  state.block_reason = opts.block_reason
  M.last = vim.deepcopy(proposals)
  for _, proposal in ipairs(state.proposals) do proposal.conflict = proposal.conflict or check_conflict(proposal) end

  vim.cmd("tabnew")
  state.tab = vim.api.nvim_get_current_tabpage()
  state.original_win = vim.api.nvim_get_current_win()
  if require("mucli.config").get().diff.layout == "horizontal" then vim.cmd("belowright split")
  else vim.cmd("belowright vsplit") end
  state.proposed_win = vim.api.nvim_get_current_win()
  render()
end

function M.close(return_to_previous)
  local tab, previous = state.tab, state.return_tab
  reset_windows()
  state.tab, state.original_win, state.proposed_win = nil, nil, nil
  if valid_tab(tab) then
    pcall(vim.api.nvim_set_current_tabpage, tab)
    pcall(vim.cmd, "tabclose")
  end
  if return_to_previous ~= false and valid_tab(previous) then pcall(vim.api.nvim_set_current_tabpage, previous) end
end

local function decide(decision, reason)
  if state.decided then return end
  state.decided = true
  local callback = state.callback
  M.close(true)
  state.callback, state.proposals = nil, {}
  if callback then callback(decision, reason) end
end

function M.accept()
  if not state.can_approve then
    util.notify(state.block_reason or "This proposal cannot be approved safely", vim.log.levels.ERROR)
    return
  end
  for _, proposal in ipairs(state.proposals) do
    proposal.conflict = proposal.conflict or check_conflict(proposal)
    if proposal.conflict then
      util.notify("Approval blocked: an unsaved buffer differs from the version used for this diff", vim.log.levels.ERROR)
      return
    end
  end
  decide("accept")
end

function M.reject() decide("reject", "User rejected the proposed change") end

function M.explain()
  vim.ui.input({ prompt = "Feedback for MUCLI: " }, function(reason)
    if reason and reason ~= "" then decide("reject", reason) end
  end)
end

function M.next()
  if #state.proposals < 2 then return end
  state.index = state.index % #state.proposals + 1
  render()
end

function M.previous()
  if #state.proposals < 2 then return end
  state.index = (state.index - 2) % #state.proposals + 1
  render()
end

function M.is_open() return not not valid_tab(state.tab) end

function M.review_approval(modifications, callback, opts)
  local proposals = {}
  for _, modification in ipairs(modifications or {}) do
    if modification.can_render_diff ~= false and modification.filename then
      proposals[#proposals + 1] = {
        path = util.normalize_path(modification.filename),
        original = tostring(modification.original_content or ""),
        proposed = tostring(modification.modified_content or ""),
        summary = modification.summary,
        source = "server_approval",
      }
    end
  end
  M.pending_events = {}
  M.open(proposals, callback, opts)
end

function M.review_editor(proposal, callback)
  proposal.source = "editor_tool"
  M.open({ proposal }, callback)
end

function M.capture_event(event)
  if not event or not event.filename then return end
  M.pending_events[#M.pending_events + 1] = {
    path = util.normalize_path(event.filename), original = tostring(event.original or ""),
    proposed = tostring(event.new or ""), source = "server_event",
  }
  if #M.pending_events > 20 then table.remove(M.pending_events, 1) end
  M.last = vim.deepcopy(M.pending_events)
end

function M.open_last()
  local proposals = #M.last > 0 and vim.deepcopy(M.last) or vim.deepcopy(M.pending_events)
  if #proposals == 0 then util.notify("No MUCLI diff is available", vim.log.levels.WARN); return end
  M.open(proposals, function() end)
end

function M.parse_unified_diff(text)
  local hunks, current = {}, nil
  for line in (tostring(text or "") .. "\n"):gmatch("(.-)\n") do
    local old_start, old_count, new_start, new_count = line:match("^@@ %-(%d+),?(%d*) %+(%d+),?(%d*) @@")
    if old_start then
      current = {
        old_start = tonumber(old_start), old_count = old_count == "" and 1 or tonumber(old_count),
        new_start = tonumber(new_start), new_count = new_count == "" and 1 or tonumber(new_count), lines = {},
      }
      hunks[#hunks + 1] = current
    elseif current and (line:sub(1, 1) == " " or line:sub(1, 1) == "+" or line:sub(1, 1) == "-") then
      current.lines[#current.lines + 1] = { kind = line:sub(1, 1), text = line:sub(2) }
    end
  end
  return hunks
end

function M.apply_hunks(original, hunks)
  if not hunks or #hunks == 0 then return nil, "No unified diff hunks were found" end
  local out, cursor = {}, 1
  for _, hunk in ipairs(hunks or {}) do
    while cursor < hunk.old_start do out[#out + 1] = original[cursor]; cursor = cursor + 1 end
    for _, line in ipairs(hunk.lines) do
      if line.kind == " " then
        if original[cursor] ~= line.text then return nil, ("Context mismatch at line %d"):format(cursor) end
        out[#out + 1] = original[cursor]
        cursor = cursor + 1
      elseif line.kind == "-" then
        if original[cursor] ~= line.text then return nil, ("Removal mismatch at line %d"):format(cursor) end
        cursor = cursor + 1
      elseif line.kind == "+" then
        out[#out + 1] = line.text
      end
    end
  end
  while cursor <= #original do out[#out + 1] = original[cursor]; cursor = cursor + 1 end
  return out
end

function M.show_diff(path, diff_text)
  local buf = util.find_buffer(path)
  local original
  if buf then original = vim.api.nvim_buf_get_lines(buf, 0, -1, false)
  else original = vim.fn.readfile(path) end
  local modified, err = M.apply_hunks(original, M.parse_unified_diff(diff_text))
  if not modified then util.notify(err, vim.log.levels.ERROR); return end
  local target = buf or vim.fn.bufadd(path)
  vim.fn.bufload(target)
  local tick = vim.api.nvim_buf_get_changedtick(target)
  M.review_editor({
    path = util.normalize_path(path), original = table.concat(original, "\n"),
    proposed = table.concat(modified, "\n"), bufnr = target,
    changedtick = tick,
  }, function(decision)
    if decision == "accept" and vim.api.nvim_buf_is_valid(target)
      and vim.api.nvim_buf_get_changedtick(target) == tick then
      vim.api.nvim_buf_set_lines(target, 0, -1, false, modified)
    end
  end)
end

return M
