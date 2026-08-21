if vim.g.loaded_mucli_nvim then return end
vim.g.loaded_mucli_nvim = true

local function core() return require("mucli").ensure_setup() end

local function command(name, fn, opts)
  vim.api.nvim_create_user_command(name, fn, opts or {})
end

local function range(args)
  if args.range and args.range > 0 then return args.line1, args.line2 end
  return nil, nil
end

command("Mucli", function() core().with_ready(require("mucli.chat.panel").toggle) end, { desc = "Toggle MUCLI editor" })
command("MucliAsk", function(args) core().with_ready(function() require("mucli.conversation").ask(args.args) end) end, { nargs = "*", desc = "Ask MUCLI" })
command("MucliActions", function(args)
  local first, last = range(args)
  core().with_ready(function() require("mucli.actions").open(first, last) end)
end, { range = true, desc = "Open MUCLI code actions" })
command("MucliSend", function(args)
  local first, last = range(args)
  core().with_ready(function() require("mucli.conversation").send_selection(args.args, first, last) end)
end, { range = true, nargs = "*", desc = "Send selection to MUCLI" })
command("MucliSendFile", function(args) core().with_ready(function() require("mucli.conversation").send_file(args.args) end) end, { nargs = "*", desc = "Send current file to MUCLI" })
command("MucliExplain", function(args)
  local first, last = range(args); core().with_ready(function() require("mucli.actions").explain(first, last) end)
end, { range = true, desc = "Explain selected/current code" })
command("MucliImprove", function(args)
  local first, last = range(args); core().with_ready(function() require("mucli.actions").improve(first, last) end)
end, { range = true, desc = "Improve selected/current code" })
command("MucliFix", function(args)
  local first, last = range(args); core().with_ready(function() require("mucli.actions").fix(first, last) end)
end, { range = true, desc = "Fix selected/current code" })
command("MucliReview", function(args)
  local first, last = range(args); core().with_ready(function() require("mucli.hints").analyze(first, last) end)
end, { range = true, desc = "Publish MUCLI review hints" })
command("MucliHints", function(args)
  local first, last = range(args); core().with_ready(function() require("mucli.hints").analyze(first, last) end)
end, { range = true, desc = "Publish MUCLI hints" })
command("MucliHintsClear", function() require("mucli.hints").clear() end, { desc = "Clear MUCLI hints" })
command("MucliHintAction", function() require("mucli.hints").action() end, { desc = "Act on nearest MUCLI hint" })
command("MucliComplete", function() core().with_ready(require("mucli.completion").request) end, { desc = "Request inline MUCLI completion" })
command("MucliCompleteAccept", function(args)
  if args.args == "word" then require("mucli.completion").accept_word() else require("mucli.completion").accept() end
end, { nargs = "?", complete = function() return { "word" } end, desc = "Accept MUCLI completion" })
command("MucliCompleteDismiss", require("mucli.completion").clear, { desc = "Dismiss MUCLI completion" })
command("MucliContext", require("mucli.context_panel").toggle, { desc = "Inspect and manage MUCLI context" })
command("MucliContextAdd", require("mucli.context").picker, { desc = "Add MUCLI context" })
command("MucliContextInspect", require("mucli.context_panel").inspect, { desc = "Inspect the exact MUCLI context payload" })
command("MucliContextClear", function() require("mucli.context").clear() end, { desc = "Clear MUCLI context" })
command("MucliContextClearTurn", require("mucli.context").clear_turn, { desc = "Clear turn-only MUCLI context" })
command("MucliContextClearPinned", require("mucli.context").clear_pinned, { desc = "Clear pinned MUCLI context" })
command("MucliDiff", require("mucli.diff").open_last, { desc = "Open latest MUCLI diff" })
command("MucliInterrupt", require("mucli.chat.input").interrupt, { desc = "Interrupt MUCLI turn" })
command("MucliSetup", function() core(); require("mucli.wizard").start() end, { desc = "Configure MUCLI" })
command("MucliConfig", function() core(); require("mucli.wizard").reconfigure() end, { desc = "Configure MUCLI" })
command("MucliModel", function(args)
  core()
  if args.args == "" then require("mucli.wizard").switch_model(); return end
  local provider = require("mucli.store").state.provider or require("mucli.config").get().provider
  if not provider then require("mucli.wizard").switch_provider(); return end
  require("mucli.session").switch_provider(provider, args.args, function(_, response)
    if not response.ok then require("mucli.util").notify(response.error, vim.log.levels.ERROR) end
  end)
end, { nargs = "?", desc = "Switch MUCLI model" })
command("MucliProvider", function(args)
  core()
  if args.args == "" then require("mucli.wizard").switch_provider(); return end
  require("mucli.session").fetch_models(args.args, function(models)
    vim.ui.select(models, { prompt = "MUCLI model" }, function(model)
      if model then require("mucli.session").switch_provider(args.args, model, function() end) end
    end)
  end)
end, { nargs = "?", complete = function() return { "openai", "gemini", "ollama" } end, desc = "Switch MUCLI provider" })
command("MucliSession", function(args)
  core()
  if args.args == "" then require("mucli.wizard").start(); return end
  local cfg = require("mucli.config").get()
  cfg.session, cfg.provider, cfg.model = args.args, nil, nil
  require("mucli").reconnect(function(success, err)
    if not success then require("mucli.util").notify(err, vim.log.levels.ERROR) end
  end)
end, { nargs = "?", desc = "Switch MUCLI session" })
command("MucliHealth", function() vim.cmd("checkhealth mucli") end, { desc = "Check MUCLI integration" })

local ok, which_key = pcall(require, "which-key")
if ok and which_key.add then which_key.add({ { "<leader>m", group = "MUCLI" } }) end
