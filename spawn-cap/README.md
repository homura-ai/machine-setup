# spawn-cap (2026-11-11; concurrent cap 2026-11-13)
An automated check that stops agent threads from spawning too many subagents or agents, plus a tree graph of who started whom. Register `spawn-cap hook` under both `PreToolUse` and `PostToolUse` for Claude Code and Codex.

- Tree: parent->child spawn tree from T3 statev2.sqlite (lineage.parentThreadId, subagents) + a launches ledger (~/.local/state/spawn-cap/edges.json; t3_thread_launch has no lineage parent). `spawn-cap tree` prints it as JSON, with each node's status (running/idle) and subtree_running.
- Running: a thread with runs counts while it has an active run; a native Claude/Codex subagent (no runs) counts while its subagent row is active; a Claude workflow container is not counted; deleted threads never count.
- Ledger: the `PostToolUse` hook should match the T3 MCP tools `t3_thread_launch` and `create_threads`. It records the caller and the new thread IDs from the tool response so these launches count toward the caller's subtree.
- Check: PreToolUse hook in ~/.claude/settings.json and ~/.codex/hooks.json runs `spawn-cap hook`; exits 2 (blocks) when the call would put more than the limit of agents running at once under any thread:
  - spawns (Agent, Task, spawn_agent, delegate_task, t3_thread_launch, create_threads, run_scheduled_task_now on an unbound task): checked against the caller and each ancestor;
  - starting an existing idle agent or thread (t3_thread_send, t3_thread_send_attachments, run_scheduled_task_now, Claude SendMessage, Codex send_input, resume_agent, followup_task): checked against the target's ancestors.
  - Never blocked: a message to an agent that is already running, a hand-back to an idle ancestor of the caller, SendMessage with only notify_when_idle, targets on another machine or that cannot be resolved. Fails open.
- Default: 10 running at once per subtree (the subtree's root not counted). Change in ~/.config/spawn-cap/config.json {"limit":N,"exempt":[ids]}.
- Exempt: thread ids in `"exempt"` and threads whose title starts with one of `"exempt_title_prefixes"` (for example a recovery agent), and everything called from inside their subtrees. None by default.
- Override (human-only): `spawn-cap allow <thread-id|all> [extra] [minutes]`.
- Blocks log: ~/.local/state/spawn-cap/blocks.jsonl (wake blocks carry action "start-existing" and target). `spawn-cap status` shows running counts per subtree.
- Tests: `python3 test_spawn_cap.py` (fixture database; never touches live state).
- Uninstall: remove both `spawn-cap hook` registrations from ~/.claude/settings.json and ~/.codex/hooks.json.
