local failures = {}
local passed = 0

vim.notify = function() end

local function fail(message)
  error(message, 2)
end

local function eq(actual, expected, message)
  if not vim.deep_equal(actual, expected) then
    fail((message or "values differ") .. "\nexpected: " .. vim.inspect(expected) .. "\nactual:   " .. vim.inspect(actual))
  end
end

local function truthy(value, message)
  if not value then fail(message or "expected a truthy value") end
end

local function test(name, fn)
  local ok, err = xpcall(fn, debug.traceback)
  if ok then
    passed = passed + 1
    print("ok  " .. name)
  else
    failures[#failures + 1] = name .. "\n" .. err
    print("not ok  " .. name)
  end
end

local root = vim.fn.getcwd()
require("mucli.config").setup({
  auto_connect = false,
  session = "nvim-tests",
  workspace = { root = root },
  context = { clear_staged_after_send = true },
})

test("SSE parser handles fragmented CRLF events", function()
  local events = {}
  local parser = require("mucli.sse").Parser.new(function(event, name)
    events[#events + 1] = { event = event, name = name }
  end)
  parser:feed('event: update\r\ndata: {"kind":"assistant_')
  parser:feed('delta","text":"hello"}\r')
  parser:feed('\n\r')
  parser:feed('\n')
  eq(#events, 1)
  eq(events[1].name, "update")
  eq(events[1].event.text, "hello")
end)

test("SSE parser joins multiline data and reports invalid JSON", function()
  local events = {}
  local parser = require("mucli.sse").Parser.new(function(event) events[#events + 1] = event end)
  parser:feed('data: {"kind":"hello",\ndata: "busy":[]}\n\n')
  parser:feed("data: definitely-not-json\n\n")
  eq(events[1].kind, "hello")
  eq(events[2].kind, "protocol_error")
end)

test("HTTP response parser separates JSON body and status", function()
  local parsed = require("mucli.client")._parse_http('{"ok":true}\n201', 0, "")
  truthy(parsed.ok)
  eq(parsed.status, 201)
  eq(parsed.json.ok, true)
  local failed = require("mucli.client")._parse_http('{"detail":"bad"}\n409', 0, "")
  eq(failed.ok, false)
  eq(failed.error, "bad")
end)

test("unified diff parser applies standard and omitted counts", function()
  local diff = require("mucli.diff")
  local hunks = diff.parse_unified_diff(table.concat({
    "--- a/example.txt",
    "+++ b/example.txt",
    "@@ -1,3 +1,4 @@",
    " one",
    "-two",
    "+TWO",
    "+two-and-a-half",
    " three",
  }, "\n"))
  local result, err = diff.apply_hunks({ "one", "two", "three" }, hunks)
  truthy(result, err)
  eq(result, { "one", "TWO", "two-and-a-half", "three" })
  local single = diff.parse_unified_diff("@@ -2 +2 @@\n-two\n+TWO")
  eq(single[1].old_count, 1)
  eq(single[1].new_count, 1)
end)

test("unified diff rejects missing hunks and context mismatches", function()
  local diff = require("mucli.diff")
  local result, err = diff.apply_hunks({ "a" }, {})
  eq(result, nil)
  truthy(err:match("No unified diff hunks"))
  result, err = diff.apply_hunks({ "wrong" }, diff.parse_unified_diff("@@ -1 +1 @@\n-right\n+new"))
  eq(result, nil)
  truthy(err:match("Removal mismatch"))
end)

test("diff approval honors a server-side approval block", function()
  local diff = require("mucli.diff")
  local decision
  diff.open({ {
    path = root .. "/blocked.lua",
    original = "return false",
    proposed = "return true",
  } }, function(value) decision = value end, {
    can_approve = false,
    block_reason = "preview validation failed",
  })
  diff.accept()
  eq(decision, nil)
  truthy(diff.is_open())
  diff.reject()
  eq(decision, "reject")
  eq(diff.is_open(), false)
end)

test("diff approval rechecks live buffer conflicts", function()
  local diff = require("mucli.diff")
  local source = vim.api.nvim_create_buf(true, false)
  local path = root .. "/live-conflict.lua"
  vim.api.nvim_buf_set_name(source, path)
  vim.api.nvim_buf_set_lines(source, 0, -1, false, { "return false" })
  vim.bo[source].modified = false
  local decision
  diff.open({ {
    path = path,
    original = "return false",
    proposed = "return true",
    changedtick = vim.api.nvim_buf_get_changedtick(source),
  } }, function(value) decision = value end)
  vim.api.nvim_buf_set_lines(source, 0, -1, false, { "return 'changed'" })
  diff.accept()
  eq(decision, nil)
  truthy(diff.is_open())
  diff.reject()

  diff.capture_event({
    filename = root .. "/newest.lua", original = "old", new = "new",
  })
  eq(diff.last[#diff.last].path, root .. "/newest.lua")
end)

test("structured hints and multiline completions parse", function()
  local hints = require("mucli.hints").parse_response(
    '<mucli-hints>{"hints":[{"line":2,"message":"Use a guard"}]}</mucli-hints>'
  )
  eq(hints[1].line, 2)
  local completion = require("mucli.completion").parse_response(
    "<mucli-completion>first\nsecond</mucli-completion>"
  )
  eq(completion, "first\nsecond")
end)

test("conversation store streams one assistant turn", function()
  local store = require("mucli.store")
  store.reset()
  store.handle({ kind = "assistant_start", turn_id = "turn-1" })
  store.handle({ kind = "assistant_delta", turn_id = "turn-1", text = "hello" })
  store.handle({ kind = "assistant_delta", turn_id = "turn-1", text = " world" })
  store.handle({ kind = "tool_call", tool_name = "read_file" })
  store.handle({ kind = "assistant_end", turn_id = "turn-1" })
  eq(#store.state.messages, 1)
  eq(store.state.messages[1].text, "hello world")
  eq(store.state.messages[1].activities[1].label, "read_file")

  store.load_history({ turns = { {
    role = "user",
    parts = { { type = "text", text = "Fix this\n\n## MUCLI editor context\nWorkspace: `/tmp/project`" } },
  } } })
  eq(store.state.messages[1].text, "Fix this")

  store.handle({ kind = "user_message", text = "/status" })
  eq(store.state.busy, true)
  store.handle({ kind = "command_result", result = { ok = true } })
  eq(store.state.busy, false)
end)

test("editor intelligence requests stay out of chat history", function()
  local client = require("mucli.client")
  local session = require("mucli.session")
  local store = require("mucli.store")
  local original_post = client.post
  local captured, completed
  session.client_id = "nvim-test-client"
  store.reset()
  store.state.busy = false
  client.post = function(path, body, callback)
    captured = { path = path, body = body }
    callback({ ok = true, json = { text = "structured response" } })
  end
  local ok, err = pcall(function()
    truthy(require("mucli.conversation").ephemeral("analyze", {
      kind = "hints",
      on_complete = function(text) completed = text end,
    }))
  end)
  client.post = original_post
  if not ok then error(err) end
  eq(captured.path, "/api/extensions/neovim/request")
  eq(captured.body.session_name, "nvim-tests")
  eq(completed, "structured response")
  eq(#store.state.messages, 0)
  eq(store.state.busy, false)
end)

test("staged context survives failed sends and clears after acceptance", function()
  local client = require("mucli.client")
  local context = require("mucli.context")
  local conversation = require("mucli.conversation")
  local store = require("mucli.store")
  local original_post = client.post
  context.clear()
  store.reset()
  store.state.busy = false
  context.stage({
    type = "file", path = root .. "/retry.lua", relative_path = "retry.lua",
    content = "return true", filetype = "lua", changedtick = 1,
  })
  client.post = function(_, _, callback) callback({ ok = false, error = "offline" }) end
  truthy(conversation.send("First attempt", { open_panel = false }))
  eq(#context.items, 1)

  client.post = function(_, _, callback) callback({ ok = true, json = { accepted = true } }) end
  truthy(conversation.send("Retry", { open_panel = false }))
  eq(#context.items, 0)
  client.post = original_post
end)

test("choice prompts support multi-select and quiz payloads", function()
  local client = require("mucli.client")
  local prompts = require("mucli.prompts")
  local original_post, original_select, original_input = client.post, vim.ui.select, vim.ui.input
  local answers, select_count = {}, 0
  client.post = function(path, body, callback)
    answers[#answers + 1] = { path = path, body = body }
    if callback then callback({ ok = true }) end
  end
  vim.ui.select = function(items, _, callback)
    select_count = select_count + 1
    if select_count == 1 then callback(items[3]) -- toggle first multi-select option
    elseif select_count == 2 then callback(items[1]) -- submit selections
    else callback(items[1]) end -- first quiz option
  end
  vim.ui.input = function(_, callback) callback("typed answer") end
  local ok, err = pcall(function()
    prompts.handle({
      kind = "prompt", id = "multi", prompt = {
        shape = "choice", question = "Pick", multi_select = true,
        options = { "alpha", "beta" },
      },
    })
    prompts.handle({
      kind = "prompt", id = "quiz", prompt = {
        shape = "quiz", questions = {
          { qid = "one", prompt = "First?", kind = "multiple_choice", options = { "A", "B" } },
          { qid = "two", prompt = "Second?", kind = "fill_blank" },
        },
      },
    })
  end)
  client.post, vim.ui.select, vim.ui.input = original_post, original_select, original_input
  if not ok then error(err) end
  eq(answers[1].body.selected, { "alpha" })
  eq(answers[2].body.answers, { one = "A", two = "typed answer" })
end)

test("exact visual selection and staged-count metadata are preserved", function()
  local context = require("mucli.context")
  context.items = {}
  local buf = vim.api.nvim_create_buf(true, false)
  vim.api.nvim_buf_set_name(buf, root .. "/selection_fixture.lua")
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, { "alpha beta gamma", "second" })
  vim.api.nvim_set_current_buf(buf)
  vim.fn.setpos("'<", { buf, 1, 7, 0 })
  vim.fn.setpos("'>", { buf, 1, 10, 0 })
  local selection = context.capture_selection()
  eq(selection.content, "beta")
  context.stage(selection)
  local _, metadata = context.compose("question")
  eq(metadata.staged_count, 1)
  eq(#context.items, 1)
  context.consume(metadata.staged_ids)
  eq(#context.items, 0)
  eq(context.latest_selection().content, "beta")
end)

test("editor buffer tool returns changedtick and blocks outside paths", function()
  local tools = require("mucli.tools")
  local current = vim.api.nvim_get_current_buf()
  local result = tools.get_buffer({})
  truthy(result.ok)
  eq(result.data.changedtick, vim.api.nvim_buf_get_changedtick(current))
  local denied = tools.get_buffer({ file_path = "/tmp/outside-mucli-workspace.txt" })
  eq(denied.ok, false)
  truthy(denied.error:match("outside"))
  local secret = tools.get_buffer({ file_path = ".env" })
  eq(secret.ok, false)
  truthy(secret.error:match("Secret%-path"))
  truthy(require("mucli.util").is_secret_path(root .. "/credentials-prod.json"))

  local outside = vim.api.nvim_create_buf(true, false)
  vim.api.nvim_buf_set_name(outside, "/tmp/outside-mucli-buffer.txt")
  vim.api.nvim_buf_set_lines(outside, 0, -1, false, { "private" })
  local listed = tools.list_buffers()
  for _, item in ipairs(listed.data.buffers) do
    truthy(item.path ~= "/tmp/outside-mucli-buffer.txt")
  end

  local boundary = vim.fn.tempname()
  vim.fn.mkdir(boundary .. "/inside", "p")
  vim.fn.mkdir(boundary .. "/outside", "p")
  local linked = (vim.uv or vim.loop).fs_symlink(
    boundary .. "/outside", boundary .. "/inside/escape"
  )
  truthy(linked)
  eq(require("mucli.util").is_within(
    boundary .. "/inside/escape/new.txt", boundary .. "/inside"
  ), false)
end)

test("partial completion inserts text, moves cursor, and keeps remainder", function()
  local completion = require("mucli.completion")
  completion.setup()
  local buf = vim.api.nvim_create_buf(true, false)
  vim.api.nvim_buf_set_name(buf, root .. "/completion_fixture.lua")
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, { "foo" })
  vim.api.nvim_set_current_buf(buf)
  vim.api.nvim_win_set_cursor(0, { 1, 3 })
  completion.suggestion = {
    buf = buf, row = 0, col = 3,
    tick = vim.api.nvim_buf_get_changedtick(buf), text = " bar baz",
  }
  truthy(completion.accept_word())
  eq(vim.api.nvim_get_current_line(), "foo bar")
  -- Normal-mode cursors sit on the final byte; insert-mode mappings resume
  -- immediately after it.
  eq(vim.api.nvim_win_get_cursor(0), { 1, 6 })
  eq(completion.suggestion.text, " baz")
  vim.api.nvim_win_set_cursor(0, { 1, 0 })
  vim.api.nvim_exec_autocmds("CursorMoved", { buffer = buf })
  eq(completion.suggestion, nil)
end)

test("chat dock creates persistent conversation and composer windows", function()
  local panel = require("mucli.chat.panel")
  panel.open(false)
  truthy(panel.is_open())
  eq(vim.bo[panel.get_buf()].buftype, "nofile")
  eq(vim.bo[panel.get_input_buf()].modifiable, true)
  panel.close()
  eq(panel.is_open(), false)
end)

test("plugin command surface loads on Neovim 0.10+", function()
  vim.cmd("runtime plugin/mucli.lua")
  for _, name in ipairs({
    "Mucli", "MucliAsk", "MucliActions", "MucliReview", "MucliComplete",
    "MucliContext", "MucliDiff", "MucliInterrupt", "MucliHealth",
  }) do
    eq(vim.fn.exists(":" .. name), 2, name .. " was not registered")
  end
end)

if #failures > 0 then
  error(("%d test(s) failed:\n\n%s"):format(#failures, table.concat(failures, "\n\n")))
end

print(("MUCLI Neovim tests: %d passed"):format(passed))
