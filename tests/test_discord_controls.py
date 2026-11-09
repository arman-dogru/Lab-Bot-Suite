import importlib.util
import asyncio
import unittest
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

spec = importlib.util.spec_from_file_location('jetson_controls_test',
    Path(__file__).resolve().parents[1] / 'deploy/control_plugin/__init__.py')
controls = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controls)


class StatusRoutingTests(unittest.TestCase):
    def test_goal_start_ids_are_bounded_private_and_persisted(self):
        with tempfile.TemporaryDirectory() as home,patch.dict('os.environ',{'HERMES_HOME':home}):
            source=SimpleNamespace(chat_id='thread')
            for index in range(70):
                controls._remember_goal_start(SimpleNamespace(source=source,message_id=str(index),text='PRIVATE objective'))
            records=controls._goal_starts()
            self.assertEqual(len(records),64)
            self.assertIn('thread:69',records)
            self.assertNotIn('thread:0',records)
            self.assertNotIn('PRIVATE',controls._goal_starts_path().read_text())
            self.assertEqual(controls._goal_starts_path().stat().st_mode & 0o777,0o600)

    def test_status_channel_text_uses_local_controls(self):
        with patch.dict('os.environ', {'DISCORD_STATUS_CHANNEL': '123'}):
            with patch.object(controls, 'handle', return_value='Local status') as handle:
                result = controls.status_channel(platform='discord', source={'chat_id':'123'}, text='how is it going?')
                self.assertEqual(result, {'action':'handled', 'reply':'Local status'})
                handle.assert_called_once_with('status')

    def test_paid_chat_and_other_platforms_are_not_consumed(self):
        with patch.dict('os.environ', {'DISCORD_STATUS_CHANNEL': '123'}):
            self.assertIsNone(controls.status_channel(platform='discord', source={'chat_id':'456'}, text='code this'))
            self.assertIsNone(controls.status_channel(platform='other', source={'chat_id':'123'}, text='status'))

    def test_evidence_failure_breaker_preserves_productive_goals(self):
        records={}
        guard=controls.GoalEvidenceGuard(records)
        reason='The agent only claims success without showing command output or file contents.'
        self.assertFalse(guard.check('session','first goal',reason))
        self.assertFalse(guard.check('session','first goal',reason))
        self.assertTrue(guard.check('session','first goal',reason))
        restarted=controls.GoalEvidenceGuard(records)
        self.assertTrue(restarted.check('session','first goal',reason))
        self.assertFalse(restarted.check('session','a new goal',reason))
        for _ in range(10):
            self.assertFalse(restarted.check('session','a new goal','Processing the next input file.'))

    def test_goal_notices_are_quiet_and_routed_to_status(self):
        calls=[]
        async def send(channel,message,metadata=None):
            calls.append((channel,message))
        async def original(source,message):
            calls.append(('original',message))
        gateway=SimpleNamespace(_send_goal_status_notice=original,
            _goal_notice_adapter=lambda source:SimpleNamespace(send=send))
        source=SimpleNamespace(platform=SimpleNamespace(value='discord'),chat_id='456')
        loop=asyncio.new_event_loop()
        try:
            with patch.dict('os.environ',{'DISCORD_STATUS_CHANNEL':'123'}):
                controls._goal_notice_route(gateway)
                loop.run_until_complete(gateway._send_goal_status_notice(source,'↻ Continuing toward goal'))
                loop.run_until_complete(gateway._send_goal_status_notice(source,'✓ Goal achieved'))
            self.assertEqual(calls,[('123','✓ Goal completed. The final result is in its task conversation.')])
        finally:
            loop.close()

    def test_only_automatic_goal_progress_is_suppressed_after_judging(self):
        from unittest.mock import MagicMock
        state=SimpleNamespace(status='active',last_verdict='continue')
        async def original(event):
            return 'Observed result or progress'
        async def lookup(key):
            return SimpleNamespace(session_id='session')
        gateway=SimpleNamespace(_handle_message=original,
            _turn_failed=lambda:False,_session_key_for_source=lambda source:'key',
            async_session_store=SimpleNamespace(lookup_by_session_key=lookup))
        source=SimpleNamespace(platform=SimpleNamespace(value='discord'),user_id='owner',chat_id='thread')
        event=SimpleNamespace(source=source,text='Initial automatic goal step',inherit_channel_pin=True,message_id=None)
        fake_goals=SimpleNamespace(GoalManager=MagicMock(return_value=SimpleNamespace(state=state)))
        loop=asyncio.new_event_loop()
        try:
            with tempfile.TemporaryDirectory() as home,patch.dict('os.environ',{'DISCORD_ALLOWED_USERS':'owner','HERMES_HOME':home}),patch.dict(sys.modules,{'hermes_cli.goals':fake_goals}):
                controls._goal_delivery_route(gateway,source)
                self.assertIs(gateway._handle_message.__self__,gateway)
                self.assertIsNone(loop.run_until_complete(gateway._handle_message(event)))
                event.message_id='actual-discord-message'
                self.assertEqual(loop.run_until_complete(gateway._handle_message(event)),'Observed result or progress')
                controls._remember_goal_start(event)
                # Native kickoff retains the initiating command's message ID.
                self.assertIsNone(loop.run_until_complete(gateway._handle_message(event)))
                event.edit_revision='owner-edit'
                self.assertEqual(loop.run_until_complete(gateway._handle_message(event)),'Observed result or progress')
                event.edit_revision=None
                event.message_id=None
                # Native kickoff explicitly has inherit_channel_pin=False;
                # subsequent continuation events have True. Neither has a message ID.
                event.inherit_channel_pin=False
                self.assertIsNone(loop.run_until_complete(gateway._handle_message(event)))
                event.inherit_channel_pin=True
                for status,verdict in [('done','done'),('paused','continue'),('active','wait')]:
                    state.status,state.last_verdict=status,verdict
                    self.assertEqual(loop.run_until_complete(gateway._handle_message(event)),'Observed result or progress')
                state.status,state.last_verdict='active','continue'
                event.text='/goal status'
                self.assertEqual(loop.run_until_complete(gateway._handle_message(event)),'Observed result or progress')
                event.text='[Continuing toward your standing goal] next step'
                gateway._turn_failed=lambda:True
                self.assertEqual(loop.run_until_complete(gateway._handle_message(event)),'Observed result or progress')
                gateway._turn_failed=lambda:False
                source.user_id='unapproved'
                self.assertEqual(loop.run_until_complete(gateway._handle_message(event)),'Observed result or progress')
        finally:
            loop.close()


if __name__ == '__main__':
    unittest.main()
