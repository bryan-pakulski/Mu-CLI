-- A blank provider response must fail visibly and be retryable from the panel.
local function run()
  vim.opt.rtp:append('integrations/nvim')
  local repo, root = vim.fn.getcwd(), vim.fn.tempname()
  vim.fn.mkdir(root, 'p')
  vim.env.PYTHONPATH = repo
  local mucli = require('mucli')
  mucli.setup({ workspace = root, command = { 'python3', repo .. '/integrations/nvim/tests/fake_service.py' },
    analysis = { provider = { name = 'fake', model = 'fake' } } })
  vim.cmd.edit(vim.fn.fnameescape(root .. '/main.py'))
  local source = vim.api.nvim_get_current_buf()
  vim.api.nvim_buf_set_lines(source, 0, -1, false, { 'def main():', '    return 1' })
  local ui = require('mucli.ui')
  vim.cmd('MucliAsk SIMULATE_EMPTY_REPLY')
  assert(vim.wait(5000, function()
    local state = ui.histories[root]
    return state and state.entries[1] and state.entries[1].status == 'failed'
  end), 'Two empty attempts must produce a visible failed request')
  local first = ui.histories[root].entries[1]
  assert(first.text:find('no answer', 1, true))
  assert(first.text:find('Press r', 1, true))
  vim.cmd('MucliFocus')
  vim.api.nvim_win_set_cursor(0, { first.row, 0 })
  local retry = vim.fn.maparg('r', 'n', false, true).callback
  assert(type(retry) == 'function', 'The panel needs a retry action')
  retry()
  assert(vim.wait(5000, function()
    local entries = ui.histories[root].entries
    return #entries == 2 and entries[2].status == 'complete'
  end), 'Retry must resubmit the failed question without another input prompt')
  local second = ui.histories[root].entries[2]
  assert(second.question == first.question and second.text == 'return 42')
  assert(first.status == 'failed', 'Keep the earlier failure in request history')
  assert(vim.api.nvim_buf_get_lines(source, 1, 2, false)[1] == '    return 1')

  local empty = ui.begin(root, 'Unexpected empty service reply')
  ui.result({ text = ' \n\n', sources = {} }, root, { entry = empty })
  assert(empty.status == 'failed' and empty.text:find('no answer', 1, true), 'Client must guard against empty success packets too')
  local edit = ui.begin(root, 'Delete selected lines', { kind = 'edit' })
  ui.finish(edit, { text = '', sources = {} })
  assert(edit.status == 'complete', 'An empty replacement is valid for an explicitly requested deletion')
  mucli.cancel()
  require('mucli.client').stop()
  vim.fn.delete(root, 'rf')
  print('Mucli empty-answer failure, panel retry, and response guard passed')
end
local ok, err = xpcall(run, debug.traceback)
if not ok then io.stderr:write(err .. '\n'); vim.cmd('cquit 1') else vim.cmd('qa!') end
