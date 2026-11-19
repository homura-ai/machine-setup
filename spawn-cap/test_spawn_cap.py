#!/usr/bin/env python3
"""Fixture tests for spawn_cap.py: a throwaway sqlite with the T3 tables the hook reads. Never touches live state.
Run: python3 test_spawn_cap.py"""
import io, json, os, sqlite3, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import spawn_cap as sc


class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(); sc.DB = os.path.join(self.d, 'db.sqlite')
        sc.CFG_DIR = os.path.join(self.d, 'cfg'); sc.CFG = os.path.join(sc.CFG_DIR, 'config.json'); sc.OVR = os.path.join(sc.CFG_DIR, 'overrides.json')
        sc.STATE = os.path.join(self.d, 'state'); sc.LOG = os.path.join(sc.STATE, 'blocks.jsonl')
        os.makedirs(sc.CFG_DIR); self.put(sc.CFG, dict(limit=3, exempt_title_prefixes=['Recovery']))
        self.env = {k: os.environ.pop(k, None) for k in ('T3CODE_THREAD_ID', 'CODEX_THREAD_ID')}
        c = self.c = sqlite3.connect(sc.DB)
        c.executescript('''
          create table orchestration_v2_projection_threads(thread_id text primary key, created_at text, archived_at text, deleted_at text, payload_json text);
          create table orchestration_v2_projection_runs(run_id text, thread_id text, status text);
          create table orchestration_v2_projection_subagents(subagent_id text, thread_id text, provider_thread_id text, child_thread_id text, status text, started_at text, updated_at text, payload_json text);
          create table orchestration_v2_projection_provider_threads(provider_thread_id text, thread_id text, provider_session_id text, payload_json text);
          create table scheduled_tasks(task_id text, thread_id text);''')
        # root -> mid -> kid1..kid4 ; root -> other ; lone (no parent)
        for t, p in [('root', None), ('mid', 'root'), ('other', 'root'), ('lone', None), ('recovery', 'root'), ('rk', 'recovery')] + [(f'kid{i}', 'mid') for i in range(1, 5)]:
            self.thread(t, p)
        c.execute("update orchestration_v2_projection_threads set payload_json=json_set(payload_json,'$.title','Recovery job') where thread_id='recovery'")
        for t in ('root', 'mid', 'kid1', 'kid2'): self.run_(t, 'running')
        self.run_('kid3', 'completed'); self.run_('kid4', 'queued'); self.run_('other', 'completed')
        c.commit()

    def tearDown(self):
        self.c.close()
        for k, v in self.env.items():
            if v is not None: os.environ[k] = v

    def put(self, path, obj):
        with open(path, 'w') as f: json.dump(obj, f)

    def log(self):
        with open(sc.LOG) as f: return [json.loads(l) for l in f]

    def thread(self, t, p, deleted=None):
        self.c.execute('insert into orchestration_v2_projection_threads values(?,?,?,?,?)',
                       (t, '2026-01-01T00:00:00', None, deleted, json.dumps(dict(title='T ' + t, lineage=dict(parentThreadId=p)))))
        self.c.execute('insert into orchestration_v2_projection_provider_threads values(?,?,?,?)', ('pt:native:n-' + t, t, 'sess-' + t, '{}'))

    def run_(self, t, st): self.c.execute('insert into orchestration_v2_projection_runs values(?,?,?)', ('r' + t + st, t, st)); self.c.commit()

    def sub(self, parent, child, st, native=None, title=None):
        self.c.execute('insert into orchestration_v2_projection_subagents values(?,?,?,?,?,?,?,?)',
                       ('s' + str(child) + str(native), parent, None, child, st, '2026-01-01', '2026-01-01',
                        json.dumps(dict(title=title, nativeTaskRef=dict(nativeId=native))))); self.c.commit()

    def hook(self, caller, tool, tin=None, event='PreToolUse'):
        os.environ['T3CODE_THREAD_ID'] = caller
        sys.stdin = io.StringIO(json.dumps(dict(tool_name=tool, tool_input=tin or {}, hook_event_name=event)))
        err = sys.stderr; sys.stderr = buf = io.StringIO()
        try: rc = sc.hook()
        finally: sys.stderr = err; sys.stdin = sys.__stdin__
        self.msg = buf.getvalue(); return rc

    def counts(self):
        c = sc.db(); par = sc.parents(c); return sc.subtree_counts(par, *sc.running_agents(c))

    def test_counts_running_not_history(self):
        s = self.counts()
        self.assertEqual(s.get('root'), 3)   # mid, kid1, kid2 (root itself not counted; queued/completed not running)
        self.assertEqual(s.get('mid'), 2)
        self.assertNotIn('kid1', s)

    def test_spawn_allowed_under_cap_blocked_at_cap(self):
        self.assertEqual(self.hook('kid1', 'Agent'), 2)            # root subtree has 3 running, limit 3
        self.assertIn('running right now', self.msg); self.assertIn("'T root'", self.msg)
        self.assertEqual(self.hook('lone', 'Agent'), 0)
        self.c.execute("update orchestration_v2_projection_runs set status='completed' where thread_id='kid2'"); self.c.commit()
        self.assertEqual(self.hook('kid1', 'mcp__t3-code__delegate_task'), 0)   # count dropped as soon as an agent finished
        self.assertEqual(self.hook('kid1', 'mcp__t3_code__create_threads', dict(threads=[{}, {}])), 2)  # 2 + 2 > 3
        rec = self.log()
        self.assertEqual((rec[0]['count'], rec[0]['limit'], rec[0]['subtree_root']), (3, 3, 'root'))

    def test_many_old_spawns_do_not_count(self):
        for i in range(30): self.thread(f'old{i}', 'lone')
        self.c.commit()
        self.assertEqual(self.hook('lone', 'Agent'), 0)

    def test_wake_idle_blocked_running_allowed(self):
        for tool in ('mcp__t3-code__t3_thread_send', 't3-code.t3_thread_send', 'mcp__t3_code__t3_thread_send_attachments'):
            self.assertEqual(self.hook('mid', tool, dict(threadId='kid3', message='x')), 2, tool)    # idle child, subtree at cap
            self.assertEqual(self.hook('mid', tool, dict(threadId='kid1', message='x')), 0, tool)    # already running: steer
        self.assertEqual(self.hook('mid', 'mcp__t3-code__t3_thread_send', dict(threadId='kid3')), 2); self.assertIn("'T kid3' (kid3) running again", self.msg)
        rec = self.log()[-1]; self.assertEqual((rec['action'], rec['target']), ('start-existing', 'kid3'))
        self.assertEqual(self.hook('mid', 'mcp__t3-code__t3_thread_send', dict(threadId='kid4')), 2)     # only a queued run: idle
        self.assertEqual(self.hook('kid1', 'mcp__t3-code__t3_thread_send', dict(threadId='other')), 2)   # idle sibling of mid under root
        self.assertEqual(self.hook('kid1', 'mcp__t3-code__t3_thread_send', dict(threadId='lone')), 0)    # top-level thread: no subtree gains
        self.assertEqual(self.hook('kid1', 'mcp__t3-code__t3_thread_send', dict(threadId='root')), 0)    # running
        self.assertEqual(self.hook('kid1', 'mcp__t3-code__t3_thread_send', dict(threadId='nope')), 0)    # unknown id
        self.assertEqual(self.hook('kid1', 'mcp__t3-code__t3_thread_send', dict(message='x')), 0)        # own thread
        self.assertEqual(self.hook('kid1', 'mcp__t3-code__t3_thread_send', dict(threadId='kid3', environmentId='e')), 0)
        self.assertEqual(self.hook('mid', 'mcp__t3-code__t3_thread_send', dict(threadId='n-kid3')), 2)   # provider's own id

    def test_wake_under_cap_allowed(self):
        self.put(sc.CFG, dict(limit=4))
        self.assertEqual(self.hook('mid', 'mcp__t3-code__t3_thread_send', dict(threadId='kid3')), 0)

    def test_wake_checks_targets_ancestors_only(self):
        # lone has 3 running children -> at cap; a message from there to an idle thread elsewhere is fine, and vice versa blocked
        for i in range(3): self.thread(f'l{i}', 'lone'); self.run_(f'l{i}', 'running')
        self.thread('lidle', 'lone'); self.c.commit()
        self.put(sc.CFG, dict(limit=5))
        self.assertEqual(self.hook('l0', 'mcp__t3-code__t3_thread_send', dict(threadId='kid3')), 0)
        self.put(sc.CFG, dict(limit=3))
        self.assertEqual(self.hook('kid1', 'mcp__t3-code__t3_thread_send', dict(threadId='lidle')), 2)

    def test_native_wake(self):
        self.thread('n1', 'mid'); self.thread('n2', 'mid'); self.c.commit()
        self.sub('mid', 'n1', 'completed', native='a111', title='worker'); self.sub('mid', 'n2', 'running', native='call_9:uuid-2', title='/root/rev')
        self.assertEqual(self.counts()['mid'], 3)                                     # running subagent row counts
        self.assertEqual(self.hook('mid', 'SendMessage', dict(to='a111', message='x')), 2)   # resumes a finished agent, over cap
        self.assertEqual(self.hook('mid', 'SendMessage', dict(to='worker [3fa9c1]', message='x')), 2)
        self.assertEqual(self.hook('mid', 'SendMessage', dict(to='main', message='x')), 0)
        self.assertEqual(self.hook('mid', 'SendMessage', dict(to='uds:/run/x.sock', message='x')), 0)
        self.assertEqual(self.hook('mid', 'send_input', dict(id='uuid-2', message='x')), 0)  # running already
        self.assertEqual(self.hook('mid', 'followup_task', dict(target='/root/rev')), 0)
        self.c.execute("update orchestration_v2_projection_subagents set status='completed' where child_thread_id='n2'"); self.c.commit()
        self.assertEqual(self.hook('mid', 'resume_agent', dict(id='uuid-2')), 2)
        self.assertEqual(self.hook('mid', 'followup_task', dict(target='/root/rev')), 2)
        self.assertEqual(self.hook('lone', 'SendMessage', dict(to='worker', message='x')), 0)  # someone else's agent name: not matched

    def test_loose_native_subagents_count_on_parent(self):
        self.put(sc.CFG, dict(limit=2))
        self.sub('lone', None, 'running'); self.sub('lone', None, 'running'); self.sub('lone', None, 'completed')
        self.assertEqual(self.counts()['lone'], 2)
        self.assertEqual(self.hook('lone', 'Task'), 2)

    def test_scheduled_task(self):
        self.c.execute("insert into scheduled_tasks values('t1','kid3'),('t2','kid1'),('t3',null)"); self.c.commit()
        self.assertEqual(self.hook('mid', 'mcp__t3-code__run_scheduled_task_now', dict(taskId='t1')), 2)
        self.assertEqual(self.hook('mid', 'mcp__t3-code__run_scheduled_task_now', dict(taskId='t2')), 0)
        self.assertEqual(self.hook('mid', 'mcp__t3-code__run_scheduled_task_now', dict(taskId='t3')), 2)   # new thread: caller's chain
        self.assertEqual(self.hook('lone', 'mcp__t3-code__run_scheduled_task_now', dict(taskId='t3')), 0)
        self.assertEqual(self.hook('mid', 'mcp__t3-code__run_scheduled_task_now', dict(taskId='zz')), 0)

    def test_exemptions(self):
        self.run_('rk', 'running')                                            # root now has 4 running
        self.assertEqual(self.hook('rk', 'Agent'), 0)                         # Recovery-titled ancestor stops the walk
        self.assertEqual(self.hook('mid', 'mcp__t3-code__t3_thread_send', dict(threadId='recovery')), 0)   # exempt target
        self.put(sc.CFG, dict(limit=3, exempt=['mid']))
        self.assertEqual(self.hook('mid', 'Agent'), 0); self.assertEqual(self.hook('kid1', 'Agent'), 0)
        self.assertEqual(self.hook('kid1', 'mcp__t3-code__t3_thread_send', dict(threadId='kid3')), 0)
        self.assertEqual(self.hook('other', 'Agent'), 2)

    def test_override(self):
        import time
        self.put(sc.OVR, {'kid1': dict(extra=1, until=time.time() + 60)})
        self.assertEqual(self.hook('kid1', 'Agent'), 0); self.assertEqual(self.hook('kid2', 'Agent'), 2)
        self.put(sc.OVR, {'root': dict(extra=1, until=time.time() + 60)})
        self.assertEqual(self.hook('mid', 'mcp__t3-code__t3_thread_send', dict(threadId='kid3')), 0)
        self.put(sc.OVR, {'all': dict(extra=1, until=time.time() - 1)})
        self.assertEqual(self.hook('kid2', 'Agent'), 2)

    def test_fail_open_and_ignored(self):
        self.assertEqual(self.hook('kid1', 'Bash'), 0); self.assertEqual(self.hook('kid1', 'mcp__t3-code__t3_thread_read', dict(threadId='kid3')), 0)
        self.assertEqual(self.hook('kid1', 'mcp__t3-code__t3_thread_send', dict(threadId=['x'])), 0)
        self.assertEqual(self.hook('kid1', 'SendMessage', 'garbage'), 0)
        self.assertEqual(self.hook('kid1', 'mcp__t3-code__t3_thread_send', dict(threadId='kid3'), event='PostToolUse'), 0)
        self.c.execute('drop table orchestration_v2_projection_runs'); self.c.commit()
        self.assertEqual(self.hook('kid1', 'Agent'), 0); self.assertEqual(self.hook('mid', 'mcp__t3-code__t3_thread_send', dict(threadId='kid3')), 0)
        self.assertTrue(os.path.exists(os.path.join(sc.STATE, 'errors.log')))
        sc.DB = os.path.join(self.d, 'missing.sqlite'); self.assertEqual(self.hook('kid1', 'Agent'), 0)
        sys.stdin = io.StringIO('not json'); self.assertEqual(sc.hook(), 0); sys.stdin = sys.__stdin__

    def test_post_hook_edges_and_deleted(self):
        self.assertEqual(self.hook('lone', 'mcp__t3-code__t3_thread_launch', event='PostToolUse'), 0)
        os.environ['T3CODE_THREAD_ID'] = 'lone'
        sys.stdin = io.StringIO(json.dumps(dict(tool_name='mcp__t3-code__t3_thread_launch', hook_event_name='PostToolUse', tool_response='{"threadId":"newt"}')))
        self.assertEqual(sc.hook(), 0); sys.stdin = sys.__stdin__
        self.assertEqual(sc.load(os.path.join(sc.STATE, 'edges.json'), {})['newt'][0], 'lone')
        self.thread('newt', None); self.thread('gone', 'lone', deleted='2026-01-02'); self.run_('newt', 'running'); self.run_('gone', 'running')
        self.assertEqual(self.counts()['lone'], 1)   # launch edge counted, deleted thread not

    def test_tree_and_status(self):
        d = sc.tree(1); n = {x['id']: x for x in d['nodes']}
        self.assertEqual((n['root']['subtree_running'], n['root']['status'], n['mid']['subtree_running']), (3, 'running', 2))
        self.assertEqual(d['limit'], 3); self.assertNotIn('kid3', n)   # idle and old: not kept
        out = sys.stdout; sys.stdout = buf = io.StringIO()
        try: sc.main(['status'])
        finally: sys.stdout = out
        self.assertIn('3/3 running in subtree  T root', buf.getvalue())

    def test_stale_subagent_row_and_deleted_child(self):
        self.sub('mid', 'kid3', 'running')                  # kid3's run is completed: row is stale
        self.thread('gone', 'lone', deleted='2026-01-02'); self.sub('lone', 'gone', 'running')
        s = self.counts(); self.assertEqual((s['root'], s['mid'], s.get('lone')), (3, 2, None))
        self.assertEqual(self.hook('mid', 'mcp__t3-code__t3_thread_send', dict(threadId='kid3')), 2)

    def test_workflow_container_not_counted(self):
        self.thread('wf', 'lone'); self.thread('wa', 'wf'); self.c.commit()
        self.sub('lone', 'wf', 'running', native='wp1', title='Workflow'); self.sub('wf', 'wa', 'running', native='w-a')
        self.assertEqual(self.counts()['lone'], 1)

    def test_hand_back_to_idle_ancestor_allowed(self):
        self.run_('other', 'running')
        self.c.execute("update orchestration_v2_projection_runs set status='completed' where thread_id='mid'"); self.c.commit()
        self.assertEqual(self.hook('kid1', 'mcp__t3-code__t3_thread_send', dict(threadId='mid', message='done')), 0)
        self.assertEqual(self.hook('kid1', 'mcp__t3-code__t3_thread_send', dict(threadId='kid3', message='x')), 2)

    def test_all_override_counted_once(self):
        import time
        self.put(sc.OVR, {'all': dict(extra=1, until=time.time() + 60)})
        self.assertEqual(self.hook('kid1', 'Agent'), 0)       # 3+1 <= 3+1
        self.run_('other', 'running')
        self.assertEqual(self.hook('kid1', 'Agent'), 2)       # 4+1 > 3+1 (was allowed when 'all' counted twice)

    def test_exempt_caller_wakes(self):
        self.run_('rk', 'running')
        self.assertEqual(self.hook('recovery', 'mcp__t3-code__t3_thread_send', dict(threadId='other')), 0)
        self.assertEqual(self.hook('rk', 'mcp__t3-code__t3_thread_send', dict(threadId='kid3')), 0)
        self.assertEqual(self.hook('kid1', 'mcp__t3-code__t3_thread_send', dict(threadId='kid3')), 2)

    def test_cycle(self):
        self.assertEqual(sc.subtree_counts({'x': 'y', 'y': 'x'}, {'x'}, {}), {'y': 1})
        self.thread('cx', None); self.thread('cy', None); self.c.commit()
        self.sub('cy', 'cx', 'completed'); self.sub('cx', 'cy', 'completed')
        par = sc.parents(sc.db()); self.assertEqual((par.get('cx'), par.get('cy')), ('cy', None))

    def test_sendmessage_subscribe_only_and_names(self):
        self.thread('n1', 'mid'); self.c.commit(); self.sub('mid', 'n1', 'completed', native='a111', title='/root/rev')
        self.assertEqual(self.hook('mid', 'SendMessage', dict(to='a111', notify_when_idle=True)), 0)
        self.assertEqual(self.hook('mid', 'followup_task', dict(target='rev', message='x')), 2)     # relative Codex name
        self.assertEqual(self.hook('mid', 'followup_task', dict(target='ev', message='x')), 0)      # not a suffix match of a name
        self.assertEqual(self.hook('kid1', 'SendMessage', dict(to='T other', message='x')), 2)      # another session by title
        self.thread('dup1', None); self.thread('dup2', None)
        self.c.execute("update orchestration_v2_projection_threads set payload_json=json_set(payload_json,'$.title','Same') where thread_id in ('dup1','dup2','other')"); self.c.commit()
        self.assertEqual(self.hook('kid1', 'SendMessage', dict(to='Same', message='x')), 0)         # ambiguous name: allowed

    def test_block_survives_log_failure(self):
        os.makedirs(sc.STATE, exist_ok=True); os.makedirs(sc.LOG)   # LOG is a directory: open() fails
        self.assertEqual(self.hook('kid1', 'Agent'), 2)


if __name__ == '__main__':
    unittest.main()
