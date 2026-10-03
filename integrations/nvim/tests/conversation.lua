-- User-facing navigation, history, and source-context behavior in real Neovim.
local function run()
  vim.opt.rtp:append('integrations/nvim')
  vim.o.columns, vim.o.lines = 140, 40
  vim.o.hidden = true
  local repo, root = vim.fn.getcwd(), vim.fn.tempname()
  vim.fn.mkdir(root, 'p')
  vim.env.PYTHONPATH = repo
  vim.fn.writefile({ 'def original():', '    return 1' }, root .. '/main.py')
  vim.fn.writefile({ 'def dependency():', '    return 2' }, root .. '/older.py')
  local mucli = require('mucli')
  mucli.setup({ workspace = root, command = { 'python3', repo .. '/integrations/nvim/tests/fake_service.py' },
    analysis = { provider = { name = 'fake', model = 'fake' } } })
  vim.cmd.edit(vim.fn.fnameescape(root .. '/main.py'))
  local source_win, source_buf = vim.api.nvim_get_current_win(), vim.api.nvim_get_current_buf()
  local ui = require('mucli.ui')
  local long = {}
  for i = 1, 90 do table.insert(long, 'Explanation line ' .. i) end
  long = table.concat(long, '\n')
  local first = ui.begin(root, 'First question', { source_buf = source_buf, source_win = source_win, path = 'main.py', line = 1 })
  ui.finish(first, { text = long, sources = { { path = 'older.py', line = 2 } } })
  local panel = vim.fn.bufwinid(ui.panel_buf)
  assert(panel ~= -1, 'A request must open the conversation panel')
  assert(vim.api.nvim_win_get_position(panel)[2] > vim.api.nvim_win_get_position(source_win)[2], 'Conversation must be on the RHS')
  assert(vim.api.nvim_get_current_win() == source_win, 'Incoming responses must preserve code focus')
  vim.api.nvim_win_set_cursor(panel, { 1, 0 })
  local before = vim.api.nvim_win_call(panel, vim.fn.winsaveview)
  vim.cmd('MucliScrollDown')
  local after = vim.api.nvim_win_call(panel, vim.fn.winsaveview)
  assert(after.topline > before.topline, 'Scroll shortcut must move the panel from code')
  assert(vim.api.nvim_get_current_win() == source_win, 'Scrolling must retain code focus')
  vim.cmd('MucliScrollUp')
  assert(vim.api.nvim_win_call(panel, vim.fn.winsaveview).topline < after.topline)
  vim.cmd('MucliFocus')
  assert(vim.api.nvim_get_current_win() == panel)
  assert(vim.fn.maparg('j', 'n', false, true).rhs == 'gj', 'j must move by display line in wrapped text')

  vim.api.nvim_set_current_win(source_win)
  local second = ui.begin(root, 'Second question', { source_buf = source_buf, source_win = source_win })
  ui.finish(second, { text = 'Second answer', sources = { { path = 'main.py', line = 1 } } })
  vim.api.nvim_set_current_win(panel)
  vim.api.nvim_win_set_cursor(panel, { first.row, 0 })
  ui.jump_request(1)
  assert(vim.api.nvim_win_get_cursor(panel)[1] == second.row, ']q must jump to the next request')
  ui.jump_request(-1)
  assert(vim.api.nvim_win_get_cursor(panel)[1] == first.row)
  local view = vim.fn.winsaveview()
  ui.update(second, 'Second answer updated', 'complete')
  assert(vim.fn.winsaveview().topline == view.topline, 'Updates must not pull the reader away from old requests')
  local saved = table.concat(vim.api.nvim_buf_get_lines(ui.panel_buf, 0, -1, false), '\n')
  ui.close_panel()
  vim.cmd('MucliChat')
  panel = vim.fn.bufwinid(ui.panel_buf)
  assert(table.concat(vim.api.nvim_buf_get_lines(ui.panel_buf, 0, -1, false), '\n') == saved, 'Closing must preserve history')
  assert(not saved:find('Inspected sources', 1, true), 'The transcript must not dump source lists')
  assert(not saved:find('- older.py:2', 1, true), 'Sources should be collapsed until requested')

  -- Source links belong to the historical reply, not just the most recent result.
  vim.api.nvim_win_set_cursor(panel, { first.row, 0 })
  ui.toggle_sources()
  for row, line in ipairs(vim.api.nvim_buf_get_lines(ui.panel_buf, 0, -1, false)) do
    if line == '- older.py:2' then vim.api.nvim_win_set_cursor(panel, { row, 0 }); break end
  end
  ui.goto_source()
  assert(vim.api.nvim_buf_get_name(0) == root .. '/older.py', 'Old reply must retain its own inspected sources')
  assert(vim.api.nvim_win_get_cursor(0)[1] == 2)
  vim.api.nvim_win_set_buf(source_win, source_buf)
  saved = table.concat(vim.api.nvim_buf_get_lines(ui.panel_buf, 0, -1, false), '\n')

  -- Popups can be scrolled without focus, focused for native navigation, and closed.
  ui.hover('Long explanation', long)
  local hover = ui.hover_win
  vim.cmd('MucliScrollDown')
  assert(vim.api.nvim_win_call(hover, vim.fn.winsaveview).topline > 1)
  assert(vim.api.nvim_get_current_win() == source_win)
  vim.cmd('MucliFocus')
  assert(vim.api.nvim_get_current_win() == hover)
  vim.cmd('normal! 5j')
  vim.wait(30)
  assert(vim.api.nvim_win_is_valid(hover), 'Popup must remain open while reading it')
  ui.close_hover()
  vim.api.nvim_set_current_win(source_win)

  -- Inspecting workspace context must never overwrite the conversation.
  vim.cmd('MucliContext')
  assert(vim.wait(5000, function() return ui.hover_win and vim.api.nvim_win_is_valid(ui.hover_win) end))
  assert(table.concat(vim.api.nvim_buf_get_lines(ui.panel_buf, 0, -1, false), '\n') == saved)
  ui.close_hover()
  vim.api.nvim_set_current_win(source_win)

  -- Explain now records its question/reply in the same history by default.
  local count = #ui.histories[root].entries
  vim.cmd('MucliExplain')
  assert(vim.wait(5000, function() return #ui.histories[root].entries > count and ui.histories[root].entries[#ui.histories[root].entries].status == 'complete' end))
  assert(not ui.hover_win, 'Default Explain must use the RHS conversation')
  assert(ui.histories[root].entries[#ui.histories[root].entries].kind == 'explain')
  local transcript = table.concat(vim.api.nvim_buf_get_lines(ui.panel_buf, 0, -1, false), '\n')
  assert(not transcript:find('Explain this code.', 1, true), 'Automatic Explain instructions must not clutter history')
  assert(not transcript:find('## You', 1, true), 'Transcript labels must be readable without raw Markdown headings')
  assert(vim.api.nvim_get_current_win() == source_win)
  vim.cmd('MucliFocus')
  vim.cmd('MucliAsk Follow up from the panel')
  assert(vim.wait(5000, function()
    local entry = ui.histories[root].entries[#ui.histories[root].entries]
    return entry.question == 'Follow up from the panel' and entry.status == 'complete'
  end), 'Follow-up must use source code rather than the conversation buffer')
  assert(ui.histories[root].source_buf == source_buf)

  -- Workspace histories survive switching, but do not mix with each other.
  local other = root .. '/other'
  vim.fn.mkdir(other, 'p')
  ui.open(other, true)
  assert(#ui.histories[other].entries == 0)
  ui.open(root, true)
  assert(#ui.histories[root].entries == count + 2)
  vim.api.nvim_win_close(source_win, true)
  vim.cmd('MucliAsk Follow up with the source hidden')
  assert(vim.wait(5000, function()
    local entry = ui.histories[root].entries[#ui.histories[root].entries]
    return entry.question == 'Follow up with the source hidden' and entry.status == 'complete'
  end), 'Hidden source buffers must not use the conversation cursor as a code position')
  vim.cmd('MucliClear')
  assert(#ui.histories[root].entries == 0)
  assert(not table.concat(vim.api.nvim_buf_get_lines(ui.panel_buf, 0, -1, false), '\n'):find('First question', 1, true))
  ui.close_panel()
  assert(vim.api.nvim_get_current_buf() == source_buf, 'Closing the last panel window must restore the source')
  mucli.cancel()
  require('mucli.client').stop()
  vim.fn.delete(root, 'rf')
  print('Mucli RHS history, scrolling, focus, historical sources, and follow-up checks passed')
end
local ok, err = xpcall(run, debug.traceback)
if not ok then io.stderr:write(err .. '\n'); vim.cmd('cquit 1') else vim.cmd('qa!') end
