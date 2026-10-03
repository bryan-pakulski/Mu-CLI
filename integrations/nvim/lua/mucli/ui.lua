local context = require('mucli.context')
local M = { sources = {}, proposal = nil, histories = {}, sequence = 0,
  conversation_namespace = vim.api.nvim_create_namespace('mucli.conversation'),
  namespace = vim.api.nvim_create_namespace('mucli.review'),
  progress_namespace = vim.api.nvim_create_namespace('mucli.progress') }

local function valid(win)
  return win and vim.api.nvim_win_is_valid(win)
end

local function history(root)
  M.histories[root] = M.histories[root] or { entries = {} }
  return M.histories[root]
end

local function reading_keys(buf)
  vim.keymap.set('n', 'j', 'gj', { buffer = buf, silent = true })
  vim.keymap.set('n', 'k', 'gk', { buffer = buf, silent = true })
end

function M.close_hover()
  if M.hover_autocmd then pcall(vim.api.nvim_del_autocmd, M.hover_autocmd); M.hover_autocmd = nil end
  local win = M.hover_win
  M.hover_win = nil
  if valid(win) then vim.api.nvim_win_close(win, true) end
end

function M.hover(title, text, options)
  M.close_hover()
  options = options or {}
  local source_win = vim.api.nvim_get_current_win()
  local buf = vim.api.nvim_create_buf(false, true)
  vim.bo[buf].bufhidden = 'wipe'
  vim.bo[buf].filetype = 'markdown'
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, vim.split(text, '\n', { plain = true }))
  vim.bo[buf].modifiable = false
  M.hover_win = vim.api.nvim_open_win(buf, options.focus or false, { relative = 'cursor', row = 1, col = 0,
    width = math.max(1, math.min(86, vim.o.columns - 4)),
    height = math.max(1, math.min(18, vim.api.nvim_buf_line_count(buf), vim.o.lines - 5)),
    style = 'minimal', border = 'rounded', title = ' ' .. title .. ' ', focusable = true,
    footer = ' Space mw: focus · Space mj/mk: scroll · q: close ', footer_pos = 'left' })
  vim.wo[M.hover_win].wrap = true
  vim.wo[M.hover_win].linebreak = true
  M.hover_root, M.hover_sources = options.root or M.root, options.sources or M.sources
  M.hover_source_buf = options.source_buf or vim.api.nvim_win_get_buf(source_win)
  reading_keys(buf)
  vim.keymap.set('n', 'q', M.close_hover, { buffer = buf, silent = true })
  vim.keymap.set('n', '<Esc>', M.close_hover, { buffer = buf, silent = true })
  vim.keymap.set('n', 'gd', function() M.goto_source(M.hover_sources, M.hover_root, source_win) end,
    { buffer = buf, desc = 'Open inspected source' })
  local win = M.hover_win
  M.hover_autocmd = vim.api.nvim_create_autocmd({ 'CursorMoved', 'InsertEnter', 'BufLeave' }, {
    callback = function(event)
      if M.scrolling or M.hover_win ~= win or (event.buf == buf and event.event == 'CursorMoved') then return end
      vim.schedule(function()
        if M.hover_win == win and vim.api.nvim_get_current_win() ~= win then M.close_hover() end
      end)
    end })
end

function M.quickfix(sources, root)
  local items = {}
  for _, source in ipairs(sources or {}) do
    table.insert(items, { filename = root .. '/' .. source.path, lnum = source.line,
      text = (source.text or ''):match('[^\n]*') or source.path })
  end
  vim.fn.setqflist({}, ' ', { title = 'Mucli code sources', items = items })
  vim.cmd('copen')
end

local function entry_at_cursor()
  if vim.api.nvim_get_current_buf() ~= M.panel_buf or not M.root then return end
  local row = vim.api.nvim_win_get_cursor(0)[1]
  for _, entry in ipairs(history(M.root).entries) do
    if entry.row and row >= entry.row and row <= entry.last_row then return entry end
  end
end

