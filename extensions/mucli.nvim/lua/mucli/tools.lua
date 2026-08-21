--- MuCLI Neovim Tools — definitions, execution dispatch, and SSE handler.
--- Tool definitions are sent to mucli via system prompt augmentation (backend).
--- SSE extension_tool_call events trigger execution, results POSTed to /api/extensions/neovim/tool_result.

local M = {}

-- Lazy-loaded modules
local config = function() return require("mucli.config") end
local client = function() return require("mucli.client") end
local session = function() return require("mucli.session") end
local diff = function() return require("mucli.diff") end

--- System prompt block sent to backend via /api/extensions/register.
--- Backend appends this to the system prompt when the neovim extension
--- is registered, so the model knows about neovim tools.
M.EXTENSION_SYSTEM_PROMPT = [[

NEOVIM EXTENSION TOOLS

You are connected to a Neovim editor via the mucli-neovim extension. The
following tools are available and dispatched through the Neovim plugin. Use
them to interact with the user's editor when it improves the workflow.

Available neovim tools (invoked like regular tool calls):

- nvim_open_file: Open a file in the editor. Params: {file_path: str, line?: int}
- nvim_jump_to_line: Jump cursor to a line in the current buffer. Params: {line: int, col?: int}
- nvim_get_buffer_content: Get all lines of a buffer. Params: {bufnr?: int} (defaults to current buffer)
- nvim_get_visual_selection: Get the user's current visual selection text. Params: {}
- nvim_apply_diff: Apply a unified diff to a file. Params: {file_path: str, diff: str}

These tools are executed by the Neovim plugin and results are returned through
the mucli API. Use them when the user is working in Neovim and you need to
inspect or modify their editor state. Prefer nvim_get_buffer_content over
read_file when the user is actively editing the file in Neovim, since the
buffer may contain unsaved changes.
]]

--- Tool definitions for neovim-specific tools.
--- These are sent to the backend via /api/extensions/register.
M.TOOL_DEFINITIONS = {
  {
    name = "nvim_open_file",
    description = "Open a file in the neovim editor. Switches the current window to the file.",
    parameters = {
      type = "object",
      properties = {
        file_path = { type = "string", description = "Absolute or relative path to the file to open." },
        line = { type = "integer", description = "Line number to jump to after opening (optional).", optional = true },
      },
      required = { "file_path" },
    },
  },
  {
    name = "nvim_jump_to_line",
    description = "Jump the cursor to a specific line in the current buffer.",
    parameters = {
      type = "object",
      properties = {
        line = { type = "integer", description = "Line number to jump to (1-indexed)." },
        col = { type = "integer", description = "Column number to jump to (1-indexed, optional).", optional = true },
      },
      required = { "line" },
    },
  },
  {
    name = "nvim_get_buffer_content",
    description = "Get all lines of a buffer. Defaults to the current buffer.",
    parameters = {
      type = "object",
      properties = {
        bufnr = { type = "integer", description = "Buffer number (optional, defaults to current buffer).", optional = true },
      },
      required = {},
    },
  },
  {
    name = "nvim_get_visual_selection",
    description = "Get the user's current visual selection text from neovim.",
    parameters = {
      type = "object",
      properties = {},
      required = {},
    },
  },
  {
    name = "nvim_apply_diff",
    description = "Apply a unified diff to a file in neovim. Opens the diff view for accept/reject.",
    parameters = {
      type = "object",
      properties = {
        file_path = { type = "string", description = "Path to the file to apply the diff to." },
        diff_text = { type = "string", description = "Unified diff text to apply." },
      },
      required = { "file_path", "diff_text" },
    },
  },
}

--- Execute a neovim tool by name with the given arguments.
--- @param tool_name string: Tool name (e.g. "nvim_open_file")
--- @param args table: Tool arguments
--- @return table: {ok=true, result=...} or {ok=false, error=...}
function M.execute_tool(tool_name, args)
  if not args then args = {} end

  if tool_name == "nvim_open_file" then
    return M._open_file(args)
  elseif tool_name == "nvim_jump_to_line" then
    return M._jump_to_line(args)
  elseif tool_name == "nvim_get_buffer_content" then
    return M._get_buffer_content(args)
  elseif tool_name == "nvim_get_visual_selection" then
    return M._get_visual_selection(args)
  elseif tool_name == "nvim_apply_diff" then
    return M._apply_diff(args)
  else
    return { ok = false, error = "Unknown tool: " .. tostring(tool_name) }
  end
end

