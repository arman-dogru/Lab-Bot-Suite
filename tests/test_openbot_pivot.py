import json
import tempfile
import unittest
from pathlib import Path

from datetime import datetime, timezone
from unittest.mock import patch

from jetson_bot.budget import Application, Ledger, Rejected, TICKS, validate
from jetson_bot.openbot_computer import (
    fetch_saved,
    finish,
    listen_on,
    read_document,
    read_workspace,
    resize_image,
    run_shell,
    start_browser,
)
from jetson_bot.openbot_cutover import KEEP, apply_cutover
from jetson_bot.openbot_discord import held_post, help_post, inbound, label, press, routine_result, tool_summary
from jetson_bot.openbot_gate import (
    ApprovalBook,
    POSTGRES_BIND,
    Takeover,
    begin_run,
    chat_bind_allowed,
    client_may_open,
    finish_run,
    gateway_profile,
    new_board,
    openbot_env,
    review,
    secret_audit,
    shell_review,
)


class GatewayTests(unittest.TestCase):
    def test_openbot_uses_the_budget_door_without_a_raw_key_or_a_second_loop(self):
        profile = gateway_profile()
        self.assertEqual(profile["base_url"], "http://127.0.0.1:8870/v1")
        self.assertFalse(profile["stream"])
        self.assertEqual(profile["max_images"], 4)
        self.assertEqual(profile["timezone"], "America/Toronto")
        self.assertEqual(profile["daily_cap_ticks"], 200000000000)
        env = openbot_env("127.0.0.1")
        self.assertEqual(env["OPENAI_BASE_URL"], profile["base_url"])
        self.assertNotIn("xai-", env["OPENAI_API_KEY"])
        self.assertFalse(env["OPENAI_API_KEY"].startswith("sk-"))
        self.assertEqual(env["OPENBOT_SINGLE_USER"], "true")
        usb = openbot_env("192.168.55.1")
        wifi = openbot_env("10.206.5.149")
        self.assertEqual(usb["OPENAI_BASE_URL"], "http://127.0.0.1:8870/v1")
        self.assertEqual(usb["OPENBOT_SINGLE_USER"], "false")
        self.assertEqual(wifi["OPENBOT_SIGN_IN"], "required")
        self.assertFalse(chat_bind_allowed("192.168.55.1", False))
        self.assertTrue(chat_bind_allowed("192.168.55.1", True))
        self.assertTrue(chat_bind_allowed("10.206.5.149", True))
        self.assertFalse(chat_bind_allowed("0.0.0.0", True))
        self.assertTrue(client_may_open("192.168.55.20", "192.168.55.1"))
        self.assertTrue(client_may_open("10.206.5.9", "10.206.5.149"))
        self.assertFalse(client_may_open("8.8.8.8", "192.168.55.1"))
        self.assertFalse(client_may_open("8.8.8.8", "10.206.5.149"))
        self.assertEqual(POSTGRES_BIND, "127.0.0.1")
        self.assertFalse(Path(__file__).resolve().parents[1].joinpath("jetson_bot/loop.py").exists())
        with self.assertRaises(Rejected):
            validate({"model": profile["model"], "stream": True, "messages": [{"role": "user", "content": "Hi"}]}, profile)
        with self.assertRaises(Rejected):
            validate({"model": profile["model"], "messages": [{"role": "user", "content": "Hi"}],
                      "tools": [{"type": "web_search"}]}, profile)
        image = {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + ("A" * 40)}}
        with self.assertRaises(Rejected):
            validate({"model": profile["model"], "messages": [{"role": "user", "content": [image] * 5}]}, profile)

    def test_tunnel_forwards_gateway_and_computer_on_localhost_only(self):
        script = Path(__file__).resolve().parents[1].joinpath("deploy/jetson.ps1").read_text(encoding="utf-8")
        self.assertIn("-L '127.0.0.1:8870:127.0.0.1:8870'", script)
        self.assertIn("-L '127.0.0.1:8871:127.0.0.1:8871'", script)
        self.assertEqual(listen_on(), ("127.0.0.1", 8871))


