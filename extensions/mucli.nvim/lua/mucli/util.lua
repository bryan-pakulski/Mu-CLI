local M = {}

function M.notify(message, level)
  vim.notify("MUCLI · " .. tostring(message), level or vim.log.levels.INFO, { title = "MUCLI" })
end

function M.schedule(fn)
  vim.schedule(function()
    local ok, err = pcall(fn)
    if not ok then
      M.notify(err, vim.log.levels.ERROR)
    end
  end)
end

function M.decode(value, fallback)
  if type(value) == "table" then return value end
  local ok, decoded = pcall(vim.json.decode, value or "")
  return ok and decoded or fallback
end

function M.encode_query(value)
  return tostring(value or ""):gsub("([^%w%-._~])", function(char)
    return string.format("%%%02X", string.byte(char))
  end)
end

function M.normalize_path(path)
  if not path or path == "" then return "" end
  path = vim.fn.fnamemodify(vim.fn.expand(path), ":p")
  path = vim.fs and vim.fs.normalize(path) or path
  if path == "/" or path:match("^%a:/$") then return path end
  return path:gsub("/+$", "")
end

function M.is_absolute(path)
  path = tostring(path or "")
  return path:match("^/") ~= nil
    or path:match("^%a:[/\\]") ~= nil
    or path:match("^\\\\") ~= nil
end

function M.canonical_path(path)
  path = M.normalize_path(path)
  if path == "" then return "" end
  local uv = vim.uv or vim.loop
  local resolved = uv.fs_realpath(path)
  if resolved then return M.normalize_path(resolved) end

  -- Resolve the nearest existing ancestor as well. This prevents a new file
  -- beneath an in-workspace symlink from escaping the workspace boundary.
  local suffix, cursor = {}, path
  while cursor and cursor ~= "" do
    local parent = vim.fs.dirname(cursor)
    if not parent or parent == cursor then break end
    table.insert(suffix, 1, vim.fs.basename(cursor))
    resolved = uv.fs_realpath(parent)
    if resolved then
      return M.normalize_path(resolved .. "/" .. table.concat(suffix, "/"))
    end
    cursor = parent
  end
  return path
end

function M.workspace_root(buf)
  local configured = require("mucli.config").get().workspace.root
  if configured and configured ~= "" then return M.normalize_path(configured) end
  buf = buf or 0
  local path = vim.api.nvim_buf_get_name(buf)
  local start = path ~= "" and vim.fs.dirname(path) or (vim.uv or vim.loop).cwd()
  local root = vim.fs.root(start, { ".git", ".mucli", "pyproject.toml", "package.json", "Cargo.toml", "go.mod" })
  return M.normalize_path(root or start)
end

function M.relative(path, root)
  path = M.normalize_path(path)
  root = M.normalize_path(root or M.workspace_root())
  if path == root then return "." end
  local prefix = root:sub(-1) == "/" and root or (root .. "/")
  if path:sub(1, #prefix) == prefix then return path:sub(#prefix + 1) end
  return path
end

function M.is_within(path, root)
  path = M.canonical_path(path)
  root = M.canonical_path(root)
  if path == "" or root == "" then return false end
  local prefix = root:sub(-1) == "/" and root or (root .. "/")
  return path == root or path:sub(1, #prefix) == prefix
end

function M.is_secret_path(path)
  path = M.canonical_path(path)
  if path == "" then return false end
  local base = (vim.fs.basename(path) or ""):lower()
  local exact_basenames = {
    id_rsa = true, id_ed25519 = true, id_ecdsa = true, id_dsa = true,
    known_hosts = true, authorized_keys = true,
  }
  if exact_basenames[base]
    or base:match("^id_rsa%.") or base:match("^id_ed25519%.")
    or base:match("^id_ecdsa%.") or base:match("^id_dsa%.")
    or base:match("^known_hosts%.")
    or base == ".env" or base:match("^%.env%.")
    or base:match("%.pem$") or base:match("%.key$")
    or base:match("%.pfx$") or base:match("%.p12$")
    or base:match("%.jks$") or base:match("%.keystore$")
    or base:match("^credentials.*%.json$")
    or base:match("^service[-_]account.*%.json$")
    or base:match("^gcp%-key.*%.json$") then
    return true
  end

  local denied_roots = {
    "~/.ssh", "~/.aws", "~/.azure", "~/.config/gcloud", "~/.kube",
    "~/.gnupg", "~/.config/gh", "~/.cargo/credentials.d",
    "/etc/ssh", "/etc/sudoers.d",
  }
  for _, root in ipairs(denied_roots) do
    if M.is_within(path, vim.fn.expand(root)) then return true end
  end
  local denied_exact = {
    "~/.docker/config.json", "~/.bashrc", "~/.zshrc", "~/.profile",
    "~/.bash_profile", "~/.zprofile", "~/.bash_history", "~/.zsh_history",
    "~/.netrc", "~/.npmrc", "~/.pypirc", "~/.cargo/credentials",
    "~/.cargo/credentials.toml", "/etc/shadow", "/etc/sudoers",
  }
  for _, candidate in ipairs(denied_exact) do
    if path == M.canonical_path(vim.fn.expand(candidate)) then return true end
  end
  return path:match("^/proc/[^/]+/environ$") ~= nil
    or path:match("^/proc/[^/]+/cmdline$") ~= nil
end

function M.lines(text)
  local lines = vim.split(tostring(text or ""), "\n", { plain = true })
  if #lines > 1 and lines[#lines] == "" then table.remove(lines) end
  if #lines == 0 then return { "" } end
  return lines
end

function M.buffer_text(buf, start_line, end_line)
  if not vim.api.nvim_buf_is_valid(buf) then return "" end
  local lines = vim.api.nvim_buf_get_lines(buf, start_line or 0, end_line or -1, false)
  return table.concat(lines, "\n")
end

function M.truncate(text, max_chars, suffix)
  text = tostring(text or "")
  max_chars = tonumber(max_chars) or #text
  if #text <= max_chars then return text, false end
  suffix = suffix or "\n… [truncated by MUCLI]"
  local keep = math.max(0, max_chars - #suffix)
  return text:sub(1, keep) .. suffix, true
end

function M.code_fence(text, language)
  local fence = "```"
  while tostring(text):find(fence, 1, true) do fence = fence .. "`" end
  return string.format("%s%s\n%s\n%s", fence, language or "", text or "", fence)
end

function M.current_filetype(path)
  local buf = path and vim.fn.bufnr(M.normalize_path(path)) or -1
  if buf and buf >= 0 and vim.api.nvim_buf_is_valid(buf) then
    return vim.bo[buf].filetype
  end
  return vim.filetype.match({ filename = path }) or "text"
end

function M.find_buffer(path)
  local target = M.normalize_path(path)
  for _, buf in ipairs(vim.api.nvim_list_bufs()) do
    if vim.api.nvim_buf_is_valid(buf) and M.normalize_path(vim.api.nvim_buf_get_name(buf)) == target then
      return buf
    end
  end
  return nil
end

function M.clamp(value, low, high)
  return math.max(low, math.min(high, value))
end

function M.uuid(seed)
  local source = table.concat({ tostring(seed or ""), tostring((vim.uv or vim.loop).hrtime()), tostring(vim.fn.getpid()) }, ":")
  return vim.fn.sha256(source):sub(1, 16)
end

return M
