local M = { cached = {}, recent = {}, definitions = {}, sequence = 0, revisions = {}, attached = {}, epoch = 0 }

function M.other_revision(buf)
  local value = 0
  for other, revision in pairs(M.revisions) do if other ~= buf then value = value + revision end end
  return value
end

function M.buffer(buf)
  if not M.attached[buf] then
    M.attached[buf] = true
    M.epoch = M.epoch + 1
    M.revisions[buf] = M.epoch
    vim.api.nvim_buf_attach(buf, false, {
      on_lines = function(_, changed)
        M.epoch = M.epoch + 1
        M.revisions[changed] = M.epoch
        M.cached[changed] = nil
      end,
      on_detach = function(_, changed) M.forget(changed) end,
    })
  end
  local tick = vim.api.nvim_buf_get_changedtick(buf)
  local saved = M.cached[buf]
  if not saved or saved.tick ~= tick then
    local lines = vim.api.nvim_buf_get_lines(buf, 0, -1, false)
    saved = { text = table.concat(lines, '\n'), lines = lines, tick = tick }
    M.cached[buf] = saved
  end
  return saved
end

local function receiver(line)
  return line:match('([%w_]+)%.[%w_]*$') or line:match('([%w_]+)%s*%([^()]*$')
end

local scope_types = { function_definition = true, function_declaration = true, method_definition = true,
  method_declaration = true, class_definition = true, class_declaration = true, function_item = true,
  impl_item = true, interface_declaration = true, arrow_function = true, method = true }

function M.scopes(buf, row, col)
  local ok, node = pcall(vim.treesitter.get_node, { bufnr = buf, pos = { row, col } })
  if not ok then return {} end
  local result = {}
  while node and #result < 3 do
    if scope_types[node:type()] then
      local first, _, last = node:range()
      table.insert(result, 1, { line = first + 1, count = math.min(8, last - first + 1) })
    end
    node = node:parent()
  end
  return result
end

function M.touch(buf)
  M.sequence = M.sequence + 1
  M.recent[buf] = M.sequence
end

function M.forget(buf)
  M.cached[buf], M.recent[buf], M.definitions[buf] = nil, nil, nil
  M.revisions[buf], M.attached[buf] = nil, nil
end

