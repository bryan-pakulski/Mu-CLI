local M = {}

local util = require("mucli.util")

M.SYSTEM_PROMPT = [[
NEOVIM EDITOR BRIDGE

The user is working through a live Neovim client. Editor context in the user
message may contain unsaved text and is authoritative over the filesystem.
Use nvim_get_buffer before reasoning about an open or modified file. Prefer
nvim_propose_edit with the returned changedtick when changing an open buffer;
the user receives a native diff and the accepted edit remains unsaved until
they write it. Never replace a buffer after a changedtick conflict. Use
nvim_publish_diagnostics for structured review findings and nvim_open_location
when navigating would help. Keep editor actions focused and reversible.
]]

M.DEFINITIONS = {
  {
    name = "nvim_get_buffer",
    description = "Read current unsaved Neovim buffer text, optionally within a 1-based line range.",
    parameters = { type = "object", properties = {
      file_path = { type = "string", description = "Workspace-relative or absolute path. Defaults to the active editor buffer." },
      start_line = { type = "integer", description = "First 1-based line, inclusive." },
      end_line = { type = "integer", description = "Last 1-based line, inclusive." },
    } },
  },
  {
    name = "nvim_list_buffers",
    description = "List loaded file buffers with path, filetype, modified state, changedtick and line count.",
    parameters = { type = "object", properties = {} },
  },
  {
    name = "nvim_get_selection",
    description = "Read the most recent visual selection, including its path and exact line range.",
    parameters = { type = "object", properties = {} },
  },
  {
    name = "nvim_get_diagnostics",
    description = "Read Neovim/LSP diagnostics for one open buffer.",
    parameters = { type = "object", properties = {
      file_path = { type = "string", description = "Buffer path; defaults to the active editor buffer." },
    } },
  },
  {
    name = "nvim_get_workspace_state",
    description = "Get workspace root, active buffer, cursor, editor mode, attached LSP clients and staged MUCLI context.",
    parameters = { type = "object", properties = {} },
  },
  {
    name = "nvim_get_document_symbols",
    description = "Request document symbols from LSP for an open buffer.",
    parameters = { type = "object", properties = {
      file_path = { type = "string", description = "Buffer path; defaults to the active editor buffer." },
    } },
  },
  {
    name = "nvim_open_location",
    description = "Open a workspace file in the user's editor and reveal a 1-based line and optional 0-based byte column.",
    parameters = { type = "object", properties = {
      file_path = { type = "string" }, line = { type = "integer" }, column = { type = "integer" },
    }, required = { "file_path" } },
  },
  {
    name = "nvim_publish_diagnostics",
    description = "Publish review hints into Neovim's diagnostic UI. Lines are 1-based.",
    parameters = { type = "object", properties = {
      file_path = { type = "string" },
      diagnostics = { type = "array", items = { type = "object", properties = {
        line = { type = "integer" }, end_line = { type = "integer" }, column = { type = "integer" },
        severity = { type = "string", enum = { "error", "warning", "info", "hint" } },
        message = { type = "string" }, code = { type = "string" },
      }, required = { "line", "message" } } },
    }, required = { "file_path", "diagnostics" } },
  },
  {
    name = "nvim_propose_edit",
    description = "Propose complete replacement text for an open buffer. Requires expected_changedtick and waits for native user diff approval; accepted text is applied to the buffer but not written to disk.",
    execution_kind = "mutate",
    parameters = { type = "object", properties = {
      file_path = { type = "string" }, new_content = { type = "string" },
      expected_changedtick = { type = "integer" }, summary = { type = "string" },
    }, required = { "file_path", "new_content", "expected_changedtick" } },
  },
}

M.diagnostic_namespace = vim.api.nvim_create_namespace("mucli_agent_diagnostics")

local function editor_window()
  local ok, panel = pcall(require, "mucli.chat.panel")
  if ok and panel.editor_window then
    local win = panel.editor_window()
    if win then return win end
  end
  return vim.api.nvim_get_current_win()
end

local function active_buffer()
  local win = editor_window()
  return vim.api.nvim_win_is_valid(win) and vim.api.nvim_win_get_buf(win) or vim.api.nvim_get_current_buf()
end

