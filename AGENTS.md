# Contribution Guidelines

For any changes to this repository run the following commands on the Python files you modified:

```
ruff format --line-length 120 <files>
ruff check --fix <files>
python -m py_compile <files>
```

Replace `<files>` with the list of modules you changed. Make sure the repository is clean (`git status`) before committing.

## PR instructions
Keep pull request titles short and descriptive.

# Setup environment instrctions
That project use UV for package manager and all dependecies stores in `pyproject.toml`
So use `uv sync`.

# Running backend
Simply `uv run uvicorn backend.main:app --host $(HOST) --port $(PORT)'

# Running tg_bot
Simply `uv run python -m tg_bot.main`

# Project Strucutre
Backend and TG Bot is microservices, that can run parallel.
`Core` is widely used in backend and tg_bot microservices.
`workers` is simply worker python scripts that used redis

# AI Agents Guidelines (e.g., Claude, Grok)

This section provides instructions for AI assistants (like Claude or Grok) to effectively work with this project. Follow these to ensure consistent, high-quality contributions.

## General Principles
- Be conversational but professional in responses.
- Refer to the user in the second person and yourself in the first person.
- Format responses in markdown. Use backticks for file, directory, function, and class names.
- NEVER lie or make things up.
- Refrain from apologizing all the time when results are unexpected. Instead, just try your best to proceed or explain the circumstances.
- Bias towards not asking the user for help if you can find the answer yourself using available tools (e.g., grep, read files).
- When providing paths, always start with the project root directory (e.g., `objopa_ecosystem/`).
- Before reading or editing a file, find the full path using tools like `find_path` if unsure.
- Use `grep` for searching symbols, scoped to relevant subtrees.
- For code blocks, use ONLY the format: ```path/to/file.ext#Lstart-end (code) ``` – no other formats allowed.

## Tool Usage
- Adhere strictly to tool schemas.
- Provide all required arguments.
- Do not use tools for items already in context.
- Use only available tools; do not assume others exist.
- Maximize parallel tool calls where possible, but sequence if dependencies exist.
- For long-running commands (e.g., builds, servers), specify `timeout_ms` or let the user cancel manually.
- Avoid HTML entity escaping; use plain characters.

## Code and File Handling
- When editing files, ensure changes are minimal and correct.
- For diagnostics, attempt 1-2 fixes, then defer to the user.
- Never simplify code just to fix issues; prioritize completeness.
- In debugging, address root causes, add logging, and use test statements.
- For external APIs/packages, use the best suited ones compatible with `pyproject.toml`; add to dependencies if needed, and note API keys.

## Project-Specific Notes
- This is an "objopa_ecosystem" project: a Telegram bot and backend microservices for media processing (downloads, generations via AI like GPT, OpenAI, etc.).
- Key components: `tg_bot` (Telegram bot), `backend` (FastAPI server), `core` (shared utilities), `workers` (Redis-based background tasks).
- Use UV for dependencies: `uv sync` to install/update.
- Lint with Ruff: `ruff format --line-length 120 <files>`, `ruff check --fix <files>`, then `python -m py_compile <files>`.
- Run backend: `uv run uvicorn backend.main:app --host $HOST --port $PORT`.
- Run tg_bot: `uv run python -m tg_bot.main`.
- Before commits, ensure `git status` is clean.

## Interaction with User
- Study the project structure and README.md for context.
- If unsure, use tools to explore (e.g., list directories, read files).
- Provide clear, step-by-step guidance.
- For code suggestions, use the exact code block format.
- If adding new files or features, ensure they fit the microservices architecture.