function M.root(buf, config)
  if config.workspace then return vim.fn.fnamemodify(config.workspace, ':p'):gsub('([^/])/$', '%1') end
  local name = vim.api.nvim_buf_get_name(buf)
  local cwd = vim.fn.getcwd()
  if name == '' or vim.bo[buf].buftype ~= '' then return cwd end
  local marker = vim.fs.find({ '.git', '.mucliignore', 'pyproject.toml', 'package.json', 'Cargo.toml', 'go.mod' },
    { upward = true, path = vim.fs.dirname(name) })[1]
  if marker then return vim.fs.dirname(marker) end
  return name:sub(1, #cwd + 1) == cwd .. '/' and cwd or vim.fs.dirname(name)
end

function M.eligible(buf, config)
  if not vim.api.nvim_buf_is_valid(buf) or not vim.api.nvim_buf_is_loaded(buf) then return false end
  if vim.bo[buf].buftype ~= '' or not vim.bo[buf].modifiable then return false end
  if vim.tbl_contains(config.exclude_filetypes, vim.bo[buf].filetype) then return false end
  local name = vim.api.nvim_buf_get_name(buf)
  if name == '' or vim.fn.fnamemodify(name, ':t'):match('^%.env') then return false end
  local bytes = vim.api.nvim_buf_get_offset(buf, vim.api.nvim_buf_line_count(buf))
  return bytes >= 0 and bytes <= config.max_buffer_bytes
end

function M.capture(buf, position)
  local pos = position or vim.api.nvim_win_get_cursor(0)
  local saved = M.buffer(buf)
  local lines = saved.lines
  local row = math.max(1, math.min(pos[1], #lines))
  pos = { row, math.max(0, math.min(pos[2], #lines[row])) }
  local offset = pos[2]
  for index = 1, pos[1] - 1 do offset = offset + #lines[index] + 1 end
  return { buf = buf, row = pos[1] - 1, col = pos[2], text = saved.text,
    offset = offset, tick = saved.tick, dependency_stamp = M.other_revision(buf) }
end

-- Only allow typing at the original cursor, with both surrounding sides intact.
-- This lets a response catch up with typing without accepting unrelated stale edits.
function M.inserted(before, after)
  if before.buf ~= after.buf then return nil end
  if before.dependency_stamp ~= after.dependency_stamp then return nil end
  local added = #after.text - #before.text
  if added < 0 or after.offset ~= before.offset + added then return nil end
  local typed = after.text:sub(before.offset + 1, after.offset)
  if before.text:sub(1, before.offset) .. typed .. before.text:sub(before.offset + 1) ~= after.text then return nil end
  return typed
end

function M.snapshot(buf, config, position)
  if not M.eligible(buf, config) then error('Buffer is unnamed, excluded, or too large for Mucli.') end
  local captured = M.capture(buf, position)
  local text = captured.text
  local params = {
    path = vim.api.nvim_buf_get_name(buf), text = text, row = captured.row, col = captured.col,
    filetype = vim.bo[buf].filetype, version = buf * 1000000000 + captured.tick, buffers = {}, diagnostics = {}, definitions = {},
    scope_ranges = M.scopes(buf, captured.row, captured.col),
  }
  if config.share_open_buffers then
    local buffers = vim.api.nvim_list_bufs()
    table.sort(buffers, function(a, b) return (M.recent[a] or 0) > (M.recent[b] or 0) end)
    local remaining, root = 4000000, M.root(buf, config)
    for _, other in ipairs(buffers) do
      if other ~= buf and M.eligible(other, config) and #params.buffers < 12 and M.root(other, config) == root then
        local saved = M.buffer(other)
        if #saved.text <= remaining then
          table.insert(params.buffers, { path = vim.api.nvim_buf_get_name(other), text = saved.text, version = other * 1000000000 + saved.tick })
          remaining = remaining - #saved.text
        end
      end
    end
  end
  local definitions = M.definitions[buf]
  local symbol = receiver(M.buffer(buf).lines[captured.row + 1]:sub(1, captured.col))
  if definitions and ((definitions.tick == captured.tick and definitions.row == captured.row and definitions.col == captured.col) or
    (symbol and symbol == definitions.receiver and math.abs(definitions.row - captured.row) <= 3
      and vim.uv.now() < definitions.expires)) then params.definitions = definitions.items end
  local diagnostics = vim.diagnostic.get(buf)
  table.sort(diagnostics, function(a, b) return math.abs(a.lnum - captured.row) < math.abs(b.lnum - captured.row) end)
  for _, diagnostic in ipairs(diagnostics) do
    if #params.diagnostics >= 20 then break end
    table.insert(params.diagnostics, { line = diagnostic.lnum + 1, message = diagnostic.message, severity = diagnostic.severity })
  end
  captured.dependency_stamp = M.other_revision(buf)
  return params, captured
end

-- Fetch definitions while idle. Completion uses cached results and never waits
-- for an LSP round trip. Requests are bounded and discarded after buffer changes.
function M.prefetch(buf, config)
  if not M.eligible(buf, config) or vim.api.nvim_get_current_buf() ~= buf then return end
  local snapshot = M.capture(buf)
  local existing = M.definitions[buf]
  if existing and existing.tick == snapshot.tick and existing.row == snapshot.row and existing.col == snapshot.col then return end
  local cache = { tick = snapshot.tick, row = snapshot.row, col = snapshot.col, items = {}, expires = vim.uv.now() + 3000 }
  M.definitions[buf] = cache
  local positions = {}
  local lines = M.buffer(buf).lines
  local line = lines[snapshot.row + 1]:sub(1, snapshot.col)
  cache.receiver = receiver(line)
  local start = line:find('[%w_]+%.?$')
  if start then table.insert(positions, { snapshot.row, start - 1 }) end
  for row = snapshot.row, math.max(0, snapshot.row - 80), -1 do
    local value = lines[row + 1]
    for col in value:gmatch('():%s*[%u][%w_]*') do
      local offset = value:find('[%u]', col)
      if offset then table.insert(positions, { row, offset - 1 }) end
      if #positions >= 3 then break end
    end
    if #positions >= 3 then break end
  end
  local root, seen, sent = M.root(buf, config), {}, 0
  for _, lsp in ipairs(vim.lsp.get_clients({ bufnr = buf })) do
    for _, method in ipairs({ 'textDocument/typeDefinition', 'textDocument/definition' }) do
      local supported = vim.fn.has('nvim-0.11') == 1 and lsp:supports_method(method, buf)
        or vim.fn.has('nvim-0.11') == 0 and lsp.supports_method(method, { bufnr = buf })
      if supported then
        for _, position in ipairs(positions) do
          if sent >= 6 then return end
          sent = sent + 1
          local encoding, character = lsp.offset_encoding or 'utf-16', position[2]
          if encoding ~= 'utf-8' then
            local ok, index = pcall(vim.str_utfindex, lines[position[1] + 1], encoding, position[2], false)
            if ok then character = index
            else
              local utf32, utf16 = vim.str_utfindex(lines[position[1] + 1], position[2])
              character = encoding == 'utf-16' and utf16 or utf32
            end
          end
          local args = { textDocument = { uri = vim.uri_from_bufnr(buf) },
            position = { line = position[1], character = character } }
          local callback = function(err, result)
            if err or not result or M.definitions[buf] ~= cache or not vim.api.nvim_buf_is_valid(buf)
              or vim.api.nvim_buf_get_changedtick(buf) ~= snapshot.tick then return end
            if result.uri or result.targetUri then result = { result } end
            for _, location in ipairs(result) do
              local uri = location.uri or location.targetUri
              local range = location.targetSelectionRange or location.range or location.targetRange
              if uri and uri:sub(1, 7) == 'file://' and range then
                local path = vim.uri_to_fname(uri)
                local key = path .. ':' .. range.start.line
                if path:sub(1, #root + 1) == root .. '/' and not seen[key] and #cache.items < 6 then
                  seen[key] = true
                  table.insert(cache.items, { path = path, line = range.start.line + 1 })
                end
              end
            end
          end
          if vim.fn.has('nvim-0.11') == 1 then lsp:request(method, args, callback, buf)
          else lsp.request(method, args, callback, buf) end
        end
      end
    end
  end
end

function M.valid(snapshot, cursor)
  if not vim.api.nvim_buf_is_valid(snapshot.buf) then return false end
  if snapshot.dependency_stamp and snapshot.dependency_stamp ~= M.other_revision(snapshot.buf) then return false end
  if vim.api.nvim_buf_get_changedtick(snapshot.buf) ~= snapshot.tick then return false end
  if cursor then
    local pos = vim.api.nvim_win_get_cursor(0)
    return vim.api.nvim_get_current_buf() == snapshot.buf and pos[1] - 1 == snapshot.row and pos[2] == snapshot.col
  end
  return true
end

return M
