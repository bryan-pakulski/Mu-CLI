local M = { configured = {} }

local util = require("mucli.util")

function M.text(buf)
  local text = table.concat(vim.api.nvim_buf_get_lines(buf, 0, -1, false), "\n")
  return text:gsub("^%s+", ""):gsub("%s+$", "")
end

function M.clear(buf)
  if vim.api.nvim_buf_is_valid(buf) then vim.api.nvim_buf_set_lines(buf, 0, -1, false, { "" }) end
end

function M.submit(buf)
  local text = M.text(buf)
  if text == "" then return end
  if require("mucli.conversation").send(text) then M.clear(buf) end
end

function M.interrupt()
  require("mucli.session").interrupt(function(result, response)
    if result and result.ok then util.notify("Interrupted the active turn")
    else util.notify((response and response.error) or "No active turn to interrupt", vim.log.levels.WARN) end
  end)
end

function M.setup(buf)
  if M.configured[buf] then return end
  M.configured[buf] = true
  local opts = { buffer = buf, silent = true }
  vim.keymap.set({ "i", "n" }, "<C-s>", function() M.submit(buf) end, vim.tbl_extend("force", opts, { desc = "Send MUCLI message" }))
  vim.keymap.set("n", "<CR>", function() M.submit(buf) end, vim.tbl_extend("force", opts, { desc = "Send MUCLI message" }))
  vim.keymap.set({ "i", "n" }, "<C-c>", M.interrupt, vim.tbl_extend("force", opts, { desc = "Interrupt MUCLI" }))
  vim.keymap.set({ "i", "n" }, "<C-a>", function()
    vim.cmd("stopinsert")
    require("mucli.context").picker()
  end, vim.tbl_extend("force", opts, { desc = "Add MUCLI context" }))
  vim.keymap.set("n", "<C-l>", function() M.clear(buf) end, vim.tbl_extend("force", opts, { desc = "Clear MUCLI draft" }))
  vim.keymap.set("n", "q", require("mucli.chat.panel").close, vim.tbl_extend("force", opts, { desc = "Close MUCLI" }))
end

-- Proof-of-concept compatibility names.
M.handle_enter = M.submit
M.handle_interrupt = M.interrupt
M.setup_input_keymaps = M.setup

return M
