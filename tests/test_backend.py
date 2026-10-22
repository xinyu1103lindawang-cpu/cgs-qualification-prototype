import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch
from server import Store, Conflict, make_server
from backend.rules import calculate
from backend.agents import Agents, ProviderError, validate_proposals

class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'test.sqlite3'
        self.store = Store(self.path)
    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()
    def ingest(self, text, case='cedar', kind='Meeting notes', request_id='test-request'):
        c = self.store.get(case)
        return self.store.ingest(case, {'revision': c['revision'], 'requestId': request_id, 'text': text,
            'source': 'Test original email', 'kind': kind, 'date': '2026-10-20'})
    def review(self, run, pid='p1', decision='Accept', **extra):
        return self.store.review(run['id'], {'revision': self.store.get(run['caseId'])['revision'], 'proposalId': pid, 'decision': decision, 'reason': '', **extra})
    def test_budget_analysis_requires_review_then_changes_q_and_p(self):
        run = self.ingest('Our project budget is approved and covers the initial scope.')
        self.assertEqual(self.store.get('cedar')['scores']['B'], 3)
        c = self.review(run)
        self.assertEqual((calculate(c)['ql'], calculate(c)['pl']), (74, 76.4))
        self.assertEqual((c['versions'][-1]['delta']['q'], c['versions'][-1]['delta']['p']), (5, 3))
        self.assertEqual(c['evidence'][-1]['sourceId'], run['sourceId'])
    def test_multiple_findings_are_reviewed_independently(self):
        run = self.ingest('Our project budget is approved and covers the initial scope. We shared the requested process maps.')
        self.assertEqual(len(run['proposals']), 2)
        self.review(run)
        self.assertEqual(self.store.get_run(run['id'])['proposals'][1]['status'], 'Pending')
        self.review(run, 'p2')
        self.assertEqual(self.store.get_run(run['id'])['status'], 'Complete')
    def test_rejection_preserves_scores_and_records_reason(self):
        run = self.ingest('Our project budget is approved and covers the initial scope.')
        c = self.review(run, decision='Reject', reason='Approval does not cover this scope.')
        self.assertEqual(c['scores']['B'], 3)
        self.assertEqual(len(c['versions']), 1)
        self.assertIn('does not cover', c['events'][0]['detail'])
    def test_edited_score_requires_rationale_and_valid_anchor(self):
        run = self.ingest('Our project budget is approved and covers the initial scope.')
        with self.assertRaises(ValueError): self.review(run, decision='Edit', score=5)
        with self.assertRaises(ValueError): self.review(run, decision='Edit', score=8, reason='Test')
        c = self.review(run, decision='Edit', score=3, reason='Consulting spend still awaits approval.')
        self.assertEqual(c['scores']['B'], 3)
        self.assertEqual(self.store.get_run(run['id'])['proposals'][0]['status'], 'Edited')
    def test_client_reply_active_even_if_live_analysis_fails(self):
        with patch.dict('os.environ', {}, clear=True):
            self.store.config({'mode': 'live'})
            run = self.ingest('Thanks, received.', 'summit', 'Client reply')
        c = self.store.get('summit')
        self.assertEqual(c['stage'], 'Active')
        self.assertIsNone(c['scores']['E'])
        self.assertEqual((run['mode'], run['status']), ('live', 'Failed'))
        self.assertIn('no demo fallback', run['error'])
        self.assertEqual(self.store.note(run['sourceId'])['text'], 'Thanks, received.')
    def test_courtesy_reply_does_not_qualify(self):
        run = self.ingest('Thank you for sending this through. I have received the introduction.', 'summit', 'Client reply')
        c = self.review(run)
        self.assertEqual((c['stage'], c['scores']['E']), ('Active', 1))
        self.assertIsNone(c['scores']['B'])
        self.assertEqual(calculate(c)['readiness'], 'More evidence needed')
    def test_text_not_a_scenario_controls_demo_result(self):
        a = self.ingest('Unrelated note without qualification facts.', request_id='one')
        b = self.ingest('Our CFO is joining the next discussion.', request_id='two')
        self.assertEqual(a['proposals'], [])
        self.assertEqual(b['proposals'][0]['target'], 'A')
        self.assertNotIn('scenario', b)
    def test_idempotency_does_not_duplicate_sources(self):
        text = 'Our project budget is approved and covers the initial scope.'
        self.assertEqual(self.ingest(text)['id'], self.ingest(text)['id'])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM notes').fetchone()[0], 1)
    def test_stale_analysis_and_old_revision_are_blocked(self):
        run = self.ingest('Our project budget is approved and covers the initial scope.')
        old = self.store.get('cedar')['revision']
        self.store.manual_review('cedar', {'revision': old, 'kind': 'score', 'dimension': 'T', 'score': 4, 'reason': 'New plan confirmed.'})
        with self.assertRaises(Conflict): self.review(run)
        with self.assertRaises(Conflict): self.store.manual_review('cedar', {'revision': old, 'kind': 'score', 'dimension': 'B', 'score': 5, 'reason': 'Stale edit'})
        self.assertEqual(self.store.get('cedar')['scores']['B'], 3)
    def test_reanalysis_reuses_saved_source_against_latest_revision(self):
        run = self.ingest('Our project budget is approved and covers the initial scope.')
        c = self.store.get('cedar')
        self.store.manual_review(c['id'], {'revision': c['revision'], 'kind': 'score', 'dimension': 'T', 'score': 4, 'reason': 'New plan.'})
        c = self.store.get('cedar')
        new = self.store.ingest(c['id'], {'revision': c['revision'], 'sourceId': run['sourceId'], 'requestId': 'rerun'})
        self.assertEqual(new['baseRevision'], c['revision'])
        self.assertEqual(new['sourceId'], run['sourceId'])
        self.review(new)
    def test_duplicate_review_is_blocked(self):
        run = self.ingest('Our project budget is approved and covers the initial scope.')
        self.review(run)
        with self.assertRaises(Conflict): self.review(run)
    def test_confirmed_first_time_is_distinct_from_missing_history(self):
        c = self.store.create_case(dict(company='Test Co', project='Pilot', contact='Client', owner='Consultant'))
        self.assertIsNone(c['scores']['H'])
        c = self.review(self.ingest('This is our first project together.', c['id']))
        self.assertEqual(c['scores']['H'], 2.5)
    def test_high_revenue_does_not_override_explicit_blocker(self):
        c = self.store.get('atlas')
        self.assertEqual((calculate(c)['pl'], calculate(c)['readiness']), (94.6, 'Not ready'))
        with self.assertRaises(ValueError): self.store.approval(c['id'], {'revision': c['revision'], 'operation': 'submit'})
    def test_conflict_needs_rationale_and_separate_resolution(self):
        run = self.ingest('We are not considering external consulting support for this project.')
        self.assertTrue(run['proposals'][0]['conflict'])
        with self.assertRaises(ValueError): self.review(run)
        c = self.review(run, reason='Contradicts earlier openness; clarification needed.')
        self.assertFalse(c['conditions']['conflict'])
        self.assertEqual(c['blocker'], 'NO_EXTERNAL_SUPPORT')
    def test_database_survives_server_reopen(self):
        run = self.ingest('Our project budget is approved and covers the initial scope.')
        self.review(run)
        self.store.db.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.get('cedar')['scores']['B'], 4)
        self.assertEqual(self.store.get_run(run['id'])['status'], 'Complete')
        self.assertEqual(self.store.note(run['sourceId'])['source'], 'Test original email')
    def test_interrupted_analysis_is_recoverable(self):
        self.store.save_run({'id': 'interrupted', 'caseId': 'cedar', 'status': 'Running'})
        self.store.db.commit()
        self.store.db.close()
        self.store = Store(self.path)
        self.assertEqual(self.store.get_run('interrupted')['status'], 'Failed')
    def test_background_reminders_do_not_deduct_scores(self):
        before = calculate(self.store.get('cedar'))
        self.store.config({'clock': 'demo', 'today': '2026-11-20'})
        reminders = self.store.scan_reminders()
        self.assertTrue(any(r['caseId'] == 'cedar' for r in reminders))
        self.assertEqual(calculate(self.store.get('cedar')), before)
        rid = reminders[0]['id']
        self.store.snooze({'id': rid})
        self.assertNotIn(rid, [r['id'] for r in self.store.scan_reminders()])
    def test_approval_snapshot_is_frozen_and_changes_block_approval(self):
        c = self.store.get('northstar')
        c = self.store.approval(c['id'], {'revision': c['revision'], 'operation': 'submit'})
        self.store.manual_review(c['id'], {'revision': c['revision'], 'kind': 'score', 'dimension': 'B', 'score': 3, 'reason': 'Approval status changed.'})
        c = self.store.get(c['id'])
        self.assertEqual(c['approval']['snapshot']['case']['scores']['B'], 4)
        with self.assertRaises(Conflict): self.store.approval(c['id'], {'revision': c['revision'], 'operation': 'approve'})
    def test_standard_approval_creates_local_decision_record(self):
        c = self.store.get('northstar')
        c = self.store.approval(c['id'], {'revision': c['revision'], 'operation': 'submit'})
        c = self.store.approval(c['id'], {'revision': c['revision'], 'operation': 'approve'})
        self.assertEqual((c['approval']['status'], c['stage']), ('Approved', 'Active'))
    def test_task_needs_agreement_and_cannot_change_scores(self):
        c = self.store.get('northstar')
        body = {'revision': c['revision'], 'title': 'Client to send materials', 'assignee': 'Client', 'side': 'Client', 'due': '2026-10-23'}
        with self.assertRaises(ValueError): self.store.task(c['id'], body)
        before = calculate(c)
        c = self.store.task(c['id'], {**body, 'confirmed': True})
        self.assertEqual(calculate(c), before)
    def test_missing_revenue_is_not_lowest_tier(self):
        c = self.store.get('northstar')
        c = self.store.manual_review(c['id'], {'revision': c['revision'], 'kind': 'revenue', 'revenue': None, 'reason': 'Scope needs an estimate.'})
        r = calculate(c)
        self.assertIsNone(r['rt'])
        self.assertEqual(r['priority'], 'Pending confirmation')
        self.assertAlmostEqual(r['pu'] - r['pl'], 40)
    def test_80_percent_coverage_does_not_bypass_missing_authority(self):
        r = calculate(self.store.get('meridian'))
        self.assertEqual((r['coverage'], r['ql'], r['qu'], r['readiness']), (80, 60, 80, 'More evidence needed'))
    def test_generated_drafts_persist_without_auto_creating_tasks(self):
        c = self.store.get('meridian')
        self.store.generate(c['id'], 'outreach', {'revision': c['revision']})
        out = self.store.generate(c['id'], 'actions', {'revision': c['revision']})
        latest = self.store.get(c['id'])
        self.assertIn('draft', latest['outreach'])
        self.assertLessEqual(len(out['actions']), 3)
        self.assertEqual(latest['tasks'], c['tasks'])

    def test_reanalysis_supersedes_old_pending_proposals(self):
        run = self.ingest('Our project budget is approved and covers the initial scope.')
        c = self.store.get('cedar')
        replacement = self.store.ingest('cedar', {'revision': c['revision'], 'sourceId': run['sourceId'], 'requestId': 'replacement'})
        self.assertEqual(self.store.get_run(run['id'])['proposals'][0]['status'], 'Superseded')
        self.review(replacement)
        c = self.store.get('cedar')
        self.store.approval('cedar', {'revision': c['revision'], 'operation': 'submit'})
    def test_pending_suggestions_prevent_standard_submission(self):
        run = self.ingest('We shared the requested process maps.', 'northstar')
        c = self.store.get('northstar')
        with self.assertRaises(ValueError): self.store.approval(c['id'], {'revision': c['revision'], 'operation': 'submit'})
    def test_failed_source_can_be_manually_reviewed_before_submission(self):
        with patch.dict('os.environ', {}, clear=True):
            self.store.config({'mode': 'live'})
            run = self.ingest('Additional non-material background.', 'northstar')
        c = self.store.get('northstar')
        with self.assertRaises(ValueError): self.store.approval(c['id'], {'revision': c['revision'], 'operation': 'submit'})
        c = self.store.manual_review(c['id'], {'revision': c['revision'], 'kind': 'source', 'sourceId': run['sourceId'], 'confirmed': True, 'reason': 'Read original; no assessment changes required.'})
        self.assertEqual(self.store.approval(c['id'], {'revision': c['revision'], 'operation': 'submit'})['approval']['status'], 'Submitted')
    def test_future_promises_and_conditional_demo_claims_not_completed_facts(self):
        for i, text in enumerate(('We will share the requested process maps.', 'If our budget is approved and covers the initial scope, we will proceed.', 'This is not our first project together.')):
            run = self.ingest(text, request_id='negative-' + str(i))
            self.assertEqual(run['proposals'], [])
    def test_human_edited_draft_is_saved_without_model_call(self):
        c = self.store.get('meridian')
        self.store.generate(c['id'], 'outreach', {'revision': c['revision']})
        self.store.generate(c['id'], 'outreach', {'revision': c['revision'], 'operation': 'save', 'subject': 'Edited subject', 'draft': 'Edited message.'})
        self.assertEqual(self.store.get(c['id'])['outreach']['draft'], 'Edited message.')

    def test_problem_summary_changes_do_not_auto_confirm_need_or_fit(self):
        c = self.store.get('summit')
        c = self.store.manual_review(c['id'], {'revision': c['revision'], 'kind': 'problem', 'problem': 'Manual processes delay monthly reporting.', 'reason': 'Original meeting notes reviewed.'})
        self.assertEqual(c['problem'], 'Manual processes delay monthly reporting.')
        self.assertIsNone(c['scores']['N'])
        self.assertIsNone(c['conditions']['fit'])

    def test_reassessment_after_an_approved_case_preserves_old_decision(self):
        c = self.store.get('northstar')
        c = self.store.approval(c['id'], {'revision': c['revision'], 'operation': 'submit'})
        c = self.store.approval(c['id'], {'revision': c['revision'], 'operation': 'approve'})
        self.assertFalse(c['approval'].get('outdated', False))
        c = self.store.manual_review(c['id'], {'revision': c['revision'], 'kind': 'score', 'dimension': 'T', 'score': 5, 'reason': 'Decision plan newly confirmed.'})
        self.assertTrue(c['approval']['outdated'])
        c = self.store.approval(c['id'], {'revision': c['revision'], 'operation': 'submit'})
        self.assertEqual(c['approval']['status'], 'Submitted')
        self.assertEqual(c['approvalHistory'][0]['status'], 'Approved')
        self.assertNotIn('approvalHistory', c['approval']['snapshot']['case'])