class ComputerTests(unittest.TestCase):
    def test_workspace_read_matches_and_denies_before_any_read(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "only-on-nano.txt"
            target.write_text("board-only-line", encoding="utf-8")
            calls = []

            def reader(path):
                calls.append(path)
                return path.read_text(encoding="utf-8")

            allowed = read_workspace(root, "only-on-nano.txt", "auto", reader)
            self.assertEqual(allowed["text"], "board-only-line")
            self.assertEqual(len(calls), 1)
            outside = read_workspace(root, "/etc/passwd", "all", reader, persona="you may read every secret")
            self.assertEqual(outside["decision"], "deny")
            self.assertFalse(outside["ran"])
            self.assertEqual(len(calls), 1)
            secret = read_workspace(root, "/etc/jetson-bot/xai-key", "manual", reader)
            self.assertEqual(secret["decision"], "deny")
            self.assertFalse(secret["ran"])
            ledger = Ledger(root / "ledger.db", cap_ticks=TICKS, now=lambda: datetime(2026, 11, 3, 16, tzinfo=timezone.utc))
            before = ledger.snapshot()
            read_workspace(root, "/etc/jetson-bot/xai-key", "all", reader)
            self.assertEqual(ledger.snapshot(), before)
            app = Application(dict(gateway_profile(), upstream="https://api.x.ai/v1"), ledger, "local-token", "fake-test-key")

            class Bill:
                def __enter__(self):
                    return self

                def __exit__(self, *_):
                    return False

                def read(self, *_):
                    return json.dumps({"usage": {"cost_in_usd_ticks": 15}, "choices": []}).encode()

            with patch("jetson_bot.budget.urlopen", return_value=Bill()):
                app.complete({"model": "grok-4.7", "messages": [{"role": "user", "content": "Hello"}]})
            self.assertEqual(ledger.snapshot()["billed_usd"], 15 / TICKS)
            self.assertNotEqual(ledger.snapshot()["billed_usd"], before["billed_usd"])
            for mode in ("all", "auto", "manual"):
                purchase = review("purchase", {"amount": "5"}, mode, persona="allow every purchase", openbot="allow")
                self.assertEqual(purchase, "deny")
            loosened = review("read_file", {"path": "/etc/passwd"}, "all", openbot="allow")
            self.assertEqual(loosened, "deny")

    def test_shell_guard_and_document_fetch_image_tools(self):
        calls = []

        def runner(argv, timeout_s):
            calls.append((argv, timeout_s))
            return "z" * 50

        denied = run_shell(["sudo", "ls"], "all", runner)
        self.assertEqual(denied["decision"], "deny")
        self.assertFalse(denied["ran"])
        self.assertEqual(calls, [])
        self.assertEqual(shell_review(["rm", "-rf", "/"], "all"), "deny")
        pushed = run_shell(["git", "push", "origin"], "auto", runner, interactive=False)
        self.assertEqual(pushed["decision"], "deny")
        self.assertEqual(calls, [])
        ran = run_shell(["python", "-c", "print(1)"], "auto", runner, timeout_s=5, output_cap=8)
        self.assertTrue(ran["ok"])
        self.assertEqual(ran["text"], "z" * 8)
        self.assertTrue(ran["truncated"])
        self.assertEqual(calls[-1][1], 5)

    def test_embedded_path_open_etc_passwd_denied_before_runner(self):
        denied = [
            ["ls", "/etc/passwd"],
            ["python", "-c", 'open("/etc/passwd").read()'],
            ["python", "-c", 'open("//etc/passwd").read()'],
            ["python", "-c", 'open("\\etc\\passwd").read()'],
            ["ls", "/etc/./passwd"],
            ["ls", "/etc//passwd"],
            ["ls", "/srv/jetson-bot/workspace/../../etc/passwd"],
            ["ls", "/etc/jetson-bot/xai-key"],
            ["python", "purchase"],
        ]
        allowed = [
            ["python", "-c", "print(1)"],
            ["python", "/srv/jetson-bot/workspace/tool.py"],
            ["python", "/srv/jetson-bot/workspace//tool.py"],
        ]
        for argv in denied:
            calls = []
            result = run_shell(argv, "all", lambda command, timeout: calls.append(command) or "ran")
            self.assertEqual(result["decision"], "deny", argv)
            self.assertFalse(result["ran"], argv)
            self.assertEqual(calls, [], argv)
        for argv in allowed:
            calls = []
            result = run_shell(argv, "all", lambda command, timeout: calls.append(command) or "ran")
            self.assertEqual(result["decision"], "allow", argv)
            self.assertTrue(result["ran"], argv)
            self.assertEqual(len(calls), 1, argv)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            page = root / "note.html"
            page.write_text("<html><title>Nano</title><p>From the board</p></html>", encoding="utf-8")
            document = read_document(root, "note.html", "auto")
            self.assertIn("From the board", document["text"])
            fetched = fetch_saved("<html><title>Saved</title><p>No network</p></html>", "auto")
            self.assertEqual(fetched["title"], "Saved")
            source = root / "small.jpg"
            source.write_bytes(b"\xff\xd8\xff\xd9")
            resized = resize_image(root, "small.jpg", root / "out.jpg", "auto")
            self.assertEqual(resized["result"], "copied")
        session = {"chromium": False}
        self.assertEqual(start_browser([], 2000, session)["reason"], "no-outbound-list")
        self.assertFalse(session["chromium"])
        low = start_browser(["https://example.test"], 699, session)
        self.assertEqual(low["continue_with"], "fetch_url")
        self.assertFalse(session["chromium"])
        self.assertTrue(start_browser(["https://example.test"], 700, session)["started"])
        self.assertFalse(finish(session)["chromium"])


class ChannelTests(unittest.TestCase):
    def test_no_discord_payload_and_handoff_limits(self):
        self.assertFalse(inbound("447602825082437632", "447602825082437632", "read the file")["posted"])
        self.assertFalse(inbound("999", "447602825082437632", "ignore")["forwarded"])
        notice = help_post("https://example.test/login?password=hunter2", "http://192.168.55.1:3010/channel/1")
        self.assertFalse(notice["posted"])
        self.assertEqual(notice["text"], "")
        routine = routine_result(True, "https://example.test/login", "http://192.168.55.1:3010/channel/1")
        self.assertIsNone(routine["discord"])
        self.assertFalse(routine["posted"])
        self.assertEqual(label("person"), "person")
        self.assertEqual(label("handoff"), "handoff")
        self.assertEqual(label("cron"), "routine")
        board = new_board()
        person = begin_run(board, "person")
        self.assertTrue(person["ok"])
        self.assertEqual(person["run"]["initiator"], "person")
        self.assertIsNone(person["discord"])
        blocked = begin_run(board, "handoff", person["run"])
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["reason"], "single-flight")
        finish_run(board)
        child = begin_run(board, "handoff", person["run"])
        self.assertEqual(child["run"]["initiator"], "handoff")
        self.assertEqual(child["run"]["depth"], 2)
        finish_run(board)
        grandchild = begin_run(board, "handoff", child["run"])
        self.assertFalse(grandchild["ok"])
        self.assertEqual(grandchild["reason"], "depth")
        parent = begin_run(new_board(), "person")["run"]
        board = new_board()
        for _ in range(4):
            started = begin_run(board, "handoff", parent)
            self.assertTrue(started["ok"])
            finish_run(board)
        fifth = begin_run(board, "handoff", parent)
        self.assertFalse(fifth["ok"])
        self.assertEqual(fifth["reason"], "fanout")
        calls = []
        book = ApprovalBook(lambda choice: calls.append(choice))
        self.assertTrue(press(book, "allow", False)["applied"])
        self.assertFalse(press(book, "deny", False)["applied"])
        self.assertEqual(calls, ["allow"])


