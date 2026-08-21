local M = {}

local config = require("mucli.config")
local client = require("mucli.client")

local SEPARATOR = "────────────────────────────────────────"
local INPUT_MARKER = "» "
local USER_HEADER = "**You:** "
local ASSISTANT_HEADER = "**Assistant:**"

-- State per buffer
local buf_state = {}

--- Ensure treesitter markdown highlighting is active on buffer
--- @param buf integer
local function ensure_treesitter(buf)
  vim.api.nvim_set_option_value("filetype", "markdown", { buf = buf })
  local ok = pcall(vim.treesitter.start, buf, "markdown")
  if not ok then
    pcall(vim.treesitter.get_parser, buf, "markdown")
  end
end

--- Get or init state for a buffer
--- @param buf integer
--- @return table
local function get_state(buf)
  if not buf_state[buf] then
    buf_state[buf] = {
      input_line_idx = nil,
      initialized = false,
      assistant_responding = false,
    }
  end
  return buf_state[buf]
end

--- Check if buffer has been initialized
--- @param buf integer
--- @return boolean
function M.is_initialized(buf)
  local st = get_state(buf)
  return st.initialized == true
end

--- Initialize buffer: set up treesitter, input line, keymaps
--- @param buf integer
function M.init_buffer(buf)
  local st = get_state(buf)
  if st.initialized then return end
  st.initialized = true

  ensure_treesitter(buf)

  vim.api.nvim_set_option_value("modifiable", true, { buf = buf })

  -- Window-local options must be set on win, not buf
  local win = vim.fn.bufwinid(buf)
  if win ~= -1 then
    vim.api.nvim_set_option_value("conceallevel", 2, { win = win })
    vim.api.nvim_set_option_value("wrap", true, { win = win })
    vim.api.nvim_set_option_value("cursorline", true, { win = win })
    vim.api.nvim_set_option_value("number", false, { win = win })
    vim.api.nvim_set_option_value("relativenumber", false, { win = win })
  end

  local lines = {
    "# mucli chat",
    "",
    "Connected to session: " .. (config.opts.session or "unknown"),
    "",
  }
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)

  M.set_input_line(buf)
end

--- Append text to end of buffer (for assistant_delta streaming)
--- @param buf integer
--- @param text string
function M.append_text(buf, text)
  if not text or text == "" then return end
  local st = get_state(buf)
  local line_count = vim.api.nvim_buf_line_count(buf)

  local lines = vim.split(text, "\n", { plain = true })

  local insert_line = line_count
  if st.input_line_idx then
    insert_line = st.input_line_idx
  end

  vim.api.nvim_buf_set_lines(buf, insert_line, insert_line, false, lines)

  if st.input_line_idx then
    st.input_line_idx = st.input_line_idx + #lines
  end

  local win = vim.fn.bufwinid(buf)
  if win ~= -1 then
    local last = vim.api.nvim_buf_line_count(buf)
    vim.api.nvim_win_set_cursor(win, { last, 0 })
  end
end

