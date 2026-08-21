local M = {}

local config = require("mucli.config")
local client = require("mucli.client")
local buffer = require("mucli.chat.buffer")

local SEPARATOR = "────────────────────────────────────────"
local INPUT_MARKER = "» "

--- Handle Enter key — read input line, send to mucli
--- @param buf integer
function M.handle_enter(buf)
  local text = buffer.get_input_text(buf)
  if not text or text == "" then return end

  -- Remove old input area (separator + » marker) so user message
  -- appears above, then fresh input area is added after.
  buffer.set_input_line(buf)  -- cleans old area + adds new one at bottom

  -- Echo user message into chat history with header
  -- (insert before the new input area)
  buffer.append_user_message(buf, text)

  -- Send to mucli via POST /api/chat/send
  local session_name = config.opts.session
  local body = {
    text = text,
    session_name = session_name,
  }
  client.post("/api/chat/send", body, function(resp)
    vim.schedule(function()
      if not resp or resp.status >= 400 then
        local err_msg = "⚠ Failed to send message"
        if resp and resp.body then
          local ok, parsed = pcall(vim.json.decode, resp.body)
          if ok and parsed and parsed.detail then
            err_msg = "⚠ " .. tostring(parsed.detail)
          end
        end
        buffer.append_text(buf, err_msg)
        buffer.append_text(buf, "")
      end
    end)
  end)
end

--- Handle interrupt — POST /api/chat/interrupt
function M.handle_interrupt()
  local session_name = config.opts.session
  local body = { session_name = session_name }
  client.post("/api/chat/interrupt", body, function(resp)
    vim.schedule(function()
      local panel = require("mucli.chat.panel")
      local buf = panel.get_buf()
      if buf and resp and resp.status < 400 then
        buffer.append_text(buf, "⏹ Interrupted")
        buffer.append_text(buf, "")
      end
    end)
  end)
end

--- Set up buffer-local keymaps for input handling
--- @param buf integer
function M.setup_input_keymaps(buf)
  -- CR in insert mode sends message
  vim.api.nvim_buf_set_keymap(buf, "i", "<CR>", "", {
    callback = function()
      M.handle_enter(buf)
    end,
    noremap = true,
    silent = true,
    desc = "Send message to mucli",
  })

  -- Ctrl-C interrupts active turn
  vim.api.nvim_buf_set_keymap(buf, "i", "<C-c>", "", {
    callback = function()
      M.handle_interrupt()
    end,
    noremap = true,
    silent = true,
    desc = "Interrupt mucli turn",
  })

  -- Also map in normal mode
  vim.api.nvim_buf_set_keymap(buf, "n", "<CR>", "", {
    callback = function()
      M.handle_enter(buf)
    end,
    noremap = true,
    silent = true,
    desc = "Send message to mucli",
  })
end

return M