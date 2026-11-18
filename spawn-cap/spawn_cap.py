#!/usr/bin/env python3
"""Spawn cap + agent tree: an automated check that stops threads from spawning too many
subagents or agents, and a tree graph of who started whom.

hook   PreToolUse hook for Claude Code and Codex. Counts the agents running right now in each subtree of
       the spawn tree (the limit is on agents running at once, per subtree). Blocks a call that would put more than
       LIMIT running agents under any thread: a spawn (checked against the caller and each of its ancestors)
       or a call that starts an existing idle agent or thread again (checked against that agent's ancestors).
       A message to an agent that is already running, a hand-back to an idle ancestor of the caller, and any call
       from a thread in an exempt subtree are never blocked.
tree   Print the parent->child spawn tree (the data structure the cap counts against) as JSON from T3's statev2.sqlite.
allow  Human-only override: `spawn-cap allow <thread-id|all> [extra] [minutes]`.
status Show running-agent counts per subtree and recent blocks.

Defaults: LIMIT=10 agents running at once under any one thread (the thread itself is not counted). Change in
~/.config/spawn-cap/config.json ({"limit": N, "exempt": [thread ids], "exempt_title_prefixes": [...]}).
Threads in an exempt subtree (by id, or by a title prefix such as a recovery agent's name) are
never blocked. Fails OPEN on any internal error
(a broken checker must never stop all agents).
"""
import json, os, sqlite3, sys, time, datetime

DB = os.path.expanduser('~/.t3/userdata/statev2.sqlite')
CFG_DIR = os.path.expanduser('~/.config/spawn-cap')
CFG = os.path.join(CFG_DIR, 'config.json')
OVR = os.path.join(CFG_DIR, 'overrides.json')
STATE = os.path.expanduser('~/.local/state/spawn-cap')
LOG = os.path.join(STATE, 'blocks.jsonl')
DEFAULT = dict(limit=10, exempt=[], exempt_title_prefixes=[])
ACTIVE = ('running', 'starting', 'preparing', 'waiting')  # a queued run is not running yet
SPAWN_TOOLS = ('delegate_task', 't3_thread_launch', 'create_threads')
WAKE_TOOLS = ('t3_thread_send', 't3_thread_send_attachments', 'run_scheduled_task_now')  # T3: start a turn in an existing thread
NATIVE_WAKE = ('SendMessage',  # Claude Code: a send to a finished agent resumes it
               'send_input', 'resume_agent', 'followup_task')  # Codex: trigger a turn in / reopen an existing agent


def base_tool(tool):
    """Accepts every MCP spelling (mcp__t3-code__x, mcp__t3_code__x, t3-code.x, ...)."""
    if tool.startswith('mcp__') and '__' in tool[5:]: return tool.rsplit('__', 1)[1]
    if '.' in tool: return tool.rsplit('.', 1)[1]
    return None


def spawn_kind(tool):
    if tool in ('Agent', 'Task', 'spawn_agent'): return 'agent'
    b = base_tool(tool)
    return b if b in SPAWN_TOOLS else None


def wake_kind(tool):
    """Tools that start an existing agent or thread running again."""
    if tool in NATIVE_WAKE: return tool
    b = base_tool(tool)
    return b if b in WAKE_TOOLS or b in NATIVE_WAKE else None

def load(path, default):
    try:
        with open(path) as f: return json.load(f)
    except Exception: return default


def cfg():
    c = dict(DEFAULT); c.update(load(CFG, {}))
    c['exempt'] = set(DEFAULT['exempt']) | set(load(CFG, {}).get('exempt', []))
    # Only a list of non-empty strings: a bare string would become one-letter prefixes exempting nearly every title.
    prefixes = c.get('exempt_title_prefixes')
    c['exempt_title_prefixes'] = tuple(x for x in prefixes if isinstance(x, str) and x) if isinstance(prefixes, list) else ()
    return c


def db():
    return sqlite3.connect('file:' + DB + '?mode=ro', uri=True, timeout=2)


def iso(t): return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')


def threads(c):
    out = {}
    for tid, created, archived, deleted, p in c.execute(
            'select thread_id,created_at,archived_at,deleted_at,payload_json from orchestration_v2_projection_threads'):
        try: d = json.loads(p)
        except Exception: d = {}
        lin = d.get('lineage') or {}
        out[tid] = dict(id=tid, title=d.get('title') or '', created=created, archived=bool(archived), deleted=bool(deleted),
                        parent=lin.get('parentThreadId'), settled=bool(d.get('settledAt')), source=d.get('creationSource'))
    # launches (t3_thread_launch) keep lineage parent null; recover launcher from the thread's first message context if present
    return out


