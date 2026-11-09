import importlib.util
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "jetson_policy", Path(__file__).resolve().parents[1] / "deploy/control_plugin/policy.py")
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


class PolicyTests(unittest.TestCase):
    def test_purchases_are_denied_in_every_mode(self):
        command = "curl https://checkout.stripe.com/pay"
        for mode in policy.MODES:
            self.assertEqual(policy.classify(command, mode), "deny")
            self.assertEqual(policy.classify("cat /etc/jetson-bot/xai-key", mode), "deny")
            self.assertEqual(policy.classify("cat /etc/jetson-bot/xai-key", mode, interactive=False), "deny")

    def test_workspace_test_is_allowed_and_push_is_held(self):
        test = "python -m unittest discover -s /workspace/project -q"
        push = "git push origin main"
        self.assertEqual(policy.classify(test, "auto"), "allow")
        self.assertEqual(policy.classify(push, "auto"), "prompt")
        self.assertEqual(policy.classify(push, "manual"), "prompt")
        self.assertEqual(policy.classify(push, "all"), "allow")
        self.assertEqual(policy.classify(push, "auto", interactive=False), "deny")
        self.assertEqual(policy.classify(push, "manual", interactive=False), "deny")

    def test_tool_directive_blocks_without_a_tty(self):
        self.assertIsNone(policy.decide_tool(
            "terminal", {"command": "python -m unittest discover -s /workspace/project"},
            mode="auto", interactive=False))
        held = policy.decide_tool(
            "terminal", {"command": "git push origin main"}, mode="manual", interactive=False)
        self.assertEqual(held["action"], "block")
        purchase = policy.decide_tool(
            "terminal", {"command": "curl https://checkout.stripe.com/pay"}, mode="all")
        self.assertEqual(purchase["action"], "block")

    def test_review_command_persists_without_a_model(self):
        with tempfile.TemporaryDirectory() as home:
            self.assertIn("No model call", policy.review_command([], home))
            self.assertEqual(policy.current_mode(home), "all")
            self.assertIn("auto", policy.review_command(["auto"], home))
            self.assertEqual(policy.current_mode(home), "auto")
            self.assertIn("manual", policy.review_command(["manual"], home))
            self.assertEqual(policy.current_mode(home), "manual")
            self.assertIn("all", policy.review_command(["all"], home))
            self.assertEqual(policy.current_mode(home), "all")
            self.assertIn("all | auto | manual", policy.review_command(["yolo"], home))
