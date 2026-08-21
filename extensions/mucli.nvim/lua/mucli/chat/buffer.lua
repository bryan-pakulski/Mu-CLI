-- Compatibility facade for the original proof-of-concept module.  The real
-- conversation model now lives in mucli.store and rendering in chat.render.
local M = {}

function M.init_buffer(buf)
  require("mucli.chat.render").render(buf, vim.fn.bufwinid(buf))
end

function M.is_initialized(buf)
  return buf and vim.api.nvim_buf_is_valid(buf)
end

function M.handle_sse_event(_, event)
  require("mucli.events").handle(event)
end

function M.append_user_message(_, text)
  require("mucli.store").add_message("user", text)
end

function M.append_text(_, text)
  require("mucli.store").add_message("assistant", text)
end

function M.clear_buffer()
  require("mucli.store").reset()
end

return M