def resolve_caller(c, payload):
    t = os.environ.get('T3CODE_THREAD_ID')
    if t: return t
    return by_native_id(c, payload.get('session_id') or payload.get('thread_id'))


def by_native_id(c, sid):
    """T3 thread id for a provider's own thread or session id."""
    if sid:
        r = c.execute("""select thread_id from orchestration_v2_projection_provider_threads
                         where thread_id is not null and (provider_thread_id=? or provider_session_id=?
                           or provider_thread_id like '%:' || ? or json_extract(payload_json,'$.nativeThreadRef.nativeId')=?)""",
                      (sid, sid, sid, sid)).fetchone()
        if r and r[0]: return r[0]
    return None


def locked_update(name, fn):
    import fcntl, tempfile
    os.makedirs(STATE, exist_ok=True); p = os.path.join(STATE, name)
    with open(p + '.lock', 'w') as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        d = fn(load(p, {}))
        fd, tmp = tempfile.mkstemp(dir=STATE)
        with os.fdopen(fd, 'w') as f: json.dump(d, f)
        os.replace(tmp, p)


def parents(c):
    """The spawn tree: child -> parent. Lineage first, then subagent links, then launch edges recorded by PostToolUse."""
    par = {t: p for t, p in c.execute("select thread_id,json_extract(payload_json,'$.lineage.parentThreadId') from orchestration_v2_projection_threads")}
    def link(child, parent):  # first link wins; never one that makes the child its own ancestor
        if parent and not par.get(child) and child not in ancestors(par, parent): par[child] = parent
    for child, parent in c.execute('''select child_thread_id,thread_id from orchestration_v2_projection_subagents
                                      where child_thread_id is not null order by coalesce(started_at,updated_at)'''):
        link(child, parent)
    for child, v in load(os.path.join(STATE, 'edges.json'), {}).items(): link(child, v[0])
    return par


def running_agents(c):
    """Agents running right now. A thread that has runs (T3 threads and delegated tasks) is running when it has an
    active run; its subagent row's status is not used, because it can stay 'running' after the run ends. A native
    Claude/Codex subagent has no runs, so its subagent row's status decides. A native row that has active subagent
    rows of its own is a container (a Claude workflow), not an agent. Native subagents with no T3 thread count on
    their parent ('loose'). Deleted threads never count."""
    q = ','.join('?' * len(ACTIVE)); run = set(); loose = {}
    for (t,) in c.execute(f"""select distinct r.thread_id from orchestration_v2_projection_runs r
                              join orchestration_v2_projection_threads t on t.thread_id=r.thread_id
                              where r.status in ({q}) and t.deleted_at is null""", ACTIVE):
        run.add(t)
    has_runs = {t for (t,) in c.execute('select distinct thread_id from orchestration_v2_projection_runs')}
    rows = c.execute(f"""select s.thread_id, s.child_thread_id, t.deleted_at from orchestration_v2_projection_subagents s
                         left join orchestration_v2_projection_threads t on t.thread_id=s.child_thread_id
                         where s.status in ({q})""", ACTIVE).fetchall()
    containers = {p for p, _, _ in rows}
    for parent, child, deleted in rows:
        if not child:
            if parent: loose[parent] = loose.get(parent, 0) + 1
        elif not deleted and child not in has_runs and child not in containers:
            run.add(child)
    return run, loose


def subtree_counts(par, run, loose):
    """The limit is per subtree: running agents below each node (the node itself is not counted)."""
    sub = {}
    def add(n, k, seen):
        while n and n not in seen: seen.add(n); sub[n] = sub.get(n, 0) + k; n = par.get(n)
    for t in run: add(par.get(t), 1, {t})
    for t, k in loose.items(): add(t, k, set())
    return sub


def ancestors(par, t):
    out = []; seen = set()
    while t and t not in seen: seen.add(t); out.append(t); t = par.get(t)
    return out


def override_extra(caller, with_all=True):
    o = load(OVR, {}); now = time.time(); extra = 0
    for k in (caller, 'all') if with_all else (caller,):
        e = o.get(k)
        if e and e.get('until', 0) > now: extra += e.get('extra', 10 ** 9)
    return extra


def proposed(kind, tin):
    if kind == 'create_threads': return max(1, len((tin or {}).get('threads') or []))
    return 1