local function resolve_path(path)
  local root = util.workspace_root()
  path = path and path ~= "" and path or vim.api.nvim_buf_get_name(active_buffer())
  if path == "" then return nil, "No file buffer is active" end
  if not util.is_absolute(path) then path = root .. "/" .. path end
  path = util.normalize_path(path)
  if not require("mucli.config").get().workspace.allow_secret_paths
    and util.is_secret_path(path) then
    return nil, "Secret-path policy blocked editor access: " .. path
  end
  if not require("mucli.config").get().workspace.allow_outside and not util.is_within(path, root) then
    return nil, "Path is outside the configured workspace: " .. path
  end
  return path
end

local function path_allowed(path)
  local workspace = require("mucli.config").get().workspace
  if not workspace.allow_secret_paths and util.is_secret_path(path) then return false end
  return workspace.allow_outside or util.is_within(path, util.workspace_root())
end

local function resolve_buffer(path, load)
  local resolved, err = resolve_path(path)
  if not resolved then return nil, nil, err end
  local buf = util.find_buffer(resolved)
  if buf and not vim.api.nvim_buf_is_loaded(buf) then
    if not load then return nil, resolved, "File is not loaded in Neovim" end
    local loaded = pcall(vim.fn.bufload, buf)
    if not loaded or not vim.api.nvim_buf_is_loaded(buf) then return nil, resolved, "Could not load file in Neovim" end
  end
  if not buf and load then
    buf = vim.fn.bufadd(resolved)
    local loaded = pcall(vim.fn.bufload, buf)
    if not loaded or not vim.api.nvim_buf_is_loaded(buf) then return nil, resolved, "Could not load file in Neovim" end
  end
  if not buf then return nil, resolved, "File is not loaded in Neovim" end
  return buf, resolved
end

local function ok(data) return { ok = true, data = data } end
local function failure(message) return { ok = false, error = tostring(message) } end

function M.get_buffer(args)
  local buf, path, err = resolve_buffer(args.file_path, true)
  if not buf then return failure(err) end
  local count = vim.api.nvim_buf_line_count(buf)
  local first = util.clamp(tonumber(args.start_line) or 1, 1, math.max(1, count))
  local last = util.clamp(tonumber(args.end_line) or count, first, math.max(first, count))
  local text = table.concat(vim.api.nvim_buf_get_lines(buf, first - 1, last, false), "\n")
  local max_chars = require("mucli.config").get().context.max_file_chars
  local truncated
  text, truncated = util.truncate(text, max_chars)
  return ok({
    path = util.relative(path), filetype = vim.bo[buf].filetype, content = text,
    start_line = first, end_line = last, total_lines = count,
    changedtick = vim.api.nvim_buf_get_changedtick(buf), modified = vim.bo[buf].modified,
    truncated = truncated,
  })
end

function M.list_buffers()
  local values = {}
  for _, buf in ipairs(vim.api.nvim_list_bufs()) do
    local path = vim.api.nvim_buf_get_name(buf)
    if vim.api.nvim_buf_is_loaded(buf) and path ~= ""
      and vim.bo[buf].buftype == "" and path_allowed(path) then
      values[#values + 1] = {
        bufnr = buf, path = util.relative(path), filetype = vim.bo[buf].filetype,
        modified = vim.bo[buf].modified, changedtick = vim.api.nvim_buf_get_changedtick(buf),
        line_count = vim.api.nvim_buf_line_count(buf),
      }
    end
  end
  return ok({ buffers = values })
end

function M.get_selection()
  local context = require("mucli.context")
  local selection = context.latest_selection()
  if not selection then return failure("No visual selection is available") end
  if selection.path and selection.path ~= "" and not path_allowed(selection.path) then
    return failure("Selection is outside the configured workspace")
  end
  return ok(selection)
end

local flatten_symbols