--- Append user message with visual header + highlight
--- Handles multi-line text (visual selections) by splitting on newlines.
--- @param buf integer
--- @param text string
function M.append_user_message(buf, text)
  if not text or text == "" then return end
  local st = get_state(buf)
  local line_count = vim.api.nvim_buf_line_count(buf)
  local insert_line = st.input_line_idx or line_count

  -- Split text on newlines — nvim_buf_set_lines requires each line separate
  local text_lines = vim.split(text, "\n", { plain = true })

  -- Build lines: blank spacer, header, then content lines
  local lines = { "", USER_HEADER .. text_lines[1] }
  for i = 2, #text_lines do
    lines[#lines + 1] = text_lines[i]
  end

  vim.api.nvim_buf_set_lines(buf, insert_line, insert_line, false, lines)

  if st.input_line_idx then
    st.input_line_idx = st.input_line_idx + #lines
  end

  -- Highlight user header line
  vim.api.nvim_buf_add_highlight(buf, -1, "Title", insert_line + 1, 0, #USER_HEADER)

  -- Reset assistant responding flag — new user message means new turn
  st.assistant_responding = false

  -- Show thinking indicator
  M.show_thinking(buf)
end

--- Show "Thinking..." indicator while model processes
--- @param buf integer
function M.show_thinking(buf)
  local st = get_state(buf)
  if st.thinking_line then return end  -- already showing

  local insert_line = st.input_line_idx or vim.api.nvim_buf_line_count(buf)
  local lines = { "", "⏳ _Thinking..._" }
  vim.api.nvim_buf_set_lines(buf, insert_line, insert_line, false, lines)

  st.thinking_line = insert_line + 1  -- track line number for removal

  if st.input_line_idx then
    st.input_line_idx = st.input_line_idx + #lines
  end

  -- Highlight with Comment group (dimmed)
  vim.api.nvim_buf_add_highlight(buf, -1, "Comment", st.thinking_line, 0, -1)
end

--- Clear "Thinking..." indicator when assistant starts responding
--- @param buf integer
function M.clear_thinking(buf)
  local st = get_state(buf)
  if not st.thinking_line then return end

  -- Remove the blank line + thinking line (2 lines)
  local think_line = st.thinking_line
  vim.api.nvim_buf_set_lines(buf, think_line - 1, think_line + 1, false, {})

  if st.input_line_idx then
    st.input_line_idx = st.input_line_idx - 2
  end

  st.thinking_line = nil
end

--- Start assistant response with header if not already responding
--- @param buf integer
function M.start_assistant_response(buf)
  local st = get_state(buf)
  if st.assistant_responding then return end
  st.assistant_responding = true

  -- Clear thinking indicator if present
  M.clear_thinking(buf)

  local line_count = vim.api.nvim_buf_line_count(buf)
  local insert_line = st.input_line_idx or line_count

  -- Add blank line + assistant header
  local lines = {
    "",
    ASSISTANT_HEADER,
  }
  vim.api.nvim_buf_set_lines(buf, insert_line, insert_line, false, lines)

  if st.input_line_idx then
    st.input_line_idx = st.input_line_idx + #lines
  end

  -- Highlight assistant header
  vim.api.nvim_buf_add_highlight(buf, -1, "Identifier", insert_line + 1, 0, #ASSISTANT_HEADER)
end

--- Append dimmed italic text (for thinking_delta streaming)
--- @param buf integer
--- @param text string
function M.append_thinking(buf, text)
  if not text or text == "" then return end

  local st = get_state(buf)
  local line_count = vim.api.nvim_buf_line_count(buf)
  local insert_line = st.input_line_idx or line_count

  local lines = vim.split(text, "\n", { plain = true })
  for i, line in ipairs(lines) do
    lines[i] = "> " .. line
  end

  vim.api.nvim_buf_set_lines(buf, insert_line, insert_line, false, lines)

  if st.input_line_idx then
    st.input_line_idx = st.input_line_idx + #lines
  end

  vim.api.nvim_buf_add_highlight(buf, -1, "Comment", insert_line, 0, -1)
end

--- Clear buffer content
--- @param buf integer
function M.clear_buffer(buf)
  local st = get_state(buf)
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, {})
  st.input_line_idx = nil
  st.assistant_responding = false
  M.init_buffer(buf)
end

--- Set up input line separator and input area at bottom.
--- Removes any existing input area first to prevent duplicates.
--- @param buf integer
function M.set_input_line(buf)
  local st = get_state(buf)

  -- Remove old input area if it exists (prevents duplicate separators)
  if st.input_line_idx then
    local delete_from = st.input_line_idx - 2
    if delete_from < 0 then delete_from = 0 end
    vim.api.nvim_buf_set_lines(buf, delete_from, -1, false, {})
  end

  local line_count = vim.api.nvim_buf_line_count(buf)

  vim.api.nvim_buf_set_lines(buf, line_count, line_count, false, {
    "",
    SEPARATOR,
    "",
    INPUT_MARKER,
  })

  st.input_line_idx = line_count + 3

  local win = vim.fn.bufwinid(buf)
  if win ~= -1 then
    vim.api.nvim_win_set_cursor(win, { line_count + 4, #INPUT_MARKER })
    vim.cmd("startinsert!")
  end
end

--- Get input line text (text after INPUT_MARKER on last line)
--- @param buf integer
--- @return string
function M.get_input_text(buf)
  local st = get_state(buf)
  if not st.input_line_idx then return "" end
  local lines = vim.api.nvim_buf_get_lines(buf, st.input_line_idx, st.input_line_idx + 1, false)
  if #lines == 0 then return "" end
  local line = lines[1]
  if line:sub(1, #INPUT_MARKER) == INPUT_MARKER then
    return line:sub(#INPUT_MARKER + 1)
  end
  return line
end

--- Clear input line (reset to just marker)
--- @param buf integer
function M.clear_input(buf)
  local st = get_state(buf)
  if not st.input_line_idx then return end
  vim.api.nvim_buf_set_lines(buf, st.input_line_idx, st.input_line_idx + 1, false, { INPUT_MARKER })
end

--- Initialize SSE event dispatch to this buffer
--- @param buf integer
function M.init_sse(buf)
  client.start_sse(function(event)
    vim.schedule(function()
      M.handle_sse_event(buf, event)
    end)
  end)
end

--- Handle a single SSE event
--- @param buf integer
--- @param event table parsed SSE event {kind=..., ...}
function M.handle_sse_event(buf, event)
  if not event or not event.kind then return end

  -- Filter events by session_name — only process events for our session
  local our_session = config.opts.session
  if event.session_name and our_session and event.session_name ~= our_session then
    return
  end

  -- Allow global events (hello, ping) that don't have session_name
  if event.kind == "hello" or event.kind == "ping" then
    return
  end

  if event.kind == "assistant_delta" then
    -- Start assistant response with header on first delta
    M.start_assistant_response(buf)
    M.append_text(buf, event.text or event.delta or "")
  elseif event.kind == "thinking_delta" then
    M.start_assistant_response(buf)
    M.append_thinking(buf, event.text or event.delta or "")
  elseif event.kind == "turn_complete" then
    local st = get_state(buf)
    st.assistant_responding = false
    M.clear_thinking(buf)  -- safety: clear if no assistant_delta came
    -- set_input_line cleans old input area and adds fresh one
    M.set_input_line(buf)
  elseif event.kind == "user_message" then
    -- Skip: handle_enter() already echoes user message locally.
    -- SSE user_message is server-side echo — would cause double display.
  elseif event.kind == "error" then
    M.clear_thinking(buf)
    M.append_text(buf, "⚠ Error: " .. (event.text or event.message or "unknown"))
    M.append_text(buf, "")
  elseif event.kind == "prompt" then
    M.append_text(buf, "📋 Approval required: " .. (event.text or event.message or ""))
    M.append_text(buf, "")
  elseif event.kind == "artifact_created" then
    M.append_text(buf, "📎 Artifact: " .. (event.name or event.text or "artifact"))
    M.append_text(buf, "")
    local ok, diff = pcall(require, "mucli.diff")
    if ok and diff.detect_and_show_diff then
      diff.detect_and_show_diff(event)
    end
  elseif event.kind == "extension_tool_call" then
    local ok, tools = pcall(require, "mucli.tools")
    if ok and tools.handle_tool_call then
      tools.handle_tool_call(event)
    end
  end
end

return M