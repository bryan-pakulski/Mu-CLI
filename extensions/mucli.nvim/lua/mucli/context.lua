--- Context capture module — visual selection + active file context.
-- Captures editor state, prompts for user message, sends both to mucli chat.
local M = {}

local client = require("mucli.client")
local config = require("mucli.config")
local buffer = require("mucli.chat.buffer")
local panel = require("mucli.chat.panel")

--- Get visual selection text using vim.fn.getregion (Neovim 0.10+).
-- Falls back to getline + marks for older versions.
-- @return string|nil selected text, or nil if no selection
function M.get_visual_selection()
  if vim.fn.exists("*getregion") == 1 then
    local mode = vim.fn.mode()
    if mode == "v" or mode == "V" or mode == "\22" then
      local ok, lines = pcall(vim.fn.getregion, 0, "'<", "'>")
      if ok and lines and #lines > 0 then
        return table.concat(lines, "\n")
      end
    end
  end

  local pos1 = vim.fn.getpos("'<")
  local pos2 = vim.fn.getpos("'>")
  if pos1[2] == 0 or pos2[2] == 0 then
    return nil
  end

  local start_line = pos1[2]
  local end_line = pos2[2]
  if start_line > end_line then
    start_line, end_line = end_line, start_line
  end

  local lines = vim.api.nvim_buf_get_lines(0, start_line - 1, end_line, false)
  return table.concat(lines, "\n")
end

--- Get active file metadata: path, filetype, content, cursor position.
-- @return table {path, filetype, lines, cursor_line}
function M.get_active_file()
  local bufnr = vim.api.nvim_get_current_buf()
  local path = vim.api.nvim_buf_get_name(bufnr)
  local filetype = vim.bo[bufnr].filetype or "text"
  local lines = vim.api.nvim_buf_get_lines(bufnr, 0, -1, false)
  local cursor = vim.api.nvim_win_get_cursor(0)

  return {
    path = path,
    filetype = filetype,
    lines = lines,
    cursor_line = cursor[1],
  }
end

--- Format context as markdown code block with filepath header.
-- @param text string content
-- @param type string "selection" or "file"
-- @param filepath string|nil optional file path
-- @param filetype string|nil optional filetype for syntax
-- @return string formatted markdown
function M.format_context(text, type, filepath, filetype)
  local header = type == "selection" and "Visual selection" or "Active file"
  if filepath and filepath ~= "" then
    header = header .. " from `" .. filepath .. "`"
  end

  local lang = filetype or ""
  return header .. ":\n```" .. lang .. "\n" .. text .. "\n```"
end

--- Prompt user for a text message via vim.ui.input.
-- @param prompt string the prompt label
-- @param callback function(text|nil) called with user input or nil if cancelled
function M.prompt_for_text(prompt, callback)
  vim.ui.input({ prompt = prompt .. " (Enter to send, Esc to cancel): " }, function(input)
    callback(input)
  end)
end

--- Send context + optional prompt to mucli chat.
-- Formats context, optionally prompts for user text, sends both.
-- @param formatted_context string already-formatted context markdown
-- @param prompt_text string|nil optional pre-existing prompt (skip UI prompt)
-- @param buf integer|nil chat buffer to echo into
function M.send_with_prompt(formatted_context, prompt_text, buf)
  local send_fn = function(user_text)
    local full_text = formatted_context
    if user_text and user_text ~= "" then
      full_text = user_text .. "\n\n" .. formatted_context
    end

    local session_name = config.opts.session
    local chat_buf = buf or panel.get_buf()

    -- Echo into chat panel if available
    if chat_buf then
      local display = prompt_text or user_text or "Sending context..."
      buffer.append_user_message(chat_buf, display)
    end

    client.post("/api/chat/send", { text = full_text, session_name = session_name }, function(resp)
      vim.schedule(function()
        if not resp or resp.status >= 400 then
          local b = chat_buf or panel.get_buf()
          if b then
            buffer.append_text(b, "⚠ Failed to send context")
            buffer.append_text(b, "")
          end
          vim.notify("mucli: failed to send context", vim.log.levels.ERROR)
        end
      end)
    end)
  end

  if prompt_text then
    send_fn(prompt_text)
  else
    M.prompt_for_text("Prompt", send_fn)
  end
end

--- Send visual selection to mucli chat with user prompt.
-- Captures selection, prompts for text, sends both.
function M.send_visual()
  local text = M.get_visual_selection()
  if not text or text == "" then
    vim.notify("mucli: no visual selection to send", vim.log.levels.WARN)
    return
  end

  local path = vim.api.nvim_buf_get_name(0)
  local filetype = vim.bo.filetype or ""
  local formatted = M.format_context(text, "selection", path, filetype)

  M.send_with_prompt(formatted, nil, nil)
end

--- Send active file content to mucli chat with user prompt.
-- Captures file, prompts for text, sends both.
function M.send_file()
  local file = M.get_active_file()
  if not file.lines or #file.lines == 0 then
    vim.notify("mucli: empty file, nothing to send", vim.log.levels.WARN)
    return
  end

  local content = table.concat(file.lines, "\n")
  local formatted = M.format_context(content, "file", file.path, file.filetype)

  M.send_with_prompt(formatted, nil, nil)
end

--- Send visual selection with an inline prompt (no UI).
-- Used by :MucliSend when user types prompt as argument.
-- @param prompt string the user's text prompt
function M.send_visual_with_prompt(prompt)
  local text = M.get_visual_selection()
  if not text or text == "" then
    vim.notify("mucli: no visual selection to send", vim.log.levels.WARN)
    return
  end

  local path = vim.api.nvim_buf_get_name(0)
  local filetype = vim.bo.filetype or ""
  local formatted = M.format_context(text, "selection", path, filetype)

  M.send_with_prompt(formatted, prompt, nil)
end

--- Send active file with an inline prompt (no UI).
-- Used by :MucliSendFile when user types prompt as argument.
-- @param prompt string the user's text prompt
function M.send_file_with_prompt(prompt)
  local file = M.get_active_file()
  if not file.lines or #file.lines == 0 then
    vim.notify("mucli: empty file, nothing to send", vim.log.levels.WARN)
    return
  end

  local content = table.concat(file.lines, "\n")
  local formatted = M.format_context(content, "file", file.path, file.filetype)

  M.send_with_prompt(formatted, prompt, nil)
end

return M