local function document_symbols_async(event)
  local args = event.tool_args or {}
  local buf, path, err = resolve_buffer(args.file_path, false)
  if not buf then M.post_result(event.call_id, nil, err); return end
  if #vim.lsp.get_clients({ bufnr = buf }) == 0 then
    M.post_result(event.call_id, ok({ path = util.relative(path), symbols = {} }))
    return
  end
  local finished = false
  local function complete(result, failure_message)
    if finished then return end
    finished = true
    M.post_result(event.call_id, result, failure_message)
  end
  vim.defer_fn(function()
    complete(nil, "Document symbol request timed out after 5 seconds")
  end, 5000)
  vim.lsp.buf_request_all(buf, "textDocument/documentSymbol", {
    textDocument = { uri = vim.uri_from_bufnr(buf) },
  }, function(responses)
    local symbols = {}
    for _, response in pairs(responses or {}) do flatten_symbols(response.result, symbols, 0) end
    complete(ok({ path = util.relative(path), symbols = symbols }))
  end)
end

function M.get_diagnostics(args)
  local buf, path, err = resolve_buffer(args.file_path, false)
  if not buf then return failure(err) end
  local values = {}
  local names = { [1] = "error", [2] = "warning", [3] = "info", [4] = "hint" }
  for _, diagnostic in ipairs(vim.diagnostic.get(buf)) do
    values[#values + 1] = {
      line = diagnostic.lnum + 1, end_line = (diagnostic.end_lnum or diagnostic.lnum) + 1,
      column = diagnostic.col or 0, severity = names[diagnostic.severity] or "info",
      message = diagnostic.message, source = diagnostic.source, code = diagnostic.code,
    }
  end
  return ok({ path = util.relative(path), diagnostics = values })
end

