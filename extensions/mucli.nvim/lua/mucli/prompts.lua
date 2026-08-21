local M = { active = {} }

local client = require("mucli.client")
local util = require("mucli.util")

local function clean(text)
  return tostring(text or "MUCLI needs your input"):gsub("%[/?[%w ]+%]", ""):gsub("^%s+", ""):gsub("%s+$", "")
end

function M.answer(id, payload)
  if not id then return end
  client.post("/api/prompts/" .. util.encode_query(id) .. "/answer", payload, function(response)
    if not response.ok then util.notify("Could not answer MUCLI prompt: " .. tostring(response.error), vim.log.levels.ERROR) end
  end)
  M.active[id] = nil
end

function M.cancel(id)
  if not id then return end
  client.post("/api/prompts/" .. util.encode_query(id) .. "/cancel", {}, function() end)
  M.active[id] = nil
end

local function approval(id, prompt)
  local function decide(decision, reason)
    M.answer(id, { approved = decision == "accept", remember = false, reason = reason })
  end
  if prompt.modifications and #prompt.modifications > 0 then
    require("mucli.diff").review_approval(prompt.modifications, decide, {
      can_approve = prompt.can_approve ~= false,
      block_reason = prompt.preview_error or "MUCLI marked this proposal as unsafe to approve",
    })
    return
  end
  local choices = {
    { label = "Approve", decision = "accept" },
    { label = "Reject", decision = "reject" },
    { label = "Reject with feedback", decision = "explain" },
  }
  if prompt.can_approve == false then table.remove(choices, 1) end
  vim.ui.select(choices, {
    prompt = clean(prompt.message or ("Allow " .. tostring(prompt.tool_name or "tool") .. "?")),
    format_item = function(item) return item.label end,
  }, function(choice)
    if not choice then decide("reject", "Approval dismissed"); return end
    if choice.decision ~= "explain" then decide(choice.decision); return end
    vim.ui.input({ prompt = "Feedback for MUCLI: " }, function(reason)
      decide("reject", reason or "User rejected the tool call")
    end)
  end)
end

local function option_value(item)
  return type(item) == "table" and (item.value or item.label) or item
end

local function choices(id, prompt)
  local values = vim.deepcopy(prompt.choices or prompt.options or {})
  local title = clean(prompt.message or prompt.question)
  if prompt.description and prompt.description ~= "" then title = title .. " — " .. clean(prompt.description) end

  if prompt.shape == "choice" and prompt.multi_select then
    local selected = {}
    local function pick()
      local menu = {
        { control = "done", label = "✓ Submit selections" },
        { control = "cancel", label = "× Cancel" },
      }
      if prompt.allow_other then menu[#menu + 1] = { control = "other", label = "+ Other…" } end
      for index, item in ipairs(values) do
        menu[#menu + 1] = { index = index, item = item }
      end
      vim.ui.select(menu, {
        prompt = title,
        format_item = function(entry)
          if entry.control then return entry.label end
          return (selected[entry.index] and "● " or "○ ") .. tostring(option_value(entry.item))
        end,
      }, function(entry)
        if not entry or entry.control == "cancel" then M.answer(id, { cancelled = true }); return end
        if entry.control == "done" then
          local result = {}
          for index, item in ipairs(values) do if selected[index] then result[#result + 1] = option_value(item) end end
          M.answer(id, { selected = result, other_text = "" })
          return
        end
        if entry.control == "other" then
          vim.ui.input({ prompt = "Other answer: " }, function(value)
            if value and value ~= "" then M.answer(id, { selected = {}, other_text = value }) else pick() end
          end)
          return
        end
        selected[entry.index] = not selected[entry.index]
        pick()
      end)
    end
    pick()
    return
  end

  if prompt.shape == "choice" and prompt.allow_other then
    values[#values + 1] = { _mucli_other = true, label = "Other…" }
  end
  vim.ui.select(values, {
    prompt = title,
    format_item = function(item)
      if type(item) == "table" then return item.label or item.value or vim.inspect(item) end
      return tostring(item)
    end,
  }, function(choice)
    if not choice then M.answer(id, { cancelled = true }); return end
    if type(choice) == "table" and choice._mucli_other then
      vim.ui.input({ prompt = "Other answer: " }, function(value)
        if value == nil then M.answer(id, { cancelled = true })
        else M.answer(id, { selected = {}, other_text = value }) end
      end)
      return
    end
    local value = option_value(choice)
    if prompt.shape == "choice" then M.answer(id, { selected = { value }, other_text = "" })
    else M.answer(id, { value = value }) end
  end)
end

local function quiz(id, prompt)
  local questions = prompt.questions or {}
  local answers, index = {}, 1
  local function next_question()
    local question = questions[index]
    if not question then M.answer(id, { answers = answers }); return end
    local qid = tostring(question.qid or index)
    local title = ("Question %d/%d · %s"):format(index, #questions, clean(question.prompt))
    if question.kind == "fill_blank" then
      vim.ui.input({ prompt = title .. ": " }, function(value)
        if value == nil then M.answer(id, { cancelled = true }); return end
        answers[qid] = value
        index = index + 1
        next_question()
      end)
      return
    end
    vim.ui.select(question.options or {}, { prompt = title, format_item = tostring }, function(value)
      if value == nil then M.answer(id, { cancelled = true }); return end
      answers[qid] = value
      index = index + 1
      next_question()
    end)
  end
  next_question()
end

function M.handle(event)
  if event.kind == "prompt_cancelled" or event.kind == "prompt_resolved" then
    M.active[event.id] = nil
    return
  end
  if event.kind ~= "prompt" then return end
  local prompt = event.prompt or event.payload or {}
  local id = event.id or prompt.id
  if not id or M.active[id] then return end
  M.active[id] = prompt
  local shape = prompt.shape or "input"
  if shape == "tool_approval" then
    approval(id, prompt)
  elseif shape == "confirm" then
    vim.ui.select({ true, false }, { prompt = clean(prompt.message), format_item = function(value) return value and "Yes" or "No" end }, function(value)
      if value == nil then M.answer(id, { cancelled = true }) else M.answer(id, { value = value }) end
    end)
  elseif shape == "choices" or shape == "choice" then
    choices(id, prompt)
  elseif shape == "quiz" then
    quiz(id, prompt)
  elseif shape == "input" then
    vim.ui.input({ prompt = clean(prompt.message) .. ": ", default = prompt.default }, function(value)
      if value == nil then M.answer(id, { cancelled = true }) else M.answer(id, { value = value }) end
    end)
  else
    util.notify("Unsupported MUCLI prompt type: " .. tostring(shape), vim.log.levels.WARN)
    M.answer(id, { cancelled = true })
  end
end

return M
