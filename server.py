#!/usr/bin/env python3
"""CGS local functional prototype. Python 3.10+, standard library only."""
import argparse
import copy
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time
import uuid
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlsplit
from backend.rules import RULES, WEIGHTS, LABELS, ANCHORS, CONDITIONS, REASONS, RULE_VERSION, calculate, validate_score, delta
from backend.agents import Agents, ProviderError

ROOT = Path(__file__).resolve().parent

def load_env():
    path = ROOT / '.env'
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip() and not line.lstrip().startswith('#') and '=' in line:
                k, value = line.split('=', 1)
                if k.strip() in ('OPENAI_API_KEY', 'OPENAI_MODEL', 'CGS_DB_PATH'):
                    os.environ.setdefault(k.strip(), value.strip().strip('\"\''))

def uid(prefix):
    return prefix + '-' + uuid.uuid4().hex[:16]

def iso_now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')

def string(value, label, maximum=3000, required=True):
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise ValueError('Provide a valid ' + label + '.')
    return value.strip()

def day(value):
    try:
        return dt.date.fromisoformat(value).isoformat()
    except (TypeError, ValueError):
        raise ValueError('Provide a valid date.') from None

class Conflict(Exception):
    pass

class Store:
    def __init__(self, path, agent_factory=None):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.agent_factory = agent_factory or (lambda mode: Agents(mode))
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS cases(id TEXT PRIMARY KEY, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS notes(id TEXT PRIMARY KEY, case_id TEXT NOT NULL, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, case_id TEXT NOT NULL, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS reminders(id TEXT PRIMARY KEY, case_id TEXT NOT NULL, data TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audits(id INTEGER PRIMARY KEY, at TEXT, case_id TEXT, operation TEXT, data TEXT);
        CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY, case_id TEXT, run_id TEXT);
        ''')
        with self.lock:
            if not self.db.execute('SELECT id FROM cases LIMIT 1').fetchone():
                self.reset(initial=True)
            self.set_default('mode', 'demo')
            self.set_default('clock', 'demo')
            self.set_default('today', dt.date.today().isoformat())
            self.set_default('revision', 0)
            self.set_default('snoozes', {})
            self.db.commit()
            # An interrupted provider call is recoverable; never leave an eternal spinner.
            for row in self.db.execute('SELECT data FROM runs').fetchall():
                run = json.loads(row['data'])
                if run['status'] == 'Running':
                    run.update(status='Failed', error='The server restarted during analysis. The source is saved; retry explicitly.')
                    self.save_run(run)
            self.db.commit()

    def setting(self, key):
        row = self.db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def set_default(self, key, value):
        self.db.execute('INSERT OR IGNORE INTO settings VALUES (?,?)', (key, json.dumps(value)))

    def set_setting(self, key, value):
        self.db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, json.dumps(value)))

    def today(self):
        return dt.date.today().isoformat() if self.setting('clock') == 'system' else self.setting('today')

    def get(self, case_id):
        row = self.db.execute('SELECT data FROM cases WHERE id=?', (case_id,)).fetchone()
        if not row:
            raise KeyError('Case not found.')
        return json.loads(row['data'])

    def put(self, case):
        self.db.execute('INSERT OR REPLACE INTO cases VALUES (?,?)', (case['id'], json.dumps(case)))

    def get_run(self, run_id):
        row = self.db.execute('SELECT data FROM runs WHERE id=?', (run_id,)).fetchone()
        if not row:
            raise KeyError('Analysis not found.')
        return json.loads(row['data'])

    def save_run(self, run):
        self.db.execute('INSERT OR REPLACE INTO runs VALUES (?,?,?)', (run['id'], run['caseId'], json.dumps(run)))

    def note(self, note_id):
        row = self.db.execute('SELECT data FROM notes WHERE id=?', (note_id,)).fetchone()
        if not row:
            raise KeyError('Source not found.')
        return json.loads(row['data'])

    def audit(self, case_id, operation, data):
        self.db.execute('INSERT INTO audits(at,case_id,operation,data) VALUES (?,?,?,?)', (iso_now(), case_id, operation, json.dumps(data)))

    def check_revision(self, c, body):
        if body.get('revision') != c['revision']:
            raise Conflict('This case changed in another view. Refresh it and review the latest evidence before saving.')

    def record(self, c, title, detail, before=None):
        after = calculate(c)
        event = {'id': uid('ev'), 'date': self.today(), 'at': iso_now(), 'title': title, 'detail': detail}
        c['events'].insert(0, event)
        c['revision'] += 1
        if (c.get('approval') or {}).get('status') in ('Approved', 'Exception approved') and not title.startswith('Human approval decision:'):
            c['approval']['outdated'] = True
        if before is not None:
            c['versions'].append({'id': uid('ver'), 'date': self.today(), 'at': iso_now(), 'title': title, 'detail': detail,
                'before': before, 'after': after, 'delta': delta(before, after), 'ruleVersion': RULE_VERSION, 'caseRevision': c['revision']})
        self.put(c)
        self.audit(c['id'], title, {'detail': detail, 'revision': c['revision']})
        self.db.commit()

    def reset(self, initial=False):
        # Reset is called only by an explicit UI action (or an empty database).
        for table in ('cases', 'notes', 'runs', 'reminders', 'requests'):
            self.db.execute('DELETE FROM ' + table)
        seed = json.loads((ROOT / 'data/seed.json').read_text())
        for c in seed['cases']:
            self.put(c)
        self.set_setting('today', seed['today'])
        self.set_setting('clock', 'demo')
        self.set_setting('snoozes', {})
        self.set_setting('mode', 'demo')
        self.audit('', 'Initialize samples' if initial else 'Reset sample workspace', {})
        self.db.commit()

    def scan_reminders(self):
        with self.lock:
            today = self.today()
            snoozes = self.setting('snoozes') or {}
            active = set()
            for row in self.db.execute('SELECT data FROM cases').fetchall():
                c = json.loads(row['data'])
                for task in c['tasks']:
                    if task['status'] == 'Open' and task['due'] < today:
                        rid = 'task-' + task['id']
                        active.add(rid)
                        data = {'id': rid, 'caseId': c['id'], 'company': c['company'], 'taskId': task['id'], 'title': task['title'],
                            'reason': task['side'] + ' action overdue since ' + task['due'] + '. Check what happened before sending a follow-up.', 'type': 'Overdue action', 'observedAt': iso_now()}
                        self.db.execute('INSERT OR REPLACE INTO reminders VALUES (?,?,?)', (rid, c['id'], json.dumps(data)))
                if c['stage'] == 'Active' and not c.get('blocker') and (dt.date.fromisoformat(today) - dt.date.fromisoformat(c['lastProgress'])).days >= 14:
                    rid = 'stale-' + c['id']
                    active.add(rid)
                    data = {'id': rid, 'caseId': c['id'], 'company': c['company'], 'title': 'No substantive progress recorded',
                        'reason': 'Last recorded progress: ' + c['lastProgress'] + '. Confirm whether activity is missing or the approach needs adjustment.', 'type': 'Progress review', 'observedAt': iso_now()}
                    self.db.execute('INSERT OR REPLACE INTO reminders VALUES (?,?,?)', (rid, c['id'], json.dumps(data)))
            for row in self.db.execute('SELECT id FROM reminders').fetchall():
                if row['id'] not in active:
                    self.db.execute('DELETE FROM reminders WHERE id=?', (row['id'],))
            self.set_setting('lastScan', iso_now())
            self.db.commit()
            return [json.loads(r['data']) for r in self.db.execute('SELECT data FROM reminders').fetchall() if (snoozes.get(json.loads(r['data'])['id']) or '') < today]

    def bootstrap(self):
        with self.lock:
            cases = []
            for row in self.db.execute('SELECT data FROM cases ORDER BY rowid').fetchall():
                c = json.loads(row['data'])
                c['result'] = calculate(c)
                c['sources'] = [json.loads(x['data']) for x in self.db.execute('SELECT data FROM notes WHERE case_id=? ORDER BY rowid DESC', (c['id'],)).fetchall()]
                c['analysisRuns'] = [json.loads(x['data']) for x in self.db.execute('SELECT data FROM runs WHERE case_id=? ORDER BY rowid DESC', (c['id'],)).fetchall()]
                cases.append(c)
            return {'cases': cases, 'today': self.today(), 'mode': self.setting('mode'), 'clock': self.setting('clock'),
                'reminders': self.scan_reminders(), 'rules': RULES, 'ruleVersion': RULE_VERSION,
                'config': {'liveConfigured': bool(os.getenv('OPENAI_API_KEY') and os.getenv('OPENAI_MODEL')), 'model': os.getenv('OPENAI_MODEL', ''),
                    'storage': 'SQLite', 'lastScan': self.setting('lastScan'), 'crm': 'Not connected', 'mail': 'Manual import', 'multiUserAuth': False}}

    def create_case(self, body):
        with self.lock:
            co, pr, ct, ow = [string(body.get(k), k, 300) for k in ('company', 'project', 'contact', 'owner')]
            today = self.today()
            c = {'id': uid('case'), 'company': co, 'project': pr, 'contact': ct, 'owner': ow, 'role': 'Pending confirmation', 'industry': 'Pending confirmation',
                 'scores': {k: None for k in WEIGHTS}, 'conditions': {k: True if k == 'conflict' else None for k in CONDITIONS},
                 'revenue': None, 'problem': 'Pending confirmation. Begin discovery with the client.', 'stage': 'Contacted', 'blocker': None,
                 'lastProgress': today, 'lastReply': None, 'tasks': [], 'evidence': [], 'events': [], 'versions': [], 'approval': None, 'sample': False, 'revision': 0}
            self.record(c, 'Case created', 'Named owner assigned. Qualification information is pending.', calculate(c))
            return c

    def ingest(self, case_id, body):
        with self.lock:
            c = self.get(case_id)
            request_id = string(body.get('requestId'), 'request id', 100)
            prior = self.db.execute('SELECT case_id, run_id FROM requests WHERE id=?', (request_id,)).fetchone()
            if prior:
                if prior['case_id'] != case_id:
                    raise Conflict('This request id belongs to a different case.')
                return self.get_run(prior['run_id'])
            for row in self.db.execute('SELECT data FROM runs WHERE case_id=?', (case_id,)).fetchall():
                if json.loads(row['data'])['status'] == 'Running':
                    raise Conflict('An analysis is already running for this case. Refresh its status before starting another.')
            self.check_revision(c, body)
            if body.get('sourceId'):
                note = self.note(body['sourceId'])
                if note['caseId'] != case_id:
                    raise ValueError('Source does not belong to this case.')
            else:
                text = string(body.get('text'), 'source text', 16000)
                source = string(body.get('source'), 'source label', 300)
                kind = body.get('kind')
                if kind not in ('Client reply', 'Meeting notes', 'Relationship record', 'Other evidence'):
                    raise ValueError('Select the evidence type.')
                date = day(body.get('date'))
                if date > self.today():
                    raise ValueError('Evidence cannot be dated in the future.')
                note = {'id': uid('src'), 'caseId': case_id, 'text': text, 'source': source, 'kind': kind, 'date': date, 'createdAt': iso_now(), 'sample': self.setting('mode') == 'demo'}
                self.db.execute('INSERT INTO notes VALUES (?,?,?)', (note['id'], case_id, json.dumps(note)))
                if kind == 'Client reply':
                    c['stage'] = 'Active'
                    c['lastReply'] = max(filter(None, (c.get('lastReply'), date)))
                    title = 'Client reply recorded · Active policy applied'
                    detail = 'Reply stored independently of AI analysis. Qualification scores are unchanged pending human review.'
                else:
                    title, detail = 'Source recorded', source + '. Awaiting evidence analysis and consultant review.'
                self.record(c, title, detail)
            run = {'id': uid('run'), 'caseId': case_id, 'sourceId': note['id'], 'source': note['source'], 'date': note['date'], 'createdAt': iso_now(),
                'mode': self.setting('mode'), 'status': 'Running', 'baseRevision': c['revision'], 'proposals': [], 'findings': [], 'calls': [], 'error': None}
            # A re-analysis replaces only unreviewed proposals from this same source.
            # Reviewed decisions and old outputs remain available for audit.
            if body.get('sourceId'):
                for row in self.db.execute('SELECT data FROM runs WHERE case_id=?', (case_id,)).fetchall():
                    previous = json.loads(row['data'])
                    if previous.get('sourceId') == note['id'] and any(p['status'] == 'Pending' for p in previous.get('proposals', [])):
                        for proposal in previous['proposals']:
                            if proposal['status'] == 'Pending':
                                proposal['status'] = 'Superseded'
                        previous.update(status='Superseded', supersededBy=run['id'])
                        self.save_run(previous)
            self.save_run(run)
            self.db.execute('INSERT INTO requests VALUES (?,?,?)', (request_id, case_id, run['id']))
            self.db.commit()
        # Do not hold the DB lock while a remote provider works.
        agent = self.agent_factory(run['mode'])
        try:
            result = agent.analyze(note, c)
            with self.lock:
                current = self.get(case_id)
                run.update(result)
                run['status'] = 'Awaiting review' if run['proposals'] else 'Complete'
                run['stale'] = current['revision'] != run['baseRevision']
        except ProviderError as e:
            run.update(status='Failed', error=str(e), calls=agent.calls)
        except Exception:
            run.update(status='Failed', error='Analysis failed validation. Your source is saved; no changes were applied.', calls=agent.calls)
        with self.lock:
            self.save_run(run)
            self.audit(case_id, 'Evidence analysis ' + run['status'], {'runId': run['id'], 'mode': run['mode'], 'calls': run['calls']})
            self.db.commit()
        return run

    def review(self, run_id, body):
        with self.lock:
            run = self.get_run(run_id)
            c = self.get(run['caseId'])
            self.check_revision(c, body)
            if run['baseRevision'] != c['revision']:
                raise Conflict('Evidence changed since this analysis. Re-analyze the saved source before applying suggestions.')
            p = next((p for p in run['proposals'] if p['id'] == body.get('proposalId')), None)
            if p is None or p['status'] != 'Pending':
                raise Conflict('This suggestion was already reviewed or does not exist.')
            action = body.get('decision')
            if action not in ('Accept', 'Edit', 'Reject'):
                raise ValueError('Choose Accept, Edit or Reject.')
            reason = string(body.get('reason', ''), 'review reason', 3000, required=action in ('Edit', 'Reject') or p['conflict'])
            before = calculate(c)
            target = p['target']
            finding = next(f for f in run['findings'] if f['id'] == p['finding_id'])
            note = self.note(run['sourceId'])
            if finding['quote'] not in note['text']:
                raise ValueError('Source verification failed.')
            old = c['scores'].get(target) if target in WEIGHTS else c['conditions'].get(target.split(':')[1]) if target.startswith('condition:') else c['blocker']
            if action != 'Reject':
                if target in WEIGHTS:
                    value = body.get('score') if action == 'Edit' else p['score']
                    validate_score(target, value)
                    c['scores'][target] = value
                    if target == 'N' and value is not None and value >= 2:
                        # Keep the original full note as source; this is a reviewed summary.
                        c['problem'] = finding['fact']
                elif target.startswith('condition:'):
                    value = body.get('conditionValue') if action == 'Edit' else p['condition_value']
                    if value is not None and type(value) is not bool:
                        raise ValueError('Invalid condition value.')
                    c['conditions'][target.split(':')[1]] = value
                    if target == 'condition:external' and value is False:
                        c['blocker'] = 'NO_EXTERNAL_SUPPORT'
                    if target == 'condition:fit' and value is False:
                        c['blocker'] = 'SERVICE_MISMATCH'
                else:
                    value = p['blocker_code']
                    c['blocker'] = value
                    if value == 'NO_EXTERNAL_SUPPORT':
                        c['conditions']['external'] = False
                    elif value == 'SERVICE_MISMATCH':
                        c['conditions']['fit'] = False
                if p['conflict']:
                    c['conditions']['conflict'] = False
                if target == 'E' and value is not None and value >= 3:
                    c['lastProgress'] = max(c['lastProgress'], note['date'])
                evidence = {'id': uid('e'), 'dimension': target, 'title': 'Consultant-confirmed ' + (LABELS.get(target, target)),
                    'quote': finding['quote'], 'source': note['source'], 'sourceId': note['id'], 'runId': run['id'], 'date': note['date'],
                    'status': 'Confirmed', 'score': value if target in WEIGHTS else None, 'interpretation': p['explanation'], 'reviewReason': reason,
                    'reviewedAt': iso_now(), 'origin': 'Live AI suggestion · human confirmed' if run['mode'] == 'live' else 'Demo text rule · human confirmed',
                    'reviewDecision': action, 'oldValue': old, 'newValue': value}
                c['evidence'].append(evidence)
            p.update(status={'Accept': 'Accepted', 'Edit': 'Edited', 'Reject': 'Rejected'}[action], reviewedAt=iso_now(), reviewReason=reason)
            self.record(c, 'Suggestion ' + p['status'].lower(), target + ': ' + p['explanation'] + (' Review: ' + reason if reason else ''), before if action != 'Reject' else None)
            run['baseRevision'] = c['revision']
            run['status'] = 'Awaiting review' if any(x['status'] == 'Pending' for x in run['proposals']) else 'Complete'
            self.save_run(run)
            self.db.commit()
            return c

    def manual_review(self, case_id, body):
        with self.lock:
            c = self.get(case_id)
            self.check_revision(c, body)
            reason = string(body.get('reason'), 'review evidence and rationale', 4000)
            kind = body.get('kind')
            before = calculate(c)
            if kind == 'score':
                k, value = body.get('dimension'), body.get('score')
                validate_score(k, value)
                old = c['scores'][k]
                c['scores'][k] = value
                c['evidence'].append({'id': uid('e'), 'dimension': k, 'title': 'Manual consultant review', 'quote': reason,
                    'source': 'Manual review · ' + c['owner'], 'date': self.today(), 'status': 'Confirmed', 'score': value,
                    'interpretation': 'Pending confirmation' if value is None else 'Confirmed first-time relationship; neutral rule.' if value == 2.5 else ANCHORS[k][int(value)], 'origin': 'Human review'})
                detail = LABELS[k] + ': ' + str(old) + ' → ' + str(value) + '. ' + reason
            elif kind == 'revenue':
                revenue = body.get('revenue')
                if revenue is not None:
                    if not isinstance(revenue, dict):
                        raise ValueError('Provide a valid revenue estimate.')
                    low, high = revenue.get('low'), revenue.get('high')
                    if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in (low, high)) or low < 0 or high < low or high > 1e12:
                        raise ValueError('Revenue estimates must be non-negative, with upper ≥ lower.')
                    revenue = {'low': low, 'high': high, 'basis': reason, 'date': self.today()}
                c['revenue'] = revenue
                detail = 'CGS fee revenue estimate reviewed. ' + reason
            elif kind == 'conditions':
                values = body.get('conditions')
                if not isinstance(values, dict) or set(values) != set(CONDITIONS) or any(v is not None and type(v) is not bool for v in values.values()):
                    raise ValueError('Review all six qualification conditions.')
                blocker = body.get('blocker') or None
                if blocker is not None and blocker not in REASONS:
                    raise ValueError('Choose a valid Not ready reason.')
                c['conditions'], c['blocker'] = values, blocker
                if values['external'] is False:
                    c['blocker'] = 'NO_EXTERNAL_SUPPORT'
                elif values['fit'] is False:
                    c['blocker'] = 'SERVICE_MISMATCH'
                detail = reason
            elif kind == 'problem':
                c['problem'] = string(body.get('problem'), 'client problem summary', 5000)
                detail = 'Client problem summary updated. ' + reason
            elif kind == 'source':
                note = self.note(body.get('sourceId'))
                if note['caseId'] != case_id or body.get('confirmed') is not True:
                    raise ValueError('Confirm that you reviewed this case source and recorded any required assessment changes.')
                note['manualReview'] = {'at': iso_now(), 'reviewer': c['owner'], 'reason': reason}
                self.db.execute('UPDATE notes SET data=? WHERE id=?', (json.dumps(note), note['id']))
                detail = 'Original source reviewed manually: ' + note['source'] + '. ' + reason
            else:
                raise ValueError('Unknown manual review type.')
            self.record(c, {'score': 'Rating reviewed', 'revenue': 'Revenue estimate reviewed', 'conditions': 'Qualification conditions reviewed', 'source': 'Source reviewed manually', 'problem': 'Client problem summary reviewed'}[kind], detail, before)
            return c

    def task(self, case_id, body):
        with self.lock:
            c = self.get(case_id)
            self.check_revision(c, body)
            op = body.get('operation', 'create')
            if op == 'create':
                title, owner = string(body.get('title'), 'action title', 500), string(body.get('assignee'), 'action owner', 200)
                due = day(body.get('due'))
                side = body.get('side')
                if side not in ('CGS', 'Client') or body.get('confirmed') is not True:
                    raise ValueError('Confirm the task owner and agreed date before recording an action.')
                c['tasks'].append({'id': uid('t'), 'title': title, 'assignee': owner, 'due': due, 'side': side, 'status': 'Open', 'createdAt': iso_now()})
                detail = title + ' · ' + owner + ' · due ' + due
            else:
                task = next((t for t in c['tasks'] if t['id'] == body.get('taskId')), None)
                if task is None:
                    raise KeyError('Task not found.')
                if op == 'toggle':
                    task['status'] = 'Done' if task['status'] == 'Open' else 'Open'
                    if task['status'] == 'Done':
                        c['lastProgress'] = self.today()
                    detail = task['title'] + ' · ' + task['status'] + '. No scores changed automatically.'
                elif op == 'reschedule':
                    task['due'] = day(body.get('due'))
                    detail = task['title'] + ' · due ' + task['due'] + '. ' + string(body.get('reason'), 'rescheduling reason')
                else:
                    raise ValueError('Unknown task operation.')
            self.record(c, 'Agreed action ' + op, detail)
            return c

    def generate(self, case_id, kind, body):
        with self.lock:
            c = self.get(case_id)
            self.check_revision(c, body)
            if kind == 'outreach' and body.get('operation') == 'save':
                if not c.get('outreach'):
                    raise ValueError('Generate an outreach draft first.')
                c['outreach']['subject'] = string(body.get('subject'), 'draft subject', 400)
                c['outreach']['draft'] = string(body.get('draft'), 'draft text', 15000)
                c['outreach']['editedAt'] = iso_now()
                self.put(c)
                self.audit(case_id, 'Human edited outreach draft', {})
                self.db.commit()
                return c['outreach']
            mode, base = self.setting('mode'), c['revision']
        agent = self.agent_factory(mode)
        context = {**c, 'workspaceDate': self.today()}
        output = agent.outreach(context) if kind == 'outreach' else agent.actions(context)
        with self.lock:
            current = self.get(case_id)
            if current['revision'] != base:
                raise Conflict('The case changed while generating this draft. Generate it again against the latest evidence.')
            output.update(mode=mode, createdAt=iso_now(), baseRevision=base)
            current['outreach' if kind == 'outreach' else 'advice'] = output
            # Store a draft without changing the evidence revision or an approval snapshot.
            self.put(current)
            self.audit(case_id, 'Generated ' + kind, {'mode': mode, 'calls': agent.calls})
            self.db.commit()
        return output

    def approval(self, case_id, body):
        with self.lock:
            c = self.get(case_id)
            self.check_revision(c, body)
            op = body.get('operation')
            if op in ('submit', 'exception'):
                existing = c.get('approval')
                if existing and (existing['status'] in ('Submitted', 'Exception requested') or existing['status'] in ('Approved', 'Exception approved') and not existing.get('outdated')):
                    raise Conflict('An approval already exists. Return the pack before resubmitting.')
                result = calculate(c)
                pending = any(p['status'] == 'Pending' for row in self.db.execute('SELECT data FROM runs WHERE case_id=?', (case_id,)).fetchall() for p in json.loads(row['data']).get('proposals', []))
                if op == 'submit' and pending:
                    raise ValueError('Review all pending evidence suggestions before standard submission.')
                if op == 'submit':
                    latest = {}
                    for row in self.db.execute('SELECT data FROM runs WHERE case_id=? ORDER BY rowid', (case_id,)).fetchall():
                        run = json.loads(row['data'])
                        latest[run['sourceId']] = run
                    for source_id, run in latest.items():
                        if run['status'] in ('Running', 'Failed') and not self.note(source_id).get('manualReview'):
                            raise ValueError('A source has an incomplete analysis. Re-analyze it or record a manual source review before submitting.')
                if op == 'submit' and (c['stage'] != 'Active' or result['readiness'] != 'Ready'):
                    raise ValueError('Standard submission requires an Active case that meets all Ready conditions.')
                reason = string(body.get('reason', ''), 'exception rationale', required=op == 'exception')
                if existing:
                    c.setdefault('approvalHistory', []).append(copy.deepcopy(existing))
                snapshot = copy.deepcopy(c)
                snapshot.pop('approval', None)
                snapshot.pop('approvalHistory', None)
                c['approval'] = {'id': uid('approval'), 'status': 'Submitted' if op == 'submit' else 'Exception requested', 'reason': reason,
                    'date': self.today(), 'version': c['revision'] + 1, 'ruleVersion': RULE_VERSION,
                    'snapshot': {'case': snapshot, 'result': result, 'date': self.today()}, 'events': []}
                self.record(c, 'Qualification pack submitted', 'Frozen evidence snapshot for a human review. No CRM update.')
            elif op in ('approve', 'return', 'defer'):
                a = c.get('approval')
                if not a or a['status'] not in ('Submitted', 'Exception requested'):
                    raise ValueError('No pending qualification pack.')
                if op == 'approve' and a['version'] != c['revision']:
                    raise Conflict('Evidence or tasks changed after submission. Return and resubmit an updated pack before approval.')
                reason = string(body.get('reason', ''), 'review rationale', required=op != 'approve' or a['status'] == 'Exception requested')
                a['status'] = ('Exception approved' if a['status'] == 'Exception requested' else 'Approved') if op == 'approve' else 'Returned' if op == 'return' else 'Deferred'
                a['reviewedAt'] = iso_now()
                a['events'].append({'decision': a['status'], 'reason': reason, 'at': iso_now(), 'reviewer': 'Local human reviewer'})
                self.record(c, 'Human approval decision: ' + a['status'], reason or 'Human reviewed the frozen qualification pack. No CRM update.')
            else:
                raise ValueError('Unknown approval action.')
            return c

    def config(self, body):
        with self.lock:
            mode = body.get('mode', self.setting('mode'))
            clock = body.get('clock', self.setting('clock'))
            if mode not in ('demo', 'live') or clock not in ('demo', 'system'):
                raise ValueError('Choose a valid mode and clock.')
            self.set_setting('mode', mode)
            self.set_setting('clock', clock)
            if 'today' in body:
                self.set_setting('today', day(body['today']))
            self.audit('', 'Workspace configuration changed', {'mode': mode, 'clock': clock})
            self.db.commit()

    def snooze(self, body):
        with self.lock:
            rid = string(body.get('id'), 'reminder id', 100)
            snoozes = self.setting('snoozes') or {}
            snoozes[rid] = (dt.date.fromisoformat(self.today()) + dt.timedelta(days=7)).isoformat()
            self.set_setting('snoozes', snoozes)
            self.db.commit()

class Handler(SimpleHTTPRequestHandler):
    server_version = 'CGSLocal/2.0'
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / 'dist'), **kwargs)

    def log_message(self, fmt, *args):
        # Paths only, no bodies or credentials.
        pass

    def send_json(self, status, value):
        data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(data)

    def valid_host(self):
        return self.headers.get('Host', '').split(':')[0] in ('127.0.0.1', 'localhost')

    def do_GET(self):
        if not self.valid_host():
            return self.send_json(403, {'error': 'Local workspace access only.'})
        path = urlsplit(self.path).path
        if path in ('/api/bootstrap', '/api/health'):
            data = {'ok': True} if path == '/api/health' else self.server.store.bootstrap()
            if path == '/api/bootstrap':
                data['csrf'] = self.server.csrf
            return self.send_json(200, data)
        if path.startswith('/api/'):
            return self.send_json(404, {'error': 'API route not found.'})
        if path not in ('/', '/index.html', '/app.js', '/engine.js', '/styles.css', '/favicon.ico'):
            return self.send_json(404, {'error': 'File not found.'})
        super().do_GET()

    def do_POST(self):
        try:
            if not self.valid_host():
                return self.send_json(403, {'error': 'Local workspace access only.'})
            origin = self.headers.get('Origin')
            if origin and origin not in ('http://127.0.0.1:' + str(self.server.server_port), 'http://localhost:' + str(self.server.server_port)):
                return self.send_json(403, {'error': 'Cross-origin changes are not allowed.'})
            if self.headers.get('X-CSRF-Token') != self.server.csrf:
                return self.send_json(403, {'error': 'Refresh the workspace before making changes.'})
            size = int(self.headers.get('Content-Length', 0))
            if size < 1 or size > 100000:
                raise ValueError('Request body is empty or too large.')
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict):
                raise ValueError('Expected a JSON object.')
            path = urlsplit(self.path).path.strip('/').split('/')
            store = self.server.store
            result = None
            if path == ['api', 'cases']:
                result = store.create_case(body)
            elif len(path) == 4 and path[:2] == ['api', 'cases']:
                case_id, op = path[2:]
                if op == 'analyze':
                    result = store.ingest(case_id, body)
                elif op == 'review':
                    result = store.manual_review(case_id, body)
                elif op == 'tasks':
                    result = store.task(case_id, body)
                elif op in ('outreach', 'actions'):
                    result = store.generate(case_id, op, body)
                elif op == 'approval':
                    result = store.approval(case_id, body)
                else:
                    raise KeyError('API route not found.')
            elif len(path) == 4 and path[:2] == ['api', 'runs'] and path[3] == 'review':
                result = store.review(path[2], body)
            elif path == ['api', 'config']:
                store.config(body)
            elif path == ['api', 'reminders', 'snooze']:
                store.snooze(body)
            elif path == ['api', 'demo', 'reset']:
                if body.get('confirm') != 'RESET':
                    raise ValueError('Explicit reset confirmation is required.')
                with store.lock:
                    store.reset()
            else:
                raise KeyError('API route not found.')
            self.send_json(200, {'ok': True, 'result': result})
        except Conflict as e:
            self.send_json(409, {'error': str(e)})
        except ProviderError as e:
            self.send_json(502, {'error': str(e)})
        except (ValueError, TypeError) as e:
            self.send_json(400, {'error': str(e)})
        except KeyError as e:
            self.send_json(404, {'error': str(e).strip("'")})
        except Exception:
            self.send_json(500, {'error': 'The operation failed. Refresh and retry; existing records are preserved.'})

def make_server(port=8765, db_path=None, agent_factory=None):
    store = Store(db_path or os.getenv('CGS_DB_PATH', str(ROOT / 'runtime/cgs.sqlite3')), agent_factory)
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    server.store, server.csrf = store, secrets.token_urlsafe(32)
    return server

def main():
    load_env()
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    server = make_server(args.port)
    stop = threading.Event()
    def monitor():
        while not stop.is_set():
            try:
                server.store.scan_reminders()
            except Exception:
                pass
            stop.wait(30)
    threading.Thread(target=monitor, daemon=True).start()
    print('CGS workspace: http://127.0.0.1:%s/' % server.server_port, flush=True)
    print('SQLite persistence enabled. API keys remain server-side. Ctrl+C to stop.', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
        server.store.db.close()

if __name__ == '__main__':
    main()