function M.workspace_state()
  local win = editor_window()
  local buf = vim.api.nvim_win_get_buf(win)
  local cursor = vim.api.nvim_win_get_cursor(win)
  local clients = {}
  for _, lsp in ipairs(vim.lsp.get_clients({ bufnr = buf })) do clients[#clients + 1] = lsp.name end
  local path = vim.api.nvim_buf_get_name(buf)
  local allowed = path == "" or path_allowed(path)
  return ok({
    root = util.workspace_root(), active_file = allowed and util.relative(path) or nil,
    active_file_outside_workspace = not allowed,
    cursor = { line = cursor[1], column = cursor[2] }, mode = vim.api.nvim_get_mode().mode,
    filetype = vim.bo[buf].filetype, modified = vim.bo[buf].modified,
    changedtick = vim.api.nvim_buf_get_changedtick(buf), lsp_clients = clients,
    staged_context = require("mucli.context").summary(),
  })
end

flatten_symbols = function(symbols, out, depth)
  for _, symbol in ipairs(symbols or {}) do
    if #out >= 200 then return end
    local range = symbol.range or (symbol.location and symbol.location.range) or {}
    local start = range.start or {}
    out[#out + 1] = {
      name = symbol.name, kind = symbol.kind, line = (start.line or 0) + 1,
      character = start.character or 0, depth = depth,
    }
    flatten_symbols(symbol.children, out, depth + 1)
  end
end

function M.document_symbols(args)
  local buf, path, err = resolve_buffer(args.file_path, false)
  if not buf then return failure(err) end
  local params = { textDocument = { uri = vim.uri_from_bufnr(buf) } }
  local responses = vim.lsp.buf_request_sync(buf, "textDocument/documentSymbol", params, 1500) or {}
  local symbols = {}
  for _, response in pairs(responses) do flatten_symbols(response.result, symbols, 0) end
  return ok({ path = util.relative(path), symbols = symbols })
end

function M.open_location(args)
  local path, err = resolve_path(args.file_path)
  if not path then return failure(err) end
  local win = editor_window()
  vim.api.nvim_set_current_win(win)
  vim.cmd("edit " .. vim.fn.fnameescape(path))
  local count = vim.api.nvim_buf_line_count(0)
  local line = util.clamp(tonumber(args.line) or 1, 1, math.max(1, count))
  local text = (vim.api.nvim_buf_get_lines(0, line - 1, line, false)[1] or "")
  local column = util.clamp(tonumber(args.column) or 0, 0, #text)
  vim.api.nvim_win_set_cursor(0, { line, column })
  vim.cmd("normal! zz")
  return ok({ path = util.relative(path), line = line, column = column })
end

function M.publish_diagnostics(args)
  local buf, path, err = resolve_buffer(args.file_path, false)
  if not buf then return failure(err) end
  local severity = {
    error = vim.diagnostic.severity.ERROR, warning = vim.diagnostic.severity.WARN,
    info = vim.diagnostic.severity.INFO, hint = vim.diagnostic.severity.HINT,
  }
  local values = {}
  local line_count = vim.api.nvim_buf_line_count(buf)
  for _, item in ipairs(args.diagnostics or {}) do
    local line = util.clamp(tonumber(item.line) or 1, 1, math.max(1, line_count))
    values[#values + 1] = {
      lnum = line - 1, end_lnum = util.clamp(tonumber(item.end_line) or line, line, line_count) - 1,
      col = math.max(0, tonumber(item.column) or 0), severity = severity[item.severity] or severity.hint,
      message = tostring(item.message or ""), source = "MUCLI", code = item.code,
    }
  end
  vim.diagnostic.set(M.diagnostic_namespace, buf, values, {})
  return ok({ path = util.relative(path), published = #values })
end

local handlers = {
  nvim_get_buffer = M.get_buffer,
  nvim_list_buffers = M.list_buffers,
  nvim_get_selection = M.get_selection,
  nvim_get_diagnostics = M.get_diagnostics,
  nvim_get_workspace_state = M.workspace_state,
  nvim_get_document_symbols = M.document_symbols,
  nvim_open_location = M.open_location,
  nvim_publish_diagnostics = M.publish_diagnostics,
}

function M.execute(name, args)
  local handler = handlers[name]
  if not handler then return failure("Unknown editor tool: " .. tostring(name)) end
  local success, result = pcall(handler, args or {})
  return success and result or failure(result)
end

function M.post_result(call_id, result, err)
  local session = require("mucli.session")
  local cfg = require("mucli.config").get()
  require("mucli.client").post("/api/extensions/neovim/tool_result", {
    call_id = call_id, client_id = session.client_id, session_name = cfg.session,
    result = result, error = err or "",
  }, function(response)
    if not response.ok then util.notify("Could not return editor tool result: " .. tostring(response.error), vim.log.levels.WARN) end
  end)
end

local function propose_edit(event)
  local args = event.tool_args or {}
  local buf, path, err = resolve_buffer(args.file_path, true)
  if not buf then M.post_result(event.call_id, nil, err); return end
  local expected = tonumber(args.expected_changedtick)
  local tick = vim.api.nvim_buf_get_changedtick(buf)
  if not expected or expected ~= tick then
    M.post_result(event.call_id, nil, ("Buffer changedtick conflict (expected %s, current %s); read it again before proposing edits")
      :format(tostring(expected), tostring(tick)))
    return
  end
  local original = util.buffer_text(buf)
  require("mucli.diff").review_editor({
    path = path, original = original, proposed = tostring(args.new_content or ""),
    summary = args.summary or "Agent-proposed buffer edit", bufnr = buf, changedtick = tick,
  }, function(decision, reason)
    if decision ~= "accept" then
      M.post_result(event.call_id, { accepted = false, reason = reason or "User rejected the edit" })
      return
    end
    if not vim.api.nvim_buf_is_valid(buf) or vim.api.nvim_buf_get_changedtick(buf) ~= tick then
      M.post_result(event.call_id, nil, "Buffer changed while the diff was open; proposal was not applied")
      return
    end
    vim.api.nvim_buf_set_lines(buf, 0, -1, false, util.lines(args.new_content))
    M.post_result(event.call_id, {
      accepted = true, path = util.relative(path), saved = false,
      changedtick = vim.api.nvim_buf_get_changedtick(buf),
    })
  end)
end

function M.handle_tool_call(event)
  if event.extension_id ~= "neovim" then return end
  local session = require("mucli.session")
  if event.client_id and event.client_id ~= session.client_id then return end
  if event.tool_name == "nvim_propose_edit" then
    vim.schedule(function() propose_edit(event) end)
    return
  end
  if event.tool_name == "nvim_get_document_symbols" then
    vim.schedule(function() document_symbols_async(event) end)
    return
  end
  vim.schedule(function()
    local result = M.execute(event.tool_name, event.tool_args or {})
    if result.ok then M.post_result(event.call_id, result, nil)
    else M.post_result(event.call_id, nil, result.error) end
  end)
end

return M
