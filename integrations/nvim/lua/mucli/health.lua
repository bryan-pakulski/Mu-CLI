local M = {}
function M.check()
  vim.health.start('Mucli IDE integration')
  if vim.fn.has('nvim-0.10') == 1 then vim.health.ok('Neovim >= 0.10')
  else vim.health.error('Neovim 0.10 or newer is required') end
  if vim.fn.executable('rg') == 1 then vim.health.ok('ripgrep is available')
  else vim.health.error('Install ripgrep for workspace search') end
  local config = require('mucli').config
  if not config then vim.health.warn("Call require('mucli').setup() first"); return end
  if vim.fn.executable(config.command[1]) == 1 then vim.health.ok('Python command is executable')
  else vim.health.error('Cannot execute ' .. config.command[1]) end
  if config.analysis.provider.model == '' then vim.health.warn('Configure analysis.provider.model')
  else vim.health.ok('analysis: ' .. config.analysis.provider.name .. '/' .. config.analysis.provider.model) end
  vim.health.info('The configured Python must have Mucli installed. No provider requests are made by this health check.')
end
return M
