--- plugin/mucli.lua — Neovim user commands for mucli.
-- Defines :Mucli, :MucliSend, :MucliSendFile, :MucliInterrupt, :MucliModel, :MucliSession.
-- Registers which-key groups if which-key is available.
-- This file is auto-loaded by Neovim when the plugin is on the runtime path.

local function cmd(name, opts)
  vim.api.nvim_create_user_command(name, opts[1], opts[2] or {})
end

cmd("Mucli", {
  function()
    require("mucli.chat.panel").toggle()
  end,
  { desc = "Toggle mucli chat panel" },
})

cmd("MucliSend", {
  function(args)
    local context = require("mucli.context")
    local prompt = args.args and args.args ~= "" and args.args or nil
    if prompt then
      -- User provided prompt as argument: :MucliSend "fix this code"
      context.send_visual_with_prompt(prompt)
    else
      -- No prompt arg — prompt interactively
      context.send_visual()
    end
  end,
  { range = true, nargs = "*", desc = "Send visual selection + prompt to mucli" },
})

cmd("MucliSendFile", {
  function(args)
    local context = require("mucli.context")
    local prompt = args.args and args.args ~= "" and args.args or nil
    if prompt then
      context.send_file_with_prompt(prompt)
    else
      context.send_file()
    end
  end,
  { nargs = "*", desc = "Send current file + prompt to mucli" },
})

cmd("MucliInterrupt", {
  function()
    local config = require("mucli.config")
    require("mucli.client").post("/api/chat/interrupt", { session_name = config.opts.session }, function(resp)
      vim.schedule(function()
        if resp and resp.ok then
          vim.notify("mucli: interrupt sent", vim.log.levels.INFO)
        else
          vim.notify("mucli: failed to interrupt", vim.log.levels.ERROR)
        end
      end)
    end)
  end,
  { desc = "Interrupt active mucli turn" },
})

cmd("MucliModel", {
  function(args)
    local session = require("mucli.session")
    if args.args and args.args ~= "" then
      -- Direct model name provided
      session.switch_model(args.args)
    else
      -- Show model picker via completion
      session.fetch_models(function(models)
        if not models or #models == 0 then
          vim.notify("mucli: no models available", vim.log.levels.WARN)
          return
        end
        vim.ui.select(models, { prompt = "Select model:" }, function(choice)
          if choice then
            session.switch_model(choice)
          end
        end)
      end)
    end
  end,
  {
    desc = "Switch mucli model",
    nargs = "*",
    complete = function()
      -- Async completion — return empty, actual list shown via ui.select
      return {}
    end,
  },
})

cmd("MucliSession", {
  function()
    local session = require("mucli.session")
    local config = require("mucli.config")
    session.get_active(function(info)
      vim.schedule(function()
        if info then
          vim.notify(
            string.format("mucli session: %s | model: %s | mode: %s", info.name or config.opts.session, info.model or "?", info.agent_mode or "?"),
            vim.log.levels.INFO
          )
        else
          vim.notify("mucli: no active session", vim.log.levels.WARN)
        end
      end)
    end)
  end,
  { desc = "Show mucli session status" },
})

cmd("MucliConfig", {
  function()
    require("mucli.wizard").reconfigure()
  end,
  { desc = "Configure mucli session/provider/model" },
})

cmd("MucliProvider", {
  function(args)
    local session = require("mucli.session")
    local config = require("mucli.config")
    if args.args and args.args ~= "" then
      -- Direct provider name provided — switch immediately
      local providers = { gemini = true, ollama = true, openai = true }
      if not providers[args.args] then
        vim.notify("mucli: unknown provider '" .. args.args .. "'. Valid: gemini, ollama, openai", vim.log.levels.ERROR)
        return
      end
      -- For ollama, prompt for local/cloud
      if args.args == "ollama" then
        require("mucli.wizard")._pick_ollama_mode(config.opts.session, true, args.args, function(opts)
          vim.notify("[mucli] Provider switched to: " .. tostring(opts.provider), vim.log.levels.INFO)
        end)
      else
        -- Fetch models for new provider and prompt
        require("mucli.wizard")._pick_model(config.opts.session, true, args.args, nil, function(opts)
          vim.notify("[mucli] Provider switched to: " .. tostring(opts.provider), vim.log.levels.INFO)
        end)
      end
    else
      -- Interactive provider picker
      require("mucli.wizard")._pick_provider(config.opts.session, true, function(opts)
        vim.notify("[mucli] Provider switched to: " .. tostring(opts.provider), vim.log.levels.INFO)
      end)
    end
  end,
  {
    desc = "Switch mucli provider (gemini|ollama|openai)",
    nargs = "?",
    complete = function() return { "gemini", "ollama", "openai" } end,
  },
})

-- Register which-key groups if available
local ok, wk = pcall(require, "which-key")
if ok then
  wk.add({
    { "<leader>m", group = "mucli" },
    { "<leader>mt", desc = "Toggle chat panel", cmd = require("mucli.chat.panel").toggle },
    { "<leader>ms", desc = "Send visual selection", mode = "v" },
    { "<leader>mf", desc = "Send current file" },
    { "<leader>mi", desc = "Interrupt turn" },
    { "<leader>d", group = "mucli diff" },
    { "<leader>da", desc = "Accept hunk" },
    { "<leader>dr", desc = "Reject hunk" },
  })
end