function M.goto_source(sources, root, source_win)
  local entry = entry_at_cursor()
  sources, root = sources or (entry and entry.sources) or M.sources, root or M.root
  if not root then return end
  local line = vim.api.nvim_get_current_line()
  for _, source in ipairs(sources or {}) do
    local cited = line:match(vim.pesc(source.path) .. ':(%d+)')
    cited = cited and tonumber(cited)
    if cited and cited >= source.line and cited <= (source.end_line or source.line) then
      source_win = source_win or history(root).source_win
      if not valid(source_win) or vim.api.nvim_win_get_config(source_win).relative ~= '' then
        vim.cmd('leftabove vnew')
        source_win = vim.api.nvim_get_current_win()
      end
      vim.api.nvim_set_current_win(source_win)
      vim.cmd.edit(vim.fn.fnameescape(root .. '/' .. source.path))
      vim.api.nvim_win_set_cursor(0, { math.min(cited, vim.api.nvim_buf_line_count(0)), 0 })
      history(root).source_win, history(root).source_buf = source_win, vim.api.nvim_get_current_buf()
      return
    end
  end
  M.quickfix(sources, root)
end

function M.progress(text)
  M.last_progress = text
  if M.progress_buf and vim.api.nvim_buf_is_valid(M.progress_buf) then
    vim.api.nvim_buf_clear_namespace(M.progress_buf, M.progress_namespace, 0, -1)
  end
  if not text then M.progress_buf = nil; return end
  local buf = vim.api.nvim_get_current_buf()
  if vim.bo[buf].buftype == '' then
    M.progress_buf = buf
    vim.api.nvim_buf_set_extmark(buf, M.progress_namespace, vim.api.nvim_win_get_cursor(0)[1] - 1, 0, {
      virt_text = { { '  Mucli · ' .. text, 'Comment' } }, virt_text_pos = 'eol' })
  end
end

function M.close_panel()
  local win = M.panel_buf and vim.fn.bufwinid(M.panel_buf) or -1
  local source_win = M.root and history(M.root).source_win
  if win ~= -1 then
    if #vim.api.nvim_tabpage_list_wins(0) == 1 then
      local source_buf = M.root and history(M.root).source_buf
      vim.api.nvim_win_set_buf(win, source_buf and vim.api.nvim_buf_is_valid(source_buf) and source_buf or vim.api.nvim_create_buf(true, false))
    else vim.api.nvim_win_close(win, true) end
  end
  if valid(source_win) then vim.api.nvim_set_current_win(source_win) end
end

function M.jump_request(direction)
  if not M.root then return end
  local row = vim.api.nvim_win_get_cursor(0)[1]
  local target
  for _, entry in ipairs(history(M.root).entries) do
    if direction > 0 and entry.row > row then target = entry.row; break end
    if direction < 0 and entry.row < row then target = entry.row end
  end
  if target then vim.api.nvim_win_set_cursor(0, { target, 0 }); vim.cmd('normal! zt') end
end

local function panel_buffer()
  if M.panel_buf and vim.api.nvim_buf_is_valid(M.panel_buf) then return M.panel_buf end
  local buf = vim.api.nvim_create_buf(false, true)
  M.panel_buf = buf
  vim.bo[buf].buftype = 'nofile'
  vim.bo[buf].bufhidden = 'hide'
  vim.bo[buf].swapfile = false
  vim.bo[buf].filetype = 'markdown'
  vim.bo[buf].syntax = 'markdown'
  vim.api.nvim_buf_set_name(buf, 'mucli://conversation')
  reading_keys(buf)
  vim.keymap.set('n', 'q', M.close_panel, { buffer = buf, silent = true })
  vim.keymap.set('n', '<Esc>', function()
    local source_win = M.root and history(M.root).source_win
    if valid(source_win) then vim.api.nvim_set_current_win(source_win) end
  end, { buffer = buf, desc = 'Return to code, keep conversation open' })
  vim.keymap.set('n', 'gs', function()
    local entry = entry_at_cursor()
    M.quickfix(entry and entry.sources or M.sources, M.root)
  end, { buffer = buf, desc = 'Mucli sources for this reply' })
  vim.keymap.set('n', 's', function() M.toggle_sources() end, { buffer = buf, desc = 'Expand/collapse sources' })
  vim.keymap.set('n', '?', function()
    M.hover('Mucli keys', 'Enter    Ask a follow-up\nj / k    Move by display line\nCtrl-d/u Scroll half pages\nCtrl-f/b Scroll full pages\ngg / G   First / last line\n[q / ]q  Previous / next request\ngd       Open reference at cursor\ngs       List sources in quickfix\ns        Expand/collapse sources\nr        Retry a failed question\nEsc      Return to code\nq        Close conversation', { focus = true })
  end, { buffer = buf, desc = 'Conversation keyboard shortcuts' })
  vim.keymap.set('n', 'gd', function() M.goto_source() end, { buffer = buf, desc = 'Open source at cursor' })
  vim.keymap.set('n', '<CR>', function() vim.cmd('MucliAsk') end, { buffer = buf, desc = 'Ask a follow-up' })
  vim.keymap.set('n', 'r', function()
    local entry = entry_at_cursor()
    if entry and entry.status == 'failed' and entry.retry then entry.retry() end
  end, { buffer = buf, desc = 'Retry a failed request' })
  vim.keymap.set('n', '[q', function() M.jump_request(-1) end, { buffer = buf, desc = 'Previous Mucli request' })
  vim.keymap.set('n', ']q', function() M.jump_request(1) end, { buffer = buf, desc = 'Next Mucli request' })
  return buf
