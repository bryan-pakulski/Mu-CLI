# Gemini CLI

A powerful, interactive command-line interface for Google's Gemini models. This tool features **long-term vector memory** (RAG), persistent chat sessions, file attachments, and rich syntax highlighting, all from y

## Features

- **Long-term Vector Memory**: Uses **ChromaDB** to store and index conversation history. The model automatically recalls relevant details from past turns (even those outside the active context window) using seman
- **Persistent Sessions**: Chat history is saved automatically (`~/.gemini_chats/`). You can switch between conversations, list them, or delete them.
- **Rich UI**: Beautiful terminal formatting using the `Rich` library, including:
  - Markdown rendering with panels.
  - Syntax highlighting for code blocks (easy to copy).
  - Spinners and status indicators.
- **File Attachments**: Upload local files (images, PDFs, text) to the context using `/file`.
- **Smart Input**:
  - **Autocomplete**: Context-aware tab-completion for commands, file paths, and session names.
  - **Multiline Editing**: Press `Meta+Enter` (or `Esc` then `Enter`) to submit, allowing easy entry of long prompts or code.
- **Thinking Mode**: Toggle Gemini's "thinking" process for complex reasoning tasks.
- **System Instructions**: Set custom system prompts on the fly.

## Prerequisites

1. **Python 3.9+**
2. **Google GenAI SDK**
3. **Glow** (Optional, for pretty Markdown rendering):
    - macOS: `brew install glow`
    - Linux: `sudo apt install glow` (or via package manager)

## Installation

1. Clone the repository:

```bash
git clone https://github.com/yourusername/gemini-cli.git                                        
cd gemini-cli                                                                                   
```

1. Install dependencies:

```bash
pip install -r requirements.txt                                                                 
```

1. Set your API Key:
    You need a Google Gemini API key. Export it as an environment variable:

```bash
export GOOGLE_API_KEY="your_api_key_here"                                                       
```

1. Make the script executable (optional):

```bash
chmod +x gemini_cli.py                                                                          
```

## Usage

Run the script:

```bash
python gemini_cli.py                                                                            
```

### Command Arguments

| Argument | Description |
| :--- | :--- |
| `--model` | Set default model (default: `gemini-3-pro-preview`) |
| `--thinking` | Start with thinking mode enabled |
| `--system` | Set initial system instruction |
| `--debug` | Enable debug logs for memory retrieval and vector operations |

### In-Chat Commands

| Command | Alias | Action |
| :--- | :--- | :--- |
| `/help` | `/h` | Show help menu |
| `/new [name]` | | Start a new chat session |
| `/list` | `/ls` | List available saved sessions |
| `/load [name]` | `/open` | Switch to a specific session |
| `/delete [name]`| `/rm` | Delete a session and its memory index |
| `/file [path]` | `/f` | Attach a file to the next message |
| `/clearfiles` | `/cf` | Clear currently staged files |
| `/view` | `/v` | View full history of current chat |
| `/clear` | `/c` | Clear history of current chat |
| `/search [query]`| | Manually search vector memory for a topic |
| `/reindex` | | Force rebuild of vector memory from chat history |
| `/system [txt]` | `/sys` | Update system prompt |
| `/tokens` | | View context stats (window size vs total history) |
| `/model [name]` | | View or change the active model |
| `/thinking` | | Toggle thinking mode on/off |
| `/quit` | `/q` | Exit the application |

## Input Shortcuts

- **Submit Message**: Press `Meta+Enter` (Alt+Enter) OR `Esc` followed by `Enter`.
- **New Line**: Press `Enter` (Standard multiline editing is enabled).
- **Autocomplete**: Press `Tab` to complete commands or file paths.

## Tips

- **Memory Retrieval**: The tool automatically queries memory before sending your prompt. If you want to see what the AI "remembers" about a specific topic without sending a message to the API, use `/search <topic
- **File Uploads**: You can attach multiple files before sending a message. Use `/f path/to/file1`, then `/f path/to/file2`, then type your prompt.

## License

MIT
