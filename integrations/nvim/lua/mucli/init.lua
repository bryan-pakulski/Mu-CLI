local client = require('mucli.client')
local context = require('mucli.context')
local ui = require('mucli.ui')
local M = {}

local defaults = {
  command = { 'python3', '-m', 'mu.editor' },
  workspace = nil,
  max_buffer_bytes = 500000,
  share_open_buffers = true,
  exclude_filetypes = { 'help', 'netrw', 'TelescopePrompt', 'gitcommit', 'gitrebase' },
  analysis = { timeout_ms = 180000, provider = { name = 'ollama', model = 'deepseek-v4.1-flash' } },
}

function M.cancel()
  ui.progress(nil)
  if M.analysis_client then M.analysis_client.cancel(M.analysis_id) end
  if M.analysis_entry then ui.update(M.analysis_entry, 'Cancelled.', 'cancelled') end
  M.analysis_entry = nil
  M.analysis_id = nil
end

local function active_root()
  local buf = vim.api.nvim_get_current_buf()
  if buf == ui.panel_buf and ui.root then return ui.root end
  if ui.hover_win == vim.api.nvim_get_current_win() and ui.hover_root then return ui.hover_root end
  return context.root(buf, M.config)
end

local function source_buffer()
  local current = vim.api.nvim_get_current_buf()
  if context.eligible(current, M.config) then return current end
  -- An explicitly selected but excluded file must not redirect a ranged edit.
  if vim.bo[current].buftype == '' and vim.api.nvim_buf_get_name(current) ~= '' then return end
  local root = active_root()
  local function eligible(buf)
    if not buf or not context.eligible(buf, M.config) then return false end
    local path = vim.api.nvim_buf_get_name(buf)
    local prefix = root:gsub('/$', '') .. '/'
    return context.root(buf, M.config) == root and path:sub(1, #prefix) == prefix
  end
  if ui.hover_win == vim.api.nvim_get_current_win() and eligible(ui.hover_source_buf) then return ui.hover_source_buf end
  local state = ui.histories[root]
  if state and state.source_win and vim.api.nvim_win_is_valid(state.source_win) then
    local buf = vim.api.nvim_win_get_buf(state.source_win)
    if eligible(buf) then return buf end
  end
  if state and eligible(state.source_buf) then return state.source_buf end
  if eligible(M.source_buf) then return M.source_buf end
  -- A fresh chat can be opened from a dashboard/empty split before any request.
  -- Recover a real source in this workspace, preferring visible, recent buffers.
  local visible = {}
  for _, win in ipairs(vim.api.nvim_tabpage_list_wins(0)) do visible[vim.api.nvim_win_get_buf(win)] = true end
  local buffers = vim.api.nvim_list_bufs()
  table.sort(buffers, function(a, b)
    local a_visible, b_visible = visible[a] or false, visible[b] or false
    if a_visible ~= b_visible then return a_visible end
    return (context.recent[a] or 0) > (context.recent[b] or 0)
  end)
  for _, buf in ipairs(buffers) do if eligible(buf) then return buf end end
end

local function require_source(buf)
  if buf and context.eligible(buf, M.config) then return true end
  vim.notify('Open a named source file in this workspace, then try Mucli again.', vim.log.levels.WARN)
  return false
end

local function workspace()
  local buf = source_buffer()
  return buf and context.root(buf, M.config) or active_root()
end

local function source_snapshot(buf)
  local win = vim.api.nvim_get_current_buf() == buf and vim.api.nvim_get_current_win() or vim.fn.bufwinid(buf)
  local position = win ~= -1 and vim.api.nvim_win_get_cursor(win) or vim.api.nvim_buf_get_mark(buf, '"')
  return context.snapshot(buf, M.config, position)
end

local function analyze(method, instruction, opts, source)
  local buf = source or source_buffer()
  if not require_source(buf) then return end
  local win = vim.api.nvim_get_current_buf() == buf and vim.api.nvim_get_current_win() or vim.fn.bufwinid(buf)
  local ok, params, snapshot = pcall(source_snapshot, buf)
  if not ok then vim.notify(tostring(params), vim.log.levels.WARN); return end
  M.source_buf = buf
  params.provider = M.config.analysis.provider
  params.instruction = instruction
  params.action = opts and opts.explain and 'explain' or method
  if opts and opts.range and opts.range > 0 then
    params.selection = { start = opts.line1 - 1, ['end'] = opts.line2 }
    if method == 'analyze' then
      params.instruction = instruction .. '\nFocus on lines ' .. opts.line1 .. '-' .. opts.line2
    end
  elseif method == 'edit' then
    params.selection = { start = snapshot.row, ['end'] = snapshot.row + 1 }
  end
  local root = context.root(buf, M.config)
  local started, connection = pcall(client.get, root, M.config)
  if not started then vim.notify(tostring(connection), vim.log.levels.ERROR); return end
  if M.analysis_client then M.analysis_client.cancel(M.analysis_id) end
  if M.analysis_entry then ui.update(M.analysis_entry, 'Cancelled by a newer request.', 'cancelled') end
  M.analysis_client = connection
  local entry = ui.begin(root, instruction, { hover = opts and opts.hover,
    kind = method == 'edit' and 'edit' or opts and opts.explain and 'explain' or 'question',
    path = vim.fn.fnamemodify(params.path, ':.'), line = snapshot.row + 1,
    source_buf = buf, source_win = win ~= -1 and win or nil })
  entry.retry = function() analyze(method, instruction, opts, buf) end
  M.analysis_entry = entry
  ui.progress(method == 'edit' and 'Preparing edit for review…' or 'Investigating code…')
  M.analysis_id = connection.request(method, params, function(message)
    if M.analysis_entry ~= entry then return end
    if message.event then
      ui.progress(message.message or 'Investigating…')
      ui.update(entry, message.message or 'Investigating…')
      return
    end
    M.analysis_entry, M.analysis_id = nil, nil
    ui.progress(nil)
    if message.error then
      ui.update(entry, 'Request failed: ' .. message.error .. '\n\nPress r to retry this question.', 'failed')
      vim.notify(message.error, vim.log.levels.WARN)
    elseif message.result then
      ui.result(message.result, root, { entry = entry,
        hover = opts and opts.hover and context.valid(snapshot, true) })
      if method == 'edit' then ui.review(message.result, snapshot, params.selection) end
    end
  end)
end

local function search(query)
  local root = workspace()
  local ok, connection = pcall(client.get, root, M.config)
  if not ok then vim.notify(tostring(connection), vim.log.levels.ERROR); return end
  if M.analysis_client then M.analysis_client.cancel(M.analysis_id) end
  if M.analysis_entry then ui.update(M.analysis_entry, 'Cancelled by a search.', 'cancelled'); M.analysis_entry = nil end
  ui.progress(nil)
  M.analysis_client = connection
  M.analysis_id = connection.request('search', { query = query }, function(message)
    if message.error then vim.notify(message.error, vim.log.levels.ERROR)
    elseif message.result then ui.quickfix(message.result.sources, root) end
  end)
end

function M.setup(options)
  if vim.fn.has('nvim-0.10') == 0 then error('Mucli requires Neovim 0.10 or newer.') end
  M.cancel()
  client.stop()
  context.cached, context.definitions, context.recent = {}, {}, {}
  M.config = vim.tbl_deep_extend('force', vim.deepcopy(defaults), options or {})
  local group = vim.api.nvim_create_augroup('Mucli', { clear = true })
  local updates = {}
  vim.api.nvim_create_autocmd({ 'BufEnter', 'TextChanged', 'TextChangedI', 'TextChangedP', 'BufWritePost', 'CursorHold', 'CursorHoldI', 'LspAttach' }, {
    group = group, callback = function(event)
      if event.event == 'LspAttach' then context.definitions[event.buf] = nil end
      context.touch(event.buf)
      updates[event.buf] = (updates[event.buf] or 0) + 1
      local version = updates[event.buf]
      vim.defer_fn(function()
        if updates[event.buf] ~= version or not vim.api.nvim_buf_is_valid(event.buf) then return end
        context.prefetch(event.buf, M.config)
        if M.config.share_open_buffers and event.buf == vim.api.nvim_get_current_buf() then
          local ok, params = pcall(context.snapshot, event.buf, M.config)
          if ok then
            local started, connection = pcall(client.get, context.root(event.buf, M.config), M.config)
            if started then connection.sync(params) end
          end
        end
      end, 120)
    end,
  })
  vim.api.nvim_create_autocmd('BufWipeout', { group = group, callback = function(event)
    local path = vim.api.nvim_buf_get_name(event.buf)
    for _, connection in pairs(client.jobs) do
      if connection.versions[path] then connection.notify('sync', { path = path, closed = true }); connection.versions[path] = nil end
    end
    context.forget(event.buf)
  end })
  vim.api.nvim_create_autocmd('VimLeavePre', { group = group, callback = client.stop })
  local function command(name, fn, opts)
    vim.api.nvim_create_user_command('Mucli' .. name, fn, vim.tbl_extend('force', { force = true }, opts or {}))
  end
  command('Explain', function(opts)
    opts.hover, opts.explain = opts.bang, true
    analyze('analyze', 'Explain this code.', opts)
  end, { range = true, bang = true })
  command('Chat', function() ui.toggle(workspace()) end)
  command('Focus', function() ui.focus(workspace()) end)
  command('ScrollUp', function() ui.scroll(-1) end)
  command('ScrollDown', function() ui.scroll(1) end)
  local function prompt_action(method, opts)
    if opts.args ~= '' then analyze(method, opts.args, opts); return end
    -- Input plugins temporarily change the current window/buffer. Keep the
    -- source chosen when the user invoked Ask/Edit, including from the panel.
    local buf = source_buffer()
    if not require_source(buf) then return end
    vim.ui.input({ prompt = method == 'edit' and 'Edit selection: ' or 'Mucli: ' }, function(answer)
      if answer and answer:find('%S') then analyze(method, answer, opts, buf) end
    end)
  end
  command('Ask', function(opts) prompt_action('analyze', opts) end, { nargs = '*', range = true })
  command('Edit', function(opts) prompt_action('edit', opts) end, { nargs = '*', range = true })
  command('Clear', function()
    M.cancel()
    local root = workspace()
    ui.clear(root)
    client.get(root, M.config).request('reset', {}, function() vim.notify('Mucli investigation cleared.') end)
  end)
  command('Fix', function(opts)
    analyze('edit', 'Fix diagnostics affecting the selected lines. Preserve behavior unrelated to these diagnostics.', opts)
  end, { range = true })
  command('Search', function(opts) search(opts.args) end, { nargs = '+' })
  command('References', function()
    local attached = vim.lsp.get_clients({ bufnr = 0 })
    for _, lsp in ipairs(attached) do
      local supports
      if vim.fn.has('nvim-0.11') == 1 then
        supports = lsp:supports_method('textDocument/references', 0)
      else
        supports = lsp.supports_method('textDocument/references', { bufnr = 0 })
      end
      if supports then vim.lsp.buf.references(); return end
    end
    search(vim.fn.expand('<cword>'))
  end)
  command('Sources', function() ui.quickfix(ui.sources, ui.root or vim.fn.getcwd()) end)
  command('Apply', ui.apply)
  command('Reject', ui.reject)
  command('Cancel', M.cancel)
  command('Status', function()
    vim.notify('Mucli analysis: ' .. M.config.analysis.provider.name .. '/' ..
      M.config.analysis.provider.model .. '\nWorkspace: ' .. (workspace() or ''))
  end)
  command('Context', function()
    local buf = source_buffer()
    if not require_source(buf) then return end
    local ok, params = pcall(source_snapshot, buf)
    if not ok then vim.notify(tostring(params), vim.log.levels.WARN); return end
    params.provider = M.config.analysis.provider
    local root = context.root(buf, M.config)
    client.get(root, M.config).request('context', params, function(message)
      if message.error then vim.notify(message.error, vim.log.levels.WARN); return end
      local value = message.result
      if not value then return end
      local lines = { 'Workspace: ' .. root, 'Indexed files: ' .. value.index.files .. (value.index.limited and ' (bounded coverage)' or ''),
        'Context: ' .. value.context_tokens .. ' estimated tokens · ' .. value.context_ms .. 'ms',
        'Recent edits: ' .. value.recent_edits, '', '## Enclosing declarations', value.scope,
        '', '## Imports', value.imports, '', '## Selected sources (`gd` opens source)' }
      for _, source in ipairs(value.sources) do
        table.insert(lines, '\n' .. source.path .. ':' .. source.line .. ' · ' .. source.reason .. (source.unsaved and ' · buffer overlay' or ''))
        table.insert(lines, '```\n' .. source.text .. '\n```')
      end
      ui.panel('Mucli context', table.concat(lines, '\n'), { root = root, sources = value.sources, source_buf = buf })
    end)
  end)
end

return M