end

local function render(root, jump)
  local buf = panel_buffer()
  local lines = { 'Mucli · ' .. vim.fn.fnamemodify(root, ':t'), 'Enter to ask · ? for keys', '' }
  local headings = { 1 }
  local entries = history(root).entries
  if #entries == 0 then
    vim.list_extend(lines, { 'No requests yet. Press Enter to ask about your code.', '',
      'History stays here until you exit Neovim.' })
  end
  for _, entry in ipairs(entries) do
    entry.row = #lines + 1
    table.insert(headings, #lines + 1)
    local label = entry.kind == 'explain' and 'Explain' or entry.kind == 'edit' and 'Edit' or 'You'
    if entry.path then label = label .. ' · ' .. entry.path .. ':' .. entry.line end
    table.insert(lines, label)
    table.insert(lines, '')
    if entry.kind ~= 'explain' then
      vim.list_extend(lines, vim.split(entry.question, '\n', { plain = true }))
      table.insert(lines, '')
      table.insert(headings, #lines + 1)
      vim.list_extend(lines, { 'Mucli', '' })
    end
    local text = entry.text or (entry.status == 'pending' and 'Working…' or entry.status)
    if entry.kind == 'edit' and entry.status == 'complete' then text = 'Proposed replacement:\n\n```\n' .. text .. '\n```' end
    local fenced = false
    for _, line in ipairs(vim.split(text, '\n', { plain = true })) do
      if line:match('^%s*```') then fenced = not fenced end
      if not fenced and line:match('^#+%s+') then
        line = line:gsub('^#+%s+', '')
        table.insert(headings, #lines + 1)
      end
      table.insert(lines, line)
    end
    if #(entry.sources or {}) > 0 then
      vim.list_extend(lines, { '', 'Sources (' .. #entry.sources .. ') · gs to open · s to ' .. (entry.expanded_sources and 'hide' or 'expand') })
      if entry.expanded_sources then
        for _, source in ipairs(entry.sources) do table.insert(lines, '- ' .. source.path .. ':' .. source.line) end
      end
    end
    vim.list_extend(lines, { '', '---', '' })
    entry.last_row = #lines
  end
  local win = vim.fn.bufwinid(buf)
  local view = win ~= -1 and vim.api.nvim_win_call(win, vim.fn.winsaveview) or nil
  vim.bo[buf].modifiable = true
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
  vim.api.nvim_buf_clear_namespace(buf, M.conversation_namespace, 0, -1)
  for _, row in ipairs(headings) do
    vim.api.nvim_buf_set_extmark(buf, M.conversation_namespace, row - 1, 0,
      { end_row = row - 1, end_col = #lines[row], hl_group = 'Title' })
  end
  vim.bo[buf].modifiable = false
  if win ~= -1 then
    if jump then
      vim.api.nvim_win_set_cursor(win, { jump.row or 1, 0 })
      vim.api.nvim_win_call(win, function() vim.cmd('normal! zt') end)
    elseif view then
      vim.api.nvim_win_call(win, function() vim.fn.winrestview(view) end)
    end
  end
end

function M.toggle_sources()
  local entry = entry_at_cursor()
  if not entry then return end
  entry.expanded_sources = not entry.expanded_sources
  render(M.root)
end

function M.open(root, focus, preserve_source)
  root = root or M.root or vim.fn.getcwd()
  local state = history(root)
  local previous, current_buf = vim.api.nvim_get_current_win(), vim.api.nvim_get_current_buf()
  if not preserve_source and vim.bo[current_buf].buftype == '' and vim.bo[current_buf].modifiable and vim.api.nvim_buf_get_name(current_buf) ~= '' then
    state.source_win, state.source_buf = previous, current_buf
  end
  M.root = root
  M.sources = state.entries[#state.entries] and state.entries[#state.entries].sources or {}
  render(root)
  local win = vim.fn.bufwinid(M.panel_buf)
  if win == -1 then
    vim.cmd('botright vsplit')
    win = vim.api.nvim_get_current_win()
    vim.api.nvim_win_set_buf(win, M.panel_buf)
    vim.api.nvim_win_set_width(win, math.max(24, math.min(90, math.floor(vim.o.columns * 0.4))))
    vim.wo[win].wrap, vim.wo[win].linebreak = true, true
    vim.wo[win].conceallevel, vim.wo[win].concealcursor = 2, 'nc'
    vim.wo[win].number, vim.wo[win].relativenumber = false, false
    vim.wo[win].signcolumn, vim.wo[win].foldcolumn = 'no', '0'
    vim.wo[win].winfixwidth = true
  end
  vim.api.nvim_set_current_win(focus and win or previous)
end

function M.toggle(root)
  if M.panel_buf and vim.fn.bufwinid(M.panel_buf) ~= -1 and M.root == root then M.close_panel()
  else M.open(root, true) end
end

function M.focus(root)
  if valid(M.hover_win) then vim.api.nvim_set_current_win(M.hover_win)
  else M.open(root or M.root, true) end
end

function M.scroll(direction)
  local current = vim.api.nvim_get_current_win()
  local panel = M.panel_buf and vim.fn.bufwinid(M.panel_buf) or -1
  local preview = M.proposal and M.proposal.preview_win
  local win = current == panel and panel or valid(preview) and preview or valid(M.hover_win) and M.hover_win or panel
  if not valid(win) then return end
  M.scrolling = true
  local ok, err = pcall(vim.api.nvim_win_call, win, function()
    vim.cmd.normal({ args = { vim.api.nvim_replace_termcodes(direction > 0 and '<C-d>' or '<C-u>', true, false, true) }, bang = true })
  end)
  M.scrolling = false
  if not ok then error(err) end
end

function M.begin(root, question, options)
  options = options or {}
  local state = history(root)
  if options.source_buf then state.source_buf, state.source_win = options.source_buf, options.source_win end
  M.sequence = M.sequence + 1
  local entry = { id = M.sequence, root = root, question = question, kind = options.kind or 'question',
    path = options.path, line = options.line, status = 'pending', sources = {} }
  table.insert(state.entries, entry)
  while #state.entries > 50 do table.remove(state.entries, 1) end
  if not options.hover then M.open(root, false, options.source_buf ~= nil); render(root, entry)
  elseif M.root == root and M.panel_buf then render(root) end
  return entry
end

function M.update(entry, text, status)
  if not entry then return end
  entry.text, entry.status = text, status or entry.status
  local state = history(entry.root)
  local bytes = 0
  for _, item in ipairs(state.entries) do bytes = bytes + #item.question + #(item.text or '') end
  while bytes > 256000 and #state.entries > 1 do
    local removed = table.remove(state.entries, 1)
    bytes = bytes - #removed.question - #(removed.text or '')
  end
  if M.root == entry.root and M.panel_buf then render(entry.root) end
end

function M.finish(entry, result)
  local focus = result.context and result.context.focus
  if entry.kind == 'explain' and focus then entry.path, entry.line = focus.path, focus.line end
  entry.sources = {}
  for _, source in ipairs(result.sources or {}) do
    table.insert(entry.sources, { path = source.path, line = source.line,
      end_line = source.end_line or source.line + #(vim.split(source.text or '', '\n', { plain = true })) - 1,
      text = (source.text or ''):sub(1, 200) })
  end
  if M.root == entry.root then M.sources = entry.sources end
  if entry.kind ~= 'edit' and not (result.text or ''):find('%S') then
    M.update(entry, 'Mucli received no answer. Press r to retry this question.', 'failed')
    return false
  end
  M.update(entry, (result.text or ''):sub(1, 100000), 'complete')
  return true
end

function M.clear(root)
  history(root).entries = {}
  if M.root == root then M.sources = {}; if M.panel_buf then render(root) end end
end

-- Context inspection has its own scrollable window; it cannot replace chat history.
function M.panel(title, text, options)
  M.hover(title, text, vim.tbl_extend('force', options or {}, { focus = true }))
end

function M.result(result, root, options)
  M.progress(nil)
  options = options or {}
  local entry = options.entry or M.begin(root, options.question or 'Explain this code', options)
  if not M.finish(entry, result) then return end
  if options.hover then
    local text = result.text or ''
    if #entry.sources > 0 then text = text .. '\n\nSources (' .. #entry.sources .. ') · available in the conversation panel' end
    M.hover('Mucli · explain', text, { root = root, sources = entry.sources })
  end
end

function M.reject()
  local proposal = M.proposal
  if not proposal then return end
  M.proposal = nil
  if vim.api.nvim_buf_is_valid(proposal.snapshot.buf) then
    vim.api.nvim_buf_clear_namespace(proposal.snapshot.buf, M.namespace, 0, -1)
  end
  if vim.api.nvim_buf_is_valid(proposal.preview) then
    vim.api.nvim_buf_delete(proposal.preview, { force = true })
  end
end

function M.apply()
  local proposal = M.proposal
  if not proposal then vim.notify('No Mucli edit to apply.'); return end
  if not context.valid(proposal.snapshot, false) then
    vim.notify('Source buffer changed. Reject this proposal and request a fresh edit.', vim.log.levels.WARN)
    return
  end
  if not vim.bo[proposal.snapshot.buf].modifiable then return end
  -- This explicit user action is the sole write gate for generated edits.
  vim.api.nvim_buf_set_lines(proposal.snapshot.buf, proposal.start, proposal.finish, false, proposal.lines)
  M.reject()
  vim.notify('Mucli edit applied to buffer. Review and save when ready.')
end

function M.review(result, snapshot, selection)
  M.progress(nil)
  if not context.valid(snapshot, false) then
    vim.notify('Source buffer changed while Mucli prepared the edit; proposal discarded.', vim.log.levels.WARN)
    return
  end
  M.reject()
  local lines = vim.split(result.text or '', '\n', { plain = true })
  if lines[#lines] == '' then table.remove(lines) end
  local source_win = vim.fn.bufwinid(snapshot.buf)
  if source_win == -1 then
    vim.cmd('leftabove vsplit')
    vim.api.nvim_win_set_buf(0, snapshot.buf)
    source_win = vim.api.nvim_get_current_win()
  else
    vim.api.nvim_set_current_win(source_win)
  end
  local preview = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_buf_set_lines(preview, 0, -1, false, lines)
  vim.bo[preview].filetype = vim.bo[snapshot.buf].filetype
  vim.bo[preview].buftype = 'nofile'
  vim.bo[preview].bufhidden = 'wipe'
  vim.bo[preview].modifiable = false
  vim.bo[preview].swapfile = false
  vim.api.nvim_create_autocmd('BufWipeout', { buffer = preview, once = true, callback = function()
    vim.schedule(function()
      if M.proposal and M.proposal.preview == preview then M.reject() end
    end)
  end })
  vim.api.nvim_buf_set_extmark(snapshot.buf, M.namespace, selection.start, 0, {
    end_row = selection['end'], hl_group = 'DiffDelete', hl_eol = true })
  local width = math.min(math.max(50, math.floor(vim.o.columns * 0.7)), vim.o.columns - 4)
  local height = math.min(math.max(3, #lines), math.max(3, vim.o.lines - 8))
  local preview_win = vim.api.nvim_open_win(preview, true, { relative = 'editor',
    row = math.max(1, math.floor((vim.o.lines - height) / 2) - 1),
    col = math.max(1, math.floor((vim.o.columns - width) / 2)), width = width, height = height,
    style = 'minimal', border = 'rounded',
    title = ' Mucli · replace lines ' .. (selection.start + 1) .. '-' .. selection['end'] .. ' · a apply / q reject ' })
  vim.wo[preview_win].wrap = false
  vim.cmd('diffthis')
  M.proposal = { snapshot = snapshot, start = selection.start, finish = selection['end'],
    lines = lines, preview = preview, source_win = source_win, preview_win = preview_win }
  vim.keymap.set('n', '<leader>a', M.apply, { buffer = preview, desc = 'Approve Mucli edit' })
  vim.keymap.set('n', 'a', M.apply, { buffer = preview, desc = 'Approve Mucli edit' })
  vim.keymap.set('n', 'q', M.reject, { buffer = preview, desc = 'Reject Mucli edit' })
end

return M