def wake_target(c, par, caller, kind, tin):
    """(thread the call would start, is it running) or None when the call starts nothing we can identify
    (another machine, another session, the caller itself, an unknown id)."""
    tin = tin if isinstance(tin, dict) else {}
    if tin.get('environmentId'): return None  # another machine: not in this tree
    q = ','.join('?' * len(ACTIVE))
    if kind in ('t3_thread_send', 't3_thread_send_attachments'):
        t = tin.get('threadId')
        if not t or not isinstance(t, str): return None  # omitted: the caller's own thread
        if not c.execute('select 1 from orchestration_v2_projection_threads where thread_id=?', (t,)).fetchone():
            t = by_native_id(c, t)
        return (t, None) if t else None
    if kind == 'run_scheduled_task_now':
        r = c.execute('select thread_id from scheduled_tasks where task_id=?', (str(tin.get('taskId')),)).fetchone()
        if not r: return None
        return (r[0], None) if r[0] else ('', False)  # unbound task: the run makes a new thread, like a spawn
    if kind == 'SendMessage' and not tin.get('message'): return None  # notify_when_idle only: starts nothing
    # native agents: Claude SendMessage {to: agentId | name}, Codex send_input/resume_agent {id}, followup_task {target}
    to = tin.get('to') or tin.get('id') or tin.get('target') or tin.get('agent_id') or tin.get('receiver_thread_id')
    if not to or not isinstance(to, str): return None
    to = to.split(' [')[0].strip()
    # Codex task names are '/root/<name>' and may be given relative ('<name>'); Claude agents by id or name
    rows = c.execute("""select child_thread_id, thread_id from orchestration_v2_projection_subagents
                         where child_thread_id is not null and (json_extract(payload_json,'$.nativeTaskRef.nativeId')=?1
                           or json_extract(payload_json,'$.nativeTaskRef.nativeId') like '%:' || ?1
                           or provider_thread_id like '%:' || ?1 or json_extract(payload_json,'$.title')=?1
                           or substr(json_extract(payload_json,'$.title'), -length(?1)-1)='/' || ?1)
                         order by coalesce(started_at,updated_at) desc""", (to,)).fetchall()
    mine = [r for r in rows if caller in ancestors(par, r[0])] or [r for r in rows if r[1] == caller]
    if mine: return (mine[0][0], None)
    t = by_native_id(c, to)
    if t: return (t, None)
    # Claude SendMessage to another local session by its name, which is the T3 thread title (only an unambiguous one)
    named = c.execute("select thread_id from orchestration_v2_projection_threads where deleted_at is null and json_extract(payload_json,'$.title')=?", (to,)).fetchall()
    return (named[0][0], None) if len(named) == 1 else None


def post_hook(payload, c, caller):
    """Record launcher -> launched-thread edges (launches carry no lineage parent)."""
    import re
    resp = payload.get('tool_response') or payload.get('tool_output') or ''
    if isinstance(resp, dict) and (resp.get('isError') or resp.get('is_error')): return 0
    raw = json.dumps(resp)
    if re.search(r'is_?[Ee]rror\\*"\s*:\s*true', raw): return 0
    ids = set(re.findall(r'threadId\\*"\s*:\s*\\*"([^"\\]+)', raw))
    ids.discard(caller)
    if ids:
        now = time.time()
        def upd(d):
            for i in ids: d.setdefault(i, [caller, now])
            return d
        locked_update('edges.json', upd)
    return 0


