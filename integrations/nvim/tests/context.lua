-- Exercise context, LSP caching, dependency invalidation, and focused UI in Neovim.
local function run()
  vim.opt.rtp:append('integrations/nvim')
  vim.opt.virtualedit = 'onemore'
  local repo, root = vim.fn.getcwd(), vim.fn.tempname()
  vim.fn.mkdir(root, 'p')
  vim.env.PYTHONPATH = repo
  local mucli = require('mucli')
  mucli.setup({ workspace = root, command = { 'python3', repo .. '/integrations/nvim/tests/fake_service.py' },
    analysis = { provider = { name = 'fake', model = 'fake' } } })
  local dependency = vim.api.nvim_create_buf(true, false)
  vim.api.nvim_buf_set_name(dependency, root .. '/contracts.py')
  local lines = {}
  for _ = 1, 100 do table.insert(lines, '# header') end
  vim.list_extend(lines, { 'class Invoice:', '    def total(self, *, tax_included):', '        return 42' })
  vim.api.nvim_buf_set_lines(dependency, 0, -1, false, lines)
  vim.cmd.edit(vim.fn.fnameescape(root .. '/main.py'))
  local buf = vim.api.nvim_get_current_buf()
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, {
    'from contracts import Invoice', '', 'def price(invoice: Invoice):', '    return invoice.' })
  vim.api.nvim_win_set_cursor(0, { 4, 19 })
  local context = require('mucli.context')
  local params, snapshot = context.snapshot(buf, mucli.config)
  assert(params.buffers[1].text:find('def total', 1, true), 'Related buffers must include definitions beyond line 80')
  local connection = require('mucli.client').get(root, mucli.config)
  params.provider = { name = 'fake', model = 'fake' }
  local response
  connection.request('context', params, function(message) response = message end)
  assert(vim.wait(5000, function() return response ~= nil end), 'Context request timed out')
  assert(response.result, vim.inspect(response))
  local found = false
  for _, source in ipairs(response.result.sources) do
    if source.path == 'contracts.py' and source.text:find('def total', 1, true) then found = true end
  end
  assert(found, 'Unsaved dependency must reach backend retrieval')
  assert(connection.versions[params.path] == params.version, 'Buffer versions must be synchronized')

  local original = vim.lsp.get_clients
  vim.lsp.get_clients = function()
    return { { offset_encoding = 'utf-16', supports_method = function() return true end,
      request = function(_, _, _, callback)
        vim.schedule(function() callback(nil, { { uri = vim.uri_from_fname(root .. '/contracts.py'),
          range = { start = { line = 100, character = 0 }, ['end'] = { line = 103, character = 0 } } } }) end)
      end } }
  end
  context.definitions[buf] = nil
  context.prefetch(buf, mucli.config)
  assert(vim.wait(1000, function() return #context.definitions[buf].items > 0 end), 'LSP definitions must be cached asynchronously')
  params = context.snapshot(buf, mucli.config)
  assert(params.definitions[1].line == 101)
  vim.api.nvim_win_set_cursor(0, { 1, 0 })
  params = context.snapshot(buf, mucli.config)
  assert(#params.definitions == 0, 'Moving to another line without editing must not reuse unrelated LSP definitions')
  vim.api.nvim_win_set_cursor(0, { 4, 4 })
  params = context.snapshot(buf, mucli.config)
  assert(#params.definitions == 0, 'Moving to another symbol on the same line must not reuse old definitions')
  context.prefetch(buf, mucli.config)
  assert(context.definitions[buf].col == 4, 'A column change must refresh LSP context')
  vim.api.nvim_win_set_cursor(0, { 4, 19 })
  vim.lsp.get_clients = original

  vim.api.nvim_buf_set_lines(dependency, 101, 102, false, { '    def renamed(self):' })
  assert(not context.valid(snapshot, true), 'An unsaved dependency edit must invalidate the snapshot')

  local ui = require('mucli.ui')
  ui.hover('Explanation', 'A short explanation')
  assert(vim.api.nvim_win_get_config(ui.hover_win).relative ~= '', 'Short explanations must float, not split')
  local _, fresh = context.snapshot(buf, mucli.config)
  ui.review({ text = '    return 42' }, fresh, { start = 3, ['end'] = 4 })
  assert(vim.api.nvim_win_get_config(ui.proposal.preview_win).relative == 'editor')
  assert(vim.api.nvim_buf_line_count(ui.proposal.preview) == 1, 'Review must focus on the replacement')
  assert(vim.api.nvim_buf_get_lines(buf, 3, 4, false)[1] == '    return invoice.', 'Preview cannot mutate source')
  ui.reject()
  mucli.cancel()
  require('mucli.client').stop()
  vim.fn.delete(root, 'rf')
  print('Mucli workspace context, LSP definitions, stale dependencies, and focused review passed')
end
local ok, err = xpcall(run, debug.traceback)
if not ok then io.stderr:write(err .. '\n'); vim.cmd('cquit 1') else vim.cmd('qa!') end