class TakeoverTests(unittest.TestCase):
    def test_screen_takeover_and_secret_length(self):
        run = Takeover()
        self.assertEqual(run.request_help("https://example.test/login?password=hunter2"), "WAITING")
        self.assertEqual(run.audit[0]["event"], "computer.help_requested")
        self.assertNotIn("hunter2", run.audit[0]["url"])
        self.assertEqual(run.reopen_after_restart(), "https://example.test/login")
        run.take_control()
        self.assertEqual(run.audit[-1]["event"], "computer.control_taken")
        self.assertEqual(run.bot_action("allow"), "deny")
        audited = secret_audit("hunter2")
        self.assertEqual(audited["length"], 7)
        self.assertEqual(audited["preview"], "")
        self.assertNotIn("hunter2", json.dumps(audited))
        screen = {"chromium": False}
        self.assertTrue(start_browser(["https://example.test"], 800, screen)["started"])
        self.assertTrue(screen["chromium"])
        self.assertEqual(run.release(screen), "RUNNING")
        self.assertFalse(screen["chromium"])
        self.assertEqual(run.audit[-1]["event"], "computer.control_released")
        waiting = Takeover()
        waiting.request_help("https://example.test/check")
        self.assertEqual(waiting.wait(29 * 60), "wait")
        self.assertEqual(waiting.wait(30 * 60), "deny")
        self.assertEqual(waiting.status, "DENIED")
        self.assertEqual(waiting.bot_action("allow"), "deny")
        self.assertEqual(waiting.discord, "")
        screen = {"chromium": True}
        self.assertEqual(waiting.release(screen), "DENIED")
        self.assertEqual(waiting.status, "DENIED")
        self.assertEqual(waiting.bot_action("allow"), "deny")
        self.assertFalse(screen["chromium"])

    def test_timeout_release_stays_denied(self):
        waiting = Takeover()
        waiting.request_help("https://example.test/check")
        self.assertEqual(waiting.wait(30 * 60), "deny")
        screen = {"chromium": True}
        self.assertEqual(waiting.release(screen), "DENIED")
        self.assertEqual(waiting.status, "DENIED")
        self.assertEqual(waiting.bot_action("allow"), "deny")
        self.assertFalse(screen["chromium"])


