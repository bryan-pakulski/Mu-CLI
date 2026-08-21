local M = {}

--- Default configuration
M.defaults = {
  host = "http://localhost:30311",
  session = nil, -- auto-derived from cwd basename if nil
  provider = nil,
  model = nil,
  yolo = true, -- yolo mode by default for neovim plugin
  window = {
    width = 60,
    position = "right", -- "right" or "left"
  },
  keymaps = {
    send_visual = "<leader>ms",
    send_file = "<leader>mf",
    toggle_panel = "<leader>mt",
    interrupt = "<leader>mi",
    accept_hunk = "<leader>da",
    reject_hunk = "<leader>dr",
  },
}

--- Merged configuration (accessible after setup via require('mucli.config').opts)
M.opts = nil

--- Deep merge user opts over defaults
--- @param defaults table
--- @param user_opts table
--- @return table
local function deep_merge(defaults, user_opts)
  local result = {}
  for k, v in pairs(defaults) do
    if type(v) == "table" and type(user_opts[k]) == "table" then
      result[k] = deep_merge(v, user_opts[k])
    else
      result[k] = v
    end
  end
  for k, v in pairs(user_opts) do
    if result[k] == nil then
      result[k] = v
    end
  end
  return result
end

--- Setup config with user options
--- @param user_opts table|nil User configuration overrides
--- @return table Merged config
function M.setup(user_opts)
  user_opts = user_opts or {}
  M.opts = deep_merge(M.defaults, user_opts)

  -- Session validation deferred to init.lua — if session is nil,
  -- the interactive setup wizard will prompt the user.
  return M.opts
end

return M