def hook():
    try:
        payload = json.load(sys.stdin)
        tool = payload.get('tool_name') or ''; kind = spawn_kind(tool); wake = None if kind else wake_kind(tool)
        if not kind and not wake: return 0
        post = (payload.get('hook_event_name') or '') == 'PostToolUse'
        if post and kind not in ('t3_thread_launch', 'create_threads'): return 0
        c = db(); conf = cfg(); caller = resolve_caller(c, payload)
        if not caller: return 0
        if post: return post_hook(payload, c, caller)
        if caller in conf['exempt']: return 0
        prefixes = conf['exempt_title_prefixes']
        exempt = lambda t: t in conf['exempt'] or bool(prefixes and threads_title(c, t).startswith(prefixes))
        title = threads_title(c, caller); par = parents(c); run, loose = running_agents(c)
        k = proposed(kind, payload.get('tool_input')); chain = ancestors(par, caller); target = None
        if wake:
            w = wake_target(c, par, caller, wake, payload.get('tool_input'))
            if not w: return 0
            target, on = w; k = 1
            if any(exempt(a) for a in chain): return 0  # caller in a sanctioned split / recovery subtree
            if target:  # an existing thread: it adds a running agent under its own ancestors, unless it is running already
                if on or target == caller or target in run or exempt(target): return 0
                if target in chain: return 0  # handing back to an idle ancestor: the caller is finishing, so no net agent
                chain = ancestors(par, target)[1:]
        sub = subtree_counts(par, run, loose); worst = None
        for a in chain:
            if exempt(a): break  # sanctioned split / recovery subtree
            lim = conf['limit'] + override_extra(a) + (override_extra(caller, with_all=False) if a != caller else 0)
            if sub.get(a, 0) + k > lim: worst = (a, sub.get(a, 0), lim); break
        if worst:
            a, n, limit = worst; atitle = threads_title(c, a)
            what = (f"this call would start the existing agent '{threads_title(c, target)}' ({target}) running again in it" if target
                    else f"'{title}' is in it and this call starts {k} more")
            msg = (f"SPAWN CAP: {n} agents are running right now in the subtree under '{atitle}' ({a}) "
                   f"(limit {limit} running at once per subtree); {what}, so it is blocked until some of those agents finish "
                   f"or a human runs `spawn-cap allow`. Finish with the agents that are running, or ask your owner.")
            rec = dict(at=iso(time.time()), caller=caller, title=title, subtree_root=a, tool=tool, count=n, adding=k, limit=limit)
            if wake: rec.update(action='start-existing', target=target)
            try:  # a log failure must not turn the block into an allow
                os.makedirs(STATE, exist_ok=True)
                with open(LOG, 'a') as f: f.write(json.dumps(rec) + '\n')
            except Exception: pass
            print(msg, file=sys.stderr)
            return 2  # Claude Code and Codex: exit 2 blocks the tool call, stderr goes to the model
        return 0
    except Exception as e:  # fail open
        try:
            os.makedirs(STATE, exist_ok=True)
            with open(os.path.join(STATE, 'errors.log'), 'a') as f: f.write(f'{iso(time.time())} {e!r}\n')
        except Exception: pass
        return 0


def threads_title(c, tid):
    r = c.execute("select json_extract(payload_json,'$.title') from orchestration_v2_projection_threads where thread_id=?", (tid,)).fetchone()
    return (r and r[0]) or ''


def tree(hours=24):
    c = db(); ts = threads(c); since = iso(time.time() - hours * 3600)
    blocks = []
    try:
        with open(LOG) as f: blocks = [json.loads(l) for l in f if l.strip()][-200:]
    except Exception: pass
    par = parents(c); run, loose = running_agents(c)
    keep = {t for t, v in ts.items() if not v['deleted'] and (v['created'] >= since or t in run)}
    for t in list(keep):  # include ancestors
        p = par.get(t)
        while p and p in ts and p not in keep: keep.add(p); p = par.get(p)
    conf = cfg(); sub = subtree_counts(par, run, loose)
    nodes = [dict(id=t, title=ts[t]['title'], parent=par.get(t) if par.get(t) in keep else None,
                  status='running' if t in run else 'idle', archived=ts[t]['archived'], created=ts[t]['created'],
                  subtree_running=sub.get(t, 0)) for t in keep]
    return dict(generated=iso(time.time()), limit=conf['limit'], nodes=nodes, blocks=blocks[-20:])


def main(a):
    cmd = a[0] if a else 'status'
    if cmd == 'hook': sys.exit(hook())
    if cmd == 'tree': print(json.dumps(tree(float(a[1]) if len(a) > 1 else 24))); return
    if cmd == 'allow':
        if os.environ.get('T3CODE_THREAD_ID') or os.environ.get('CODEX_THREAD_ID'):
            sys.exit('spawn-cap allow is human-only (run it in your own terminal).')
        if len(a) < 2: sys.exit('usage: spawn-cap allow <thread-id|all> [extra] [minutes]')
        tid = a[1]; extra = int(a[2]) if len(a) > 2 else 10 ** 9; mins = float(a[3]) if len(a) > 3 else 60
        o = load(OVR, {}); o[tid] = dict(extra=extra, until=time.time() + mins * 60)
        os.makedirs(CFG_DIR, exist_ok=True)
        with open(OVR, 'w') as f: json.dump(o, f, indent=1)
        print(f'allowed {tid}: +{extra} for {mins} min'); return
    d = tree(1)
    for n in sorted(d['nodes'], key=lambda n: -n['subtree_running'])[:15]:
        if n['subtree_running']: print(f"{n['subtree_running']:3d}/{d['limit']} running in subtree  {n['title'][:70]}")
    print('recent blocks:', len(d['blocks']))


if __name__ == '__main__':
    main(sys.argv[1:])
