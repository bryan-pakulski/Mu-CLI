local M = {}

M.defaults = {
  host = "http://127.0.0.1:30311",
  token = nil,
  session = nil,
  provider = nil,
  model = nil,
  yolo = false,
  auto_connect = true,
  workspace = {
    root = nil,
    allow_outside = false,
    allow_secret_paths = false,
  },
  window = {
    width = 56,
    min_width = 42,
    max_width = 88,
    input_height = 7,
    position = "right",
  },
  context = {
    automatic = true,
    cursor_lines = 80, -- compatibility only; Context v2 uses the visible viewport.
    max_chars = 48000,
    max_file_chars = 24000,
    include_diagnostics = true,
    include_open_buffers = true,
  },
  hints = {
    enabled = true,
    max_items = 20,
    max_source_chars = 30000,
    virtual_text = true,
  },
  completion = {
    enabled = true,
    context_lines = 60,
    max_source_chars = 20000,
  },
  diff = {
    auto_open = true,
    layout = "vertical",
  },
  keymaps = {
    toggle = "<leader>mm",
    ask = "<leader>ma",
    actions = "<leader>mc",
    add_selection = "<leader>ms",
    add_file = "<leader>mf",
    hints = "<leader>mh",
    complete = "<M-\\>",
    accept_completion = "<M-l>",
    dismiss_completion = "<M-e>",
    interrupt = "<leader>mx",
    next_hint = "]m",
    prev_hint = "[m",
  },
}

M.opts = nil

local function normalize(user)
  user = vim.deepcopy(user or {})
  -- Compatibility with the proof-of-concept option names.
  user.keymaps = user.keymaps or {}
  local aliases = {
    toggle_panel = "toggle",
    send_visual = "add_selection",
    send_file = "add_file",
  }
  for old, new in pairs(aliases) do
    if user.keymaps[old] and user.keymaps[new] == nil then
      user.keymaps[new] = user.keymaps[old]
    end
  end
  return user
end

function M.setup(user)
  M.opts = vim.tbl_deep_extend("force", vim.deepcopy(M.defaults), normalize(user))
  M.opts.host = tostring(M.opts.host or M.defaults.host):gsub("/+$", "")
  if M.opts.window.position ~= "left" and M.opts.window.position ~= "right" then
    error("mucli: window.position must be 'left' or 'right'")
  end
  if M.opts.diff.layout ~= "vertical" and M.opts.diff.layout ~= "horizontal" then
    error("mucli: diff.layout must be 'vertical' or 'horizontal'")
  end
  M.opts.window.width = math.max(
    M.opts.window.min_width,
    math.min(M.opts.window.max_width, tonumber(M.opts.window.width) or 56)
  )
  return M.opts
end

function M.get()
  if not M.opts then
    return M.setup({})
  end
  return M.opts
end

return M
