## Current Context (What we have built)                                                                                                                                                 
We have successfully transitioned MuCLI into a provider-agnostic, semi-agentic CLI tool.                                                                                                
																																													  
1.  **Provider Abstraction:** 
`LLMProvider` base class handles standardizing history. We have `GeminiProvider` and `OllamaProvider` implemented. 
You can switch providers dynamically using `/provider` or `--provider`.                                                                                                                                                       

2.  **Tool Definitions:** We built standard JSON schemas in `core/tools.py` for:                                                                                                        
  *   `read_file(filename)`                                                                                                                                                           
  *   `search_for_string(string)`                                                                                                                                                     
  *   `get_chunk(file, start, end)`                                                                                                                                                   
  *   `get_workspace_details()`                                                                                                                                                       

3.  **Lightweight Context (RAG):** When `/workspace <path>` is attached, we no longer dump the whole codebase into the system prompt. Instead, we use `FolderContext.get_tree_map()` to 
provide a lightweight file tree, and the LLM uses its tools to explore.                                                                                                                  

4.  **Agentic Loop:** `core/session.py` has a `while iteration < max_iterations:` loop. If the model returns a `tool_call`, the CLI automatically executes the local python function,   
appends a `tool_result`, and triggers the model again without user intervention.                                                                                                         

5.  **Variables State:** We added `/set <key> <val>` and `/get <key>`. These are saved to `SessionManager`'s JSON history.
These still need to be made useable as we would like to control system / session variable keystore with this.
Things like max_iterations, auto_approve etc..         
																																											  
---                                                                                                                                                                                     
																																													  
## Next Phase: (Agentic Guardrails & Polish)                                                                                                                                    
																																													  
Now that the AI can act autonomously, we need to add safety rails, testing, and better control over the execution loop.                                                                 
																																													  
### 1. Graceful Execution Interrupts (Ctrl C for the Agent)                                                                                                                             
**Problem:** The `while` loop runs up to 10 times (needs to be dynamically controlled). If the agent gets stuck in a hallucination loop (e.g., repeatedly searching for a bad file path), the user has no way to stop it     
without killing the entire Python process.                                                                                                                                               
**Implementation:**                                                                                                                                                                     
*   **File:** `core/session.py` inside `send_message()`.                                                                                                                                
*   **Change:** Wrap the internals of the `while iteration < max_iterations:` loop in a `try...except KeyboardInterrupt:` block.                                                        
*   **Action:** When triggered, print `"[yellow]Agentic loop interrupted by user.[/yellow]"`, append a `tool_result` to the history stating "User interrupted execution", and `break`   
the loop to return control to the prompt.                                                                                                                                                
																																													  
### 2. Workspace Boundary Enforcements (Sandboxing)                                                                                                                                     
**Problem:** The model could technically hallucinate and ask to read `../../../../etc/passwd` or code outside the attached workspace.                                                   
**Implementation:**                                                                                                                                                                     
*   **File:** `core/tools.py`.                                                                                                                                                          
*   **Change:** Update `read_file` and `get_chunk` to require the `folder_context`.                                                                                                     
*   **Action:** Resolve the requested `filename` using `os.path.abspath`. Check if that absolute path starts with any of the absolute paths in `folder_context.folders`.                
*   **Action:** If it falls *outside* the workspace:                                                                                                                                    
  *   Option A (Strict): Return a string to the model: `"Error: Access denied. File is outside the attached workspace."`                                                              
  *   Option B (Interactive): Pause and use `rich.prompt.Confirm.ask(f"Model is requesting access to {path} outside workspace. Allow?")`.                                             
																																													  
### 3. Tool Approval System (Y/N/E)                                                                                                                                                     
**Problem:** Right now tools auto-execute. For potentially destructive tools (if we add file writing later), or just to monitor token usage, the user should be able to vet the tool    
calls.                                                                                                                                                                                   
**Implementation:**                                                                                                                                                                     
*   **File:** `core/session.py` (Inside the Agentic Loop, before `execute_tool` is called).                                                                                             
*   **Change:** Add a setting (e.g., via `/set auto_approve false`).                                                                                                                    
*   **Action:** If auto-approval is off, when a tool is requested, pause the loop:                                                                                                      
  `choice = Prompt.ask(f"Allow {tool_name}({tool_args})?", choices=["y", "n", "e"])`                                                                                                  
  *   If `y`: Execute normally.                                                                                                                                                       
  *   If `n`: Do not execute. Pass `"User denied this tool call."` as the `tool_result`.                                                                                              
  *   If `e` (Explain): `reason = Prompt.ask("Provide an explanation to the model")`. Pass `"User denied this tool call. Reason: "   reason` as the `tool_result`.                    
																																													  
### 4. Tool Guarding (Dynamic Enable/Disable)                                                                                                                                           
**Problem:** The user might want to restrict the LLM from using certain tools to force a specific behavior.                                                                             
**Implementation:**                                                                                                                                                                     
*   **File:** `core/session.py` and `mucli.py`.                                                                                                                                         
*   **Change:** Create a state list `session.disabled_tools = []`.                                                                                                                      
*   **Action:** Add a command `/tool disable <name>` and `/tool enable <name>`.                                                                                                         
*   **Action:** When passing the `TOOLS` list to `provider.generate()`, filter out any tools whose names appear in `disabled_tools`.                                                    
																																													  
### 5. Error Feedback Loop                                                                                                                                                              
**Problem:** If `read_file` crashes (e.g., UnicodeDecodeError), the python script currently returns `"Error reading file: {e}"`.                                                        
**Implementation:**                                                                                                                                                                     
*   **File:** `core/tools.py`.                                                                                                                                                          
*   **Change:** Ensure all tools catch `Exception` and return a clean, descriptive string. This is partially done, but ensure the error string tells the LLM *how* to fix it (e.g.,     
`"Error: File not found. Try using search_for_string to locate it."`).                                                                                                                   
																																													  
### 6. Unit Tests                                                                                                                                                                       
**Problem:** The refactor was massive; we need automated regression testing.                                                                                                            
**Implementation:**                                                                                                                                                                     
*   **Folder:** Create a `/tests` directory.                                                                                                                                            
*   **Action:** Write tests using `pytest` for:                                                                                                                                         
  *   `core/workspace.py`: Ensure `get_tree_map()` formats correctly and respects ignored directories.                                                                                
  *   `core/session.py`: Ensure `_build_messages_from_history` accurately serializes/deserializes `tool_call` and `tool_result` dictionary structures into dataclasses.               
  *   `core/tools.py`: Test the new Workspace Boundary Enforcements (ensure it rejects paths with `../`).                                                                             
  *   `providers/ollama.py`: Test `_convert_messages` to ensure our standard format translates accurately to Ollama's `tool_calls` array format.              