--- nvim_open_file: Open file in neovim
function M._open_file(args)
  local path = args.file_path or args.path
  if not path then
    return { ok = false, error = "file_path is required" }
  end
  vim.cmd("edit " .. vim.fn.fnameescape(path))
  if args.line then
    vim.api.nvim_win_set_cursor(0, { tonumber(args.line), 0 })
  end
  return { ok = true, result = { path = path, line = args.line } }
end

--- nvim_jump_to_line: Move cursor to line
function M._jump_to_line(args)
  local line = tonumber(args.line)
  if not line then
    return { ok = false, error = "line is required and must be a number" }
  end
  local col = tonumber(args.col) or 0
  vim.api.nvim_win_set_cursor(0, { line, col - 1 })
  return { ok = true, result = { line = line, col = col } }
end

--- nvim_get_buffer_content: Get buffer lines
function M._get_buffer_content(args)
  local bufnr = args.bufnr or 0
  if bufnr == 0 then bufnr = vim.api.nvim_get_current_buf() end
  local lines = vim.api.nvim_buf_get_lines(bufnr, 0, -1, false)
  local name = vim.api.nvim_buf_get_name(bufnr)
  return { ok = true, result = { bufnr = bufnr, name = name, lines = lines, line_count = #lines } }
end

--- nvim_get_visual_selection: Get selected text
function M._get_visual_selection(args)
  -- Try vim.fn.getregion (Neovim 0.10+)
  local ok, region = pcall(vim.fn.getregion, 0, vim.fn.getpos("'<"), vim.fn.getpos("'>"))
  if ok and region then
    return { ok = true, result = { text = table.concat(region, "\n"), lines = region } }
  end
  -- Fallback: use getline with marks
  local start_pos = vim.fn.getpos("'<")
  local end_pos = vim.fn.getpos("'>")
  local start_line = start_pos[2]
  local end_line = end_pos[2]
  if start_line == 0 or end_line == 0 then
    return { ok = false, error = "No visual selection active" }
  end
  local lines = vim.fn.getline(start_line, end_line)
  return { ok = true, result = { text = table.concat(lines, "\n"), lines = lines } }
end

--- nvim_apply_diff: Apply diff via diff view
function M._apply_diff(args)
  local path = args.file_path or args.path
  local diff_text = args.diff_text or args.diff
  if not path or not diff_text then
    return { ok = false, error = "file_path and diff_text are required" }
  end
  -- Use diff module to show diff view for accept/reject
  local d = diff()
  if d.show_diff then
    d.show_diff(path, diff_text)
    return { ok = true, result = { path = path, message = "Diff view opened for accept/reject" } }
  end
  return { ok = false, error = "diff module not available" }
end

--- SSE extension_tool_call event handler.
--- Called by chat/buffer.lua when an extension_tool_call SSE event is received.
--- Executes the tool on main thread via vim.schedule() and POSTs result back.
--- @param event table: SSE event with {kind, extension_id, tool_name, tool_args, call_id, session_name}
function M.handle_tool_call(event)
  local tool_name = event.tool_name or event.tool
  local tool_args = event.tool_args or event.args or {}
  local call_id = event.call_id or event.id
  local session_name = event.session_name or config().opts.session

  if not tool_name then
    M._post_result(call_id, session_name, nil, "No tool_name in event")
    return
  end

  -- Execute on main thread via vim.schedule
  vim.schedule(function()
    local result = M.execute_tool(tool_name, tool_args)
    if result.ok then
      M._post_result(call_id, session_name, result.result, nil)
    else
      M._post_result(call_id, session_name, nil, result.error)
    end
  end)
end

--- POST tool result back to mucli via /api/extensions/neovim/tool_result
--- @param call_id string: Tool call ID
--- @param session_name string: Session name
--- @param result any: Tool result (nil on error)
--- @param error string|nil: Error message (nil on success)
function M._post_result(call_id, session_name, result, error)
  if not call_id then
    vim.notify("[mucli] Cannot post tool result: no call_id", vim.log.levels.WARN)
    return
  end

  local body = {
    call_id = call_id,
    session_name = session_name,
  }
  if result then
    body.result = result
  end
  if error then
    body.error = error
  end

  local c = client()
  c.post("/api/extensions/neovim/tool_result", body, function(res)
    -- Silent success, warn on failure
    if not res or res.status >= 400 then
      vim.notify("[mucli] Failed to post tool result: " .. tostring(res and res.status), vim.log.levels.WARN)
    end
  end)
end

return M