---
description: Initialize the current directory as an ingest project (copies CLAUDE.md template and creates context/ folder)
allowed-tools: Bash, Write
---
 
## Task
 
Initialize the current working directory as an ingest project:
 
1. Create `context/` directory if it does not exist
2. Copy `~/.claude/CLAUDE-ingest-template.md` to `./CLAUDE.md` — if `./CLAUDE.md` already exists, ask what to do, overwrite, merge, abort
3. Report each step to the user
 
