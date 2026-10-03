local M = { jobs = {}, sequence = 0 }

function M.stop()
  for _, client in pairs(M.jobs) do
    vim.fn.jobstop(client.job)
  end
  M.jobs = {}
end

function M.get(root, config)
  if M.jobs[root] then return M.jobs[root] end
  local client = { pending = {}, tail = '', error = '', versions = {} }
  local command = vim.deepcopy(config.command)
  vim.list_extend(command, { '--workspace', root })
  client.job = vim.fn.jobstart(command, {
    cwd = root,
    on_stdout = function(_, data)
      local chunks = vim.split(client.tail .. table.concat(data, '\n'), '\n', { plain = true })
      client.tail = table.remove(chunks) or ''
      for _, line in ipairs(chunks) do
        local ok, message = pcall(vim.json.decode, line)
        if ok and type(message) == 'table' then
          local cb = client.pending[message.id]
          if cb then
            if not message.event then client.pending[message.id] = nil end
            cb(message)
          end
        end
      end
    end,
    on_stderr = function(_, data)
      client.error = (client.error .. table.concat(data, '\n')):sub(-1000)
    end,
    on_exit = function(_, code)
      if M.jobs[root] == client then M.jobs[root] = nil end
      local pending = client.pending
      client.pending = {}
      for _, cb in pairs(pending) do
        cb({ error = 'Editor service exited (' .. code .. '). Check :MucliStatus and your Python installation.' })
      end
    end,
  })
  if client.job <= 0 then error('Cannot start Mucli. Check setup().command and Python installation.') end
  M.jobs[root] = client

  function client.cancel(id)
    if not id then return end
    client.pending[id] = nil
    pcall(vim.fn.chansend, client.job, vim.json.encode({ method = 'cancel', params = { id = id } }) .. '\n')
  end

  function client.notify(method, params)
    return pcall(vim.fn.chansend, client.job, vim.json.encode({ method = method, params = params }) .. '\n')
  end

  function client.sync(params)
    local active = {}
    for _, buffer in ipairs(vim.list_extend({ params }, params.buffers or {})) do
      active[buffer.path] = true
      if buffer.text and client.versions[buffer.path] ~= buffer.version then
        local ok = client.notify('sync', { path = buffer.path, text = buffer.text, version = buffer.version })
        if ok then client.versions[buffer.path] = buffer.version end
      end
    end
    for path, _ in pairs(client.versions) do
      if not active[path] then
        client.notify('sync', { path = path, closed = true })
        client.versions[path] = nil
      end
    end
  end

  function client.request(method, params, callback)
    if params.text and params.version then
      client.sync(params)
      -- Buffer contents were sent once, independently of analysis requests.
      params = vim.deepcopy(params)
      params.text = nil
      for _, buffer in ipairs(params.buffers or {}) do buffer.text = nil end
    end
    M.sequence = M.sequence + 1
    local id = M.sequence
    client.pending[id] = callback
    local ok = pcall(vim.fn.chansend, client.job, vim.json.encode({ id = id, method = method, params = params }) .. '\n')
    if not ok then
      client.pending[id] = nil
      callback({ error = 'Mucli service is unavailable; retry to reconnect.' })
    else
      vim.defer_fn(function()
        if client.pending[id] then
          client.cancel(id)
          callback({ error = 'Mucli request timed out.' })
        end
      end, config.analysis.timeout_ms)
    end
    return id
  end
  return client
end

return M
