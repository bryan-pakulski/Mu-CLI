local M = {}

local context = require("mucli.context")
local conversation = require("mucli.conversation")

local function stage(first, last, whole_file, captured)
  if captured then return context.stage(captured, "turn") end
  if first and last then return context.add_selection(first, last, nil, { scope = "turn" }) end
  if whole_file then return context.add_file(nil, { scope = "turn" }) end
  return nil
end

function M.explain(first, last, captured)
  stage(first, last, not first, captured)
  conversation.send("Explain the selected/current code in terms of intent, data flow, important invariants, and likely edge cases. Be concise but technically precise. Do not modify files.")
end

function M.improve(first, last, captured)
  stage(first, last, not first, captured)
  conversation.send("Improve the selected/current code. Prioritise correctness and clarity, preserve public behaviour, inspect related code where necessary, and present every modification through the native diff approval flow.")
end

function M.fix(first, last, captured)
  if first or captured then
    stage(first, last, false, captured)
  else
    context.add_diagnostics(nil, { scope = "turn" })
    context.add_file(nil, { scope = "turn" })
  end
  conversation.send("Fix the selected code or active diagnostics. Identify the root cause, make the smallest complete change, update relevant tests, and present modifications through the native diff approval flow.")
end

function M.tests(first, last, captured)
  stage(first, last, not first, captured)
  conversation.send("Generate or strengthen tests for this code. Cover its contract, important boundary cases, and the failure mode most likely to regress. Follow the repository's existing test conventions and show diffs for approval.")
end

function M.docs(first, last, captured)
  stage(first, last, not first, captured)
  conversation.send("Improve documentation for this code without restating the implementation. Capture the public contract, non-obvious constraints, parameters, failure behaviour, and a useful example where appropriate. Show the diff for approval.")
end

function M.review(first, last)
  require("mucli.hints").analyze(first, last)
end

function M.open(first, last, captured)
  local choices = {
    { label = "Ask about this", action = function()
      if first or captured then stage(first, last, false, captured) end
      conversation.ask()
    end },
    { label = "Explain code", action = function() M.explain(first, last, captured) end },
    { label = "Improve code", action = function() M.improve(first, last, captured) end },
    { label = "Fix diagnostics / bug", action = function() M.fix(first, last, captured) end },
    { label = "Review as inline hints", action = function() M.review(first, last) end },
    { label = "Generate tests", action = function() M.tests(first, last, captured) end },
    { label = "Improve documentation", action = function() M.docs(first, last, captured) end },
    { label = "Generate inline completion", action = require("mucli.completion").request },
    { label = "Add context…", action = context.picker },
    { label = "Open latest diff", action = require("mucli.diff").open_last },
  }
  vim.ui.select(choices, { prompt = "MUCLI code actions", format_item = function(item) return item.label end }, function(choice)
    if choice then choice.action() end
  end)
end

function M.open_visual()
  local captured = context.capture_selection()
  if not captured then return require("mucli.util").notify("No visual selection available", vim.log.levels.WARN) end
  M.open(captured.start_line, captured.end_line, captured)
end

return M
