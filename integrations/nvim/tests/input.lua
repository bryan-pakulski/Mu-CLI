-- Questions must retain their code context across asynchronous input UIs.
local function run()
  vim.opt.rtp:append('integrations/nvim')
  vim.o.hidden = true
  local repo, root = vim.fn.getcwd(), vim.fn.tempname()
  vim.fn.mkdir(root, 'p')
  vim.fn.writefile({}, root .. '/.mucliignore')
  vim.env.PYTHONPATH = repo
  local mucli, ui, client = require('mucli'), require('mucli.ui'), require('mucli.client')
  mucli.setup({ workspace = root,
    command = { 'python3', repo .. '/integrations/nvim/tests/fake_service.py' } })
  local messages, requests = {}, {}
  vim.notify = function(message) messages[#messages + 1] = message end
  local get = client.get
  client.get = function(...)
    local connection = get(...)
    if not connection.input_test then
      connection.input_test = true
      local request = connection.request
      connection.request = function(method, params, callback)
        if method == 'analyze' or method == 'edit' then
          requests[#requests + 1] = { method = method, params = vim.deepcopy(params) }
        end
        return request(method, params, callback)
      end
    end
    return connection
  end
  local pending, prompts = nil, 0
  vim.ui.input = function(_, callback) pending = callback; prompts = prompts + 1 end
  local function confirm(answer)
    assert(pending, 'Expected a question prompt')
    local callback = pending
    pending = nil
    callback(answer)
  end
  local function answered(question)
    assert(vim.wait(5000, function()
      local state = ui.histories[root]
      local entry = state and state.entries[#state.entries]
      return entry and entry.question == question and entry.status == 'complete'
    end), 'Question did not complete: ' .. question .. '\n' .. table.concat(messages, '\n'))
  end
  vim.cmd.edit(vim.fn.fnameescape(root .. '/main.py'))
  local source = vim.api.nvim_get_current_buf()
  vim.api.nvim_buf_set_lines(source, 0, -1, false, { 'def main():', '    return 1' })
  vim.api.nvim_win_set_cursor(0, { 2, 4 })
  vim.cmd('enew')
  local empty = vim.api.nvim_get_current_buf()
  vim.cmd('MucliChat')
  local panel = vim.fn.bufwinid(ui.panel_buf)
  assert(not ui.histories[root].source_buf, 'An unnamed split is not a source buffer')
  vim.cmd('MucliAsk')
  confirm('Ask from a fresh panel')
  answered('Ask from a fresh panel')
  assert(#messages == 0, table.concat(messages, '\n'))
  assert(requests[1].params.path == root .. '/main.py')
  assert(requests[1].params.text == 'def main():\n    return 1')
  assert(requests[1].params.row == 1, 'Hidden source must retain its code cursor')
  assert(ui.histories[root].source_buf == source)

  -- A saved source may be replaced by an unnamed buffer. Validate before use.
  ui.histories[root].source_buf, ui.histories[root].source_win = empty, nil
  mucli.source_buf = nil
  vim.cmd('MucliAsk Recover an invalid saved source')
  answered('Recover an invalid saved source')
  assert(requests[#requests].params.path == root .. '/main.py')

  -- Opening the input chooses the source. Changing focus before its callback
  -- cannot redirect the question, subsequent follow-ups, or a selected edit.
  vim.cmd('MucliAsk')
  vim.cmd('leftabove vnew')
  local other_win = vim.api.nvim_get_current_win()
  vim.cmd.edit(vim.fn.fnameescape(root .. '/other.py'))
  local other = vim.api.nvim_get_current_buf()
  vim.api.nvim_buf_set_lines(other, 0, -1, false, { 'other = True' })
  confirm('Keep the original source')
  answered('Keep the original source')
  assert(requests[#requests].params.path == root .. '/main.py')
  assert(ui.histories[root].source_buf == source, 'Opening the panel must not replace the chosen source')
  vim.api.nvim_set_current_win(panel)
  vim.cmd('MucliAsk Follow up after input')
  answered('Follow up after input')
  assert(requests[#requests].params.path == root .. '/main.py')

  vim.api.nvim_set_current_win(other_win)
  vim.api.nvim_win_set_buf(other_win, source)
  vim.cmd('2MucliEdit')
  vim.api.nvim_win_set_buf(other_win, other)
  confirm('Replace the return')
  assert(vim.wait(5000, function() return ui.proposal ~= nil end))
  assert(requests[#requests].params.path == root .. '/main.py')
  assert(requests[#requests].params.selection.start == 1 and requests[#requests].params.selection['end'] == 2)
  assert(ui.proposal.snapshot.buf == source and ui.proposal.start == 1)
  assert(vim.api.nvim_buf_get_lines(source, 1, 2, false)[1] == '    return 1', 'An edit still requires approval')
  ui.reject()

  -- Cancelling or submitting whitespace must not send a model request.
  vim.api.nvim_set_current_win(panel)
  local count = #requests
  vim.cmd('MucliAsk')
  confirm(nil)
  vim.cmd('MucliAsk')
  confirm('   ')
  assert(#requests == count)

  -- Never redirect a pending request to another file if its source is deleted.
  vim.cmd('MucliAsk')
  vim.api.nvim_buf_delete(source, { force = true })
  confirm('The source was closed')
  assert(#requests == count)
  assert(#messages == 1 and messages[1]:find('Open a named source file', 1, true))
  assert(not messages[1]:find('stack traceback', 1, true))

  -- An explicitly excluded file cannot redirect a selection to another source.
  vim.api.nvim_set_current_win(other_win)
  vim.cmd.edit(vim.fn.fnameescape(root .. '/.env'))
  vim.api.nvim_buf_set_lines(0, 0, -1, false, { 'PRIVATE_PLACEHOLDER' })
  local before = prompts
  vim.cmd('1MucliEdit')
  assert(prompts == before and #requests == count)

  -- Fresh workspace with no eligible source: concise guidance, no prompt/trace,
  -- and no accidental use of a file from the previous workspace.
  vim.api.nvim_set_current_win(panel)
  local elsewhere = vim.fn.tempname()
  vim.fn.mkdir(elsewhere, 'p')
  mucli.config.workspace = nil
  vim.cmd.cd(vim.fn.fnameescape(elsewhere))
  ui.open(elsewhere, true)
  before = prompts
  vim.cmd('MucliAsk')
  vim.cmd('MucliAsk No open source')
  vim.cmd('MucliExplain')
  vim.cmd('MucliEdit')
  vim.cmd('MucliContext')
  assert(prompts == before and #requests == count, 'Do not borrow sources from another workspace')
  assert(#ui.histories[elsewhere].entries == 0)
  for _, message in ipairs(messages) do
    assert(message == 'Open a named source file in this workspace, then try Mucli again.', message)
  end
  vim.cmd('enew')
  assert(require('mucli.context').root(vim.api.nvim_get_current_buf(), mucli.config) == elsewhere)
  mucli.cancel()
  client.stop()
  vim.cmd.cd(vim.fn.fnameescape(repo))
  vim.fn.delete(root, 'rf')
  vim.fn.delete(elsewhere, 'rf')
  print('Mucli input source recovery, async questions/edits, cancellation, and workspace isolation passed')
end
local ok, err = xpcall(run, debug.traceback)
if not ok then io.stderr:write(err .. '\n'); vim.cmd('cquit 1') else vim.cmd('qa!') end