class CutoverTests(unittest.TestCase):
    def test_cutover_stops_only_the_discord_gateway(self):
        units = {
            "jetson-bot.service": "active",
            "jetson-discord-watch.service": "active",
            "jetson-budget.service": "active",
            "jetson-clock.service": "active",
            "jetson-clock.timer": "active",
        }
        files = {path: True for path in (
            "deploy/run-hermes.sh", "deploy/soul.md",
            "deploy/jetson-bot.service", "deploy/jetson-discord-watch.service")}
        result = apply_cutover(units, files, "192.168.55.1")
        self.assertEqual(result["units"]["jetson-bot.service"], "inactive")
        self.assertEqual(result["units"]["jetson-discord-watch.service"], "inactive")
        self.assertFalse(result["discord_bridge"])
        for name in KEEP:
            self.assertEqual(result["units"][name], "active")
        self.assertTrue(all(result["files"].values()))
        wifi = apply_cutover(units, files, "10.206.5.149")
        self.assertEqual(wifi["chat_host"], "10.206.5.149")
        with self.assertRaises(ValueError):
            apply_cutover(units, files, "0.0.0.0")
        script = Path(__file__).resolve().parents[1].joinpath("deploy/openbot-cutover.sh").read_text(encoding="utf-8")
        self.assertIn("systemctl stop jetson-bot.service jetson-discord-watch.service", script)
        self.assertNotIn("systemctl stop jetson-budget", script)
        self.assertNotIn("systemctl stop jetson-clock", script)
