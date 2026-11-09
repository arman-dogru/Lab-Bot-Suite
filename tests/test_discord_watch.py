import io
import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError

from jetson_bot.discord_watch import Discord, Tracker, activity_text, read_new_lines, save_json


def log(logger, body):
    return '2026-10-30 19:20:00,100 INFO ' + logger + ': ' + body


class ActivityTests(unittest.TestCase):
    def test_budget_resume_reuses_the_paused_task_card(self):
        tracker=Tracker({},clock=lambda:100)
        tracker.consume(log('gateway.run','inbound message: platform=discord chat=123 msg=task'))
        tracker.consume(log('agent.conversation_loop','API call failed summary=HTTP 402: Paid work is paused by the operator.'))
        tracker.consume(log('gateway.run','response ready: platform=discord chat=123 time=1.0s api_calls=1'))
        self.assertEqual(tracker.current['status'],'Budget paused')
        nonce=tracker.current['nonce']
        tracker.consume(log('gateway.run',"inbound message: platform=discord chat=123 msg='[Jetson budget resume token]'"))
        tracker.consume(log('gateway.run','response ready: platform=discord chat=123 time=2.0s api_calls=2'))
        self.assertEqual(len(tracker.state['tasks']),1)
        self.assertEqual(tracker.current['nonce'],nonce)
        self.assertEqual(tracker.current['status'],'Finished')
        self.assertEqual(tracker.current['calls'],3)

    def test_status_controls_do_not_create_task_cards_or_interrupt_real_work(self):
        tracker=Tracker({},clock=lambda:100,ignored_chat_ids=('111','222'))
        tracker.consume(log('gateway.run','inbound message: platform=discord chat=111 msg=status'))
        self.assertIsNone(tracker.current)
        tracker.consume(log('gateway.run','inbound message: platform=discord chat=333 msg=real task'))
        tracker.consume(log('gateway.run','inbound message: platform=discord chat=111 msg=budget'))
        tracker.consume(log('gateway.run','inbound message: platform=discord chat=222 msg=ignored'))
        self.assertEqual(len(tracker.state['tasks']),1)
        self.assertEqual(tracker.current['status'],'Running')

    def test_only_runtime_facts_are_forwarded(self):
        state = {}
        tracker = Tracker(state, clock=lambda: 100)
        tracker.consume(log('gateway.run', "inbound message: platform=discord user=Owner chat=123 msg='PRIVATE-PROMPT'"))
        tracker.consume(log('agent.tool_executor', 'tool terminal completed (0.10s, 40 chars) PRIVATE-OUTPUT'))
        tracker.consume(log('agent.tool_executor', 'Tool terminal returned error (0.10s): PRIVATE-ERROR'))
        tracker.consume(log('agent.conversation_loop', 'API call #1: model=grok in=20 out=9 latency=1s PRIVATE-REASONING'))
        tracker.consume(log('gateway.run', 'response ready: platform=discord chat=123 session=x time=2.5s api_calls=1 response=20 chars'))
        text = activity_text(tracker.current, '456')
        self.assertEqual(tracker.current['tools'], {'terminal': 2})
        self.assertEqual(tracker.current['failures'],1)
        self.assertEqual(tracker.current['status'], 'Finished')
        self.assertIn('Main model calls: 1; output tokens: 9', text)
        self.assertNotIn('PRIVATE', text + json.dumps(state))

    def test_log_content_cannot_spoof_a_different_event_type(self):
        tracker = Tracker({}, clock=lambda: 100)
        tracker.consume(log('gateway.run', 'inbound message: platform=discord chat=123 msg=hello'))
        tracker.consume(log('gateway.platforms.base', 'model says: agent.tool_executor: tool terminal completed (1s)'))
        tracker.consume('API call #99: out=9000')
        self.assertEqual(tracker.current['tools'], {})
        self.assertEqual(tracker.current['calls'], 0)
        tracker.consume(log('gateway.run', 'Stopping gateway...'))
        self.assertEqual(tracker.current['status'], 'Interrupted')

    def test_goal_continuations_share_one_card_and_count_requests_per_run(self):
        tracker=Tracker({},clock=lambda:100)
        tracker.consume(log('gateway.run','inbound message: platform=discord chat=123 msg=initial goal'))
        tracker.consume(log('agent.conversation_loop','API call #7: model=grok in=20 out=9'))
        tracker.consume(log('gateway.run','response ready: platform=discord chat=123 time=2.0s api_calls=1'))
        tracker.consume(log('hermes_cli.goals','goal judge: verdict=continue reason=next step'))
        tracker.consume(log('gateway.run',"inbound message: platform=discord chat=123 msg='[Continuing toward your standing goal] next step'"))
        tracker.consume(log('agent.conversation_loop','API call #8: model=grok in=20 out=3'))
        tracker.consume(log('gateway.run','response ready: platform=discord chat=123 time=1.0s api_calls=1'))
        tracker.consume(log('hermes_cli.goals','goal judge: verdict=done reason=verified'))
        self.assertEqual(len(tracker.state['tasks']),1)
        self.assertEqual(tracker.current['calls'],2)
        self.assertEqual(tracker.current['output_tokens'],12)
        self.assertEqual(tracker.current['status'],'Finished')

    def test_dashboard_edits_coalesce_and_survive_restart(self):
        calls, cards = [], {}
        now = [100]
        def request(method, path, payload):
            calls.append((method, path, payload))
            return {'id': '789'}
        client = Discord('not-a-real-token', cards, request=request, clock=lambda: now[0])
        self.assertTrue(client.upsert('status', '123', 'Online'))
        self.assertFalse(client.upsert('status', '123', 'Online'))
        now[0] += 5
        self.assertFalse(client.upsert('status', '123', 'Working'))
        now[0] += 15
        self.assertTrue(client.upsert('status', '123', 'Working'))
        restarted = Discord('not-a-real-token', json.loads(json.dumps(cards)), request=request, clock=lambda: now[0])
        self.assertFalse(restarted.upsert('status', '123', 'Working'))
        self.assertEqual([call[0] for call in calls], ['POST', 'PATCH'])
        self.assertTrue(calls[0][2]['enforce_nonce'])
        self.assertEqual(calls[0][2]['allowed_mentions'], {'parse': []})
        self.assertNotIn('flags', calls[1][2])

    def test_discord_rate_limits_do_not_create_retry_spam(self):
        calls, now = [], [100]
        def request(*args):
            calls.append(args)
            raise HTTPError('https://discord.com', 429, 'rate limited', {}, io.BytesIO(b'{"retry_after": 45}'))
        client = Discord('not-a-real-token', {}, request=request, clock=lambda: now[0])
        self.assertFalse(client.upsert('status', '123', 'Online'))
        now[0] += 20
        self.assertFalse(client.upsert('status', '123', 'Online'))
        self.assertEqual(len(calls), 1)

    def test_log_cursor_handles_partial_writes_rotation_and_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path, saved = Path(folder) / 'agent.log', Path(folder) / 'state.json'
            path.write_text('old history\n')
            state = {}
            self.assertEqual(read_new_lines(path, state), [])
            with path.open('a') as stream:
                stream.write('new event\npartial')
            self.assertEqual(read_new_lines(path, state), ['new event'])
            save_json(saved, state)
            state = json.loads(saved.read_text())
            with path.open('a') as stream:
                stream.write(' event\n')
            self.assertEqual(read_new_lines(path, state), ['partial event'])
            path.rename(path.with_suffix('.old'))
            path.write_text('rotated event\n')
            self.assertEqual(read_new_lines(path, state), ['rotated event'])


if __name__ == '__main__':
    unittest.main()
