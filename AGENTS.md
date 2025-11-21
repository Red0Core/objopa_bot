# Contribution Guidelines

For any changes to this repository run the following commands on the Python files you modified:

```
ruff format <files>
ruff check <files>
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