class ProviderContractTests(unittest.TestCase):
    def response(self, value):
        return {'id': 'resp_test', 'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(value)}]}]}, 'req_test'
    def test_live_two_role_pipeline_and_request_trace(self):
        calls = []
        def transport(payload):
            calls.append(payload)
            if len(calls) == 1: return self.response({'findings': [{'id': 'f1', 'quote': 'The budget is approved.', 'fact': 'Approved funding.'}]})
            return self.response({'summary': 'Review budget evidence.', 'proposals': [{'finding_id': 'f1', 'target': 'B', 'score': 3, 'condition_value': None, 'blocker_code': None, 'explanation': 'Scope coverage remains unknown.', 'question': 'Does it cover consulting scope?', 'conflict': False}]})
        agent = Agents('live', transport)
        result = agent.analyze({'text': 'The budget is approved.'}, {'scores': {}})
        self.assertEqual(len(calls), 2)
        self.assertEqual([x['role'] for x in result['calls']], ['A2_evidence_extraction', 'A3_qualification_advisory'])
        self.assertEqual(result['calls'][0]['requestId'], 'req_test')
        self.assertEqual(calls[0]['text']['format']['type'], 'json_schema')
        self.assertTrue(calls[0]['text']['format']['strict'])
        self.assertFalse(calls[0]['store'])
    def test_fabricated_quote_stops_before_advisory(self):
        agent = Agents('live', lambda payload: self.response({'findings': [{'id': 'f1', 'quote': 'Invented quotation', 'fact': 'Fake'}]}))
        with self.assertRaises(ProviderError): agent.analyze({'text': 'Actual words.'}, {})
        self.assertEqual(len(agent.calls), 1)
    def test_invalid_reference_and_score_are_rejected(self):
        p = {'finding_id': 'wrong', 'target': 'B', 'score': 4, 'condition_value': None, 'blocker_code': None, 'explanation': 'Test', 'question': '', 'conflict': False}
        with self.assertRaises(ProviderError): validate_proposals([p], [{'id': 'f1'}])
        p.update(finding_id='f1', score=9)
        with self.assertRaises(ProviderError): validate_proposals([p], [{'id': 'f1'}])
    def test_refusal_and_incomplete_are_not_success(self):
        for response in ({'status': 'incomplete'}, {'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'refusal', 'refusal': 'No'}]}]}):
            with self.assertRaises(ProviderError): Agents('live', lambda payload: (response, 'req_test')).call('test', 'test', {}, {})
    def test_agent_cannot_resolve_evidence_conflicts(self):
        p = {'finding_id': 'f1', 'target': 'condition:conflict', 'score': None, 'condition_value': True, 'blocker_code': None, 'explanation': 'Test', 'question': '', 'conflict': False}
        with self.assertRaises(ProviderError): validate_proposals([p], [{'id': 'f1'}])

class HttpBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.server = make_server(0, Path(self.tmp.name) / 'http.sqlite3')
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = 'http://127.0.0.1:' + str(self.server.server_port)
    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.server.store.db.close()
        self.tmp.cleanup()
    def request(self, path, data=None, headers=None):
        req = urllib.request.Request(self.base + path, data=json.dumps(data).encode() if data is not None else None, headers=headers or {})
        try:
            with urllib.request.urlopen(req) as res: return res.status, json.load(res)
        except urllib.error.HTTPError as e: return e.code, json.load(e)
    def test_no_key_in_bootstrap_and_csrf_required(self):
        status, data = self.request('/api/bootstrap')
        self.assertEqual(status, 200)
        self.assertNotIn('OPENAI_API_KEY', json.dumps(data))
        self.assertEqual(self.request('/api/config', {'mode': 'live'})[0], 403)
        self.assertEqual(self.request('/api/config', {'mode': 'demo'}, {'X-CSRF-Token': data['csrf']})[0], 200)
    def test_cross_origin_and_nonlocal_host_rejected(self):
        _, data = self.request('/api/bootstrap')
        self.assertEqual(self.request('/api/config', {'mode': 'live'}, {'X-CSRF-Token': data['csrf'], 'Origin': 'https://untrusted.example'})[0], 403)
        self.assertEqual(self.request('/api/bootstrap', headers={'Host': 'untrusted.example'})[0], 403)
    def test_server_files_are_not_static_assets(self):
        for path in ('/.env', '/server.py', '/runtime/cgs.sqlite3'):
            self.assertEqual(self.request(path)[0], 404)

if __name__ == '__main__': unittest.main()
