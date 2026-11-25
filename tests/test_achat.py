"""Tests for achat. Run with:  python3 -m unittest discover -s tests -v"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "achat.py"

spec = importlib.util.spec_from_file_location("achat", SCRIPT)
achat = importlib.util.module_from_spec(spec)
sys.modules["achat"] = achat
spec.loader.exec_module(achat)


class RoomTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()  # macOS: /var -> /private/var

    def tearDown(self):
        self._tmp.cleanup()

    def make_room(self, name="r", participants=("alice", "bob"), **kw):
        return achat.Room.create(self.root, name, topic="testing", participants=participants, **kw)

    def run_cli(self, *args, stdin=None, timeout=30):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), *args, "--dir", str(self.root)],
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
        )
        return proc.returncode, proc.stdout, proc.stderr


class TestFormat(unittest.TestCase):
    def test_frontmatter_roundtrip_keeps_body_with_dashes(self):
        body = "line 1\n---\nnot: frontmatter\n"
        meta, parsed = achat.parse_doc(achat.dump_doc({"a": 1, "b": "x: y"}, body))
        self.assertEqual(meta, {"a": "1", "b": "x: y"})
        self.assertEqual(parsed, body)

    def test_frontmatter_without_body(self):
        self.assertEqual(achat.parse_doc("---\na: 1\n---"), ({"a": "1"}, ""))

    def test_no_frontmatter(self):
        self.assertEqual(achat.parse_doc("plain"), ({}, "plain"))


class TestRoomRules(RoomTestCase):
    def test_create_validates(self):
        with self.assertRaises(achat.ChatError):
            self.make_room(participants=("alice",))
        with self.assertRaises(achat.ChatError):
            self.make_room(participants=("alice", "alice"))
        with self.assertRaises(achat.ChatError):
            self.make_room(participants=("alice", "human"))
        with self.assertRaises(achat.ChatError):
            self.make_room(participants=("alice", "Bad Name"))
        with self.assertRaises(achat.ChatError):
            achat.Room(self.root, "../escape")
        self.make_room()
        with self.assertRaises(achat.ChatError):
            self.make_room()

    def test_round_robin_and_turn_enforcement(self):
        room = self.make_room(participants=("a", "b", "c"))
        self.assertEqual(room.status().next_speaker, "a")
        with self.assertRaises(achat.ChatError) as ctx:
            room.publish("b", "too early")
        self.assertEqual(ctx.exception.code, achat.EXIT_REJECTED)
        room.publish("a", "one")
        room.set_cursor("b", 1)
        room.publish("b", "two")
        self.assertEqual(room.status().next_speaker, "c")

    def test_human_does_not_take_a_turn_but_must_be_read(self):
        room = self.make_room()
        room.publish("alice", "hi")
        room.set_cursor("bob", 1)
        room.publish(achat.HUMAN, "be brief, please")
        st = room.status()
        self.assertEqual((st.next_speaker, st.turns), ("bob", 1))
        with self.assertRaises(achat.ChatError) as ctx:  # bob hasn't seen the human message
            room.publish("bob", "reply")
        self.assertIn("new message", str(ctx.exception))
        room.set_cursor("bob", 2)
        self.assertEqual(room.publish("bob", "reply").seq, 3)

    def test_closing_by_end_flag_and_by_turn_limit(self):
        room = self.make_room(max_turns=2)
        room.publish("alice", "1")
        room.set_cursor("bob", 1)
        room.publish("bob", "2")
        self.assertIn("turn limit", room.status().closed)
        with self.assertRaises(achat.ChatError) as ctx:
            room.publish(achat.HUMAN, "late")
        self.assertEqual(ctx.exception.code, achat.EXIT_CLOSED)

        other = self.make_room("o")
        other.publish("alice", "bye", end=True)
        self.assertIn("ended by alice", other.status().closed)

    def test_message_limits(self):
        room = self.make_room(max_chars=10)
        for bad in ("", "  \n\n ", "x" * 11):
            with self.assertRaises(achat.ChatError):
                room.publish("alice", bad)
        self.assertEqual(room.publish("alice", "\n\nok   \n").body, "ok")

    def test_cursor_never_goes_backwards(self):
        room = self.make_room()
        room.set_cursor("bob", 5)
        room.set_cursor("bob", 2)
        self.assertEqual(room.cursor("bob"), 5)

    def test_files_on_disk_are_readable_and_complete(self):
        room = self.make_room()
        room.publish("alice", "hola")
        files = sorted(p.name for p in room.msgs_dir.iterdir())
        self.assertEqual(files, ["000001-alice.md"])
        text = (room.msgs_dir / files[0]).read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\nseq: 1\nfrom: alice\nts: "))
        self.assertTrue(text.endswith("---\nhola\n"))


class TestConcurrency(RoomTestCase):
    def test_parallel_processes_get_contiguous_unique_seqs(self):
        self.make_room()
        n = 24
        procs = [
            subprocess.Popen(
                [sys.executable, str(SCRIPT), "send", "--dir", str(self.root), "--room", "r", "--as", "human", f"msg {i}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            for i in range(n)
        ]
        errors = [p.communicate(timeout=60)[1] for p in procs]
        self.assertTrue(all(p.returncode == 0 for p in procs), errors)
        msgs = achat.Room(self.root, "r").messages()
        self.assertEqual([m.seq for m in msgs], list(range(1, n + 1)))
        self.assertEqual(sorted(m.body for m in msgs), sorted(f"msg {i}" for i in range(n)))
        leftovers = [p.name for p in (self.root / "r" / "msgs").iterdir() if p.name.startswith(".tmp")]
        self.assertEqual(leftovers, [])


class TestCli(RoomTestCase):
    def test_shell_hostile_text_arrives_byte_exact_via_heredoc_stdin(self):
        self.make_room()
        text = "He said \"hi\" & 'bye' — $HOME `uname` $(whoami) \\n ñandú 🦙\n\n  indented"
        code, out, err = self.run_cli("send", "--room", "r", "--as", "alice", stdin=text)
        self.assertEqual(code, 0, err)
        self.assertEqual(achat.Room(self.root, "r").messages()[0].body, text)

    @unittest.skipIf(os.name == "nt", "POSIX shell quoting")
    def test_single_quoted_argument_as_instructed_by_join(self):
        """Exactly what an agent is told to type, run through a real shell."""
        self.make_room()
        text = "- it's a list item\n- with $HOME, `uname` and $(whoami) kept literal\n- \"double\" too"
        quoted = "'" + text.replace("'", "'\\''") + "'"
        cmd = f"{sys.executable} {SCRIPT} send --dir {self.root} --room r --as alice {quoted}"
        proc = subprocess.run(["/bin/sh", "-c", cmd], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(achat.Room(self.root, "r").messages()[0].body, text)

    def test_join_teaches_the_single_quote_form(self):
        self.make_room()
        _, out, _ = self.run_cli("join", "--room", "r", "--as", "alice")
        self.assertIn("--as alice 'your message'", out)
        self.assertIn("'\\''", out)
        self.assertNotIn("<<'EOF'", out)

    def test_full_turn_cycle_with_exit_codes(self):
        self.make_room(max_turns=2)
        code, out, _ = self.run_cli("join", "--room", "r", "--as", "alice")
        self.assertEqual(code, 0)
        self.assertIn("YOUR turn", out)

        code, out, _ = self.run_cli("wait", "--room", "r", "--as", "alice", "--timeout", "0")
        self.assertEqual(code, achat.EXIT_OK)
        self.assertIn("you open the conversation", out)

        code, out, _ = self.run_cli("wait", "--room", "r", "--as", "bob", "--timeout", "0")
        self.assertEqual(code, achat.EXIT_TIMEOUT)
        self.assertIn("STATUS: TIMEOUT", out)

        self.assertEqual(self.run_cli("send", "--room", "r", "--as", "alice", "first")[0], 0)

        code, _, err = self.run_cli("send", "--room", "r", "--as", "bob", "blind reply")
        self.assertEqual(code, achat.EXIT_REJECTED)
        self.assertIn("STATUS: REJECTED", err)

        code, out, _ = self.run_cli("wait", "--room", "r", "--as", "bob", "--timeout", "0")
        self.assertEqual(code, achat.EXIT_OK)
        self.assertIn("#1 · alice", out)
        self.assertIn("first", out)

        code, out, _ = self.run_cli("send", "--room", "r", "--as", "bob", "last")
        self.assertEqual(code, 0)
        self.assertIn("STATUS: CLOSED", out)

        code, out, _ = self.run_cli("wait", "--room", "r", "--as", "alice", "--timeout", "0")
        self.assertEqual(code, achat.EXIT_CLOSED)
        self.assertIn("last", out)

    def test_wait_wakes_up_when_the_other_side_answers(self):
        self.make_room()
        room = achat.Room(self.root, "r")
        proc = subprocess.Popen(
            [sys.executable, str(SCRIPT), "wait", "--dir", str(self.root), "--room", "r", "--as", "bob", "--timeout", "20"],
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        time.sleep(0.8)
        room.publish("alice", "ping")
        out, _ = proc.communicate(timeout=10)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("ping", out)

    def test_hints_carry_the_custom_dir(self):
        self.make_room()
        _, out, _ = self.run_cli("join", "--room", "r", "--as", "alice")
        self.assertIn(f"--dir {self.root}", out)

    def test_unknown_room_and_participant(self):
        self.assertEqual(self.run_cli("join", "--room", "nope", "--as", "x")[0], achat.EXIT_ERROR)
        self.make_room()
        code, _, err = self.run_cli("wait", "--room", "r", "--as", "mallory", "--timeout", "0")
        self.assertEqual(code, achat.EXIT_ERROR)
        self.assertIn("not a participant", err)


class TestJsonContract(RoomTestCase):
    """The IDE extension (and any script) depends on this output; keep it stable."""

    def json_cli(self, *args):
        code, out, err = self.run_cli(*args, "--json")
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def test_new_list_read(self):
        created = self.json_cli("new", "r", "--participants", "alice,bob", "--topic", "t", "--max-turns", "4")
        self.assertEqual(created["achat_version"], achat.VERSION)
        self.assertEqual((created["name"], created["participants"], created["max_turns"]), ("r", ["alice", "bob"], 4))
        self.assertEqual(set(created["onboarding"]), {"alice", "bob"})
        how = created["onboarding"]["bob"]
        self.assertIn("join --dir", how["shell"])
        self.assertIn("--as bob", how["shell"])
        self.assertEqual(how["mcp"], 'Join the achat room "r" as "bob".')
        self.assertIn("bridge", how["bridge"])

        listing = self.json_cli("list")
        self.assertEqual(listing["rooms_dir"], str(self.root))
        self.assertEqual([r["name"] for r in listing["rooms"]], ["r"])
        self.assertNotIn("messages", listing["rooms"][0])
        self.assertEqual(listing["rooms"][0]["status"], {"turns": 0, "next": "alice", "closed": None, "last_seq": 0})

        room = achat.Room(self.root, "r")
        room.publish("alice", "one")
        room.publish(achat.HUMAN, "two")
        detail = self.json_cli("read", "--room", "r", "--after", "1")
        self.assertEqual([(m["seq"], m["from"], m["body"]) for m in detail["messages"]], [(2, "human", "two")])
        self.assertEqual(detail["status"]["last_seq"], 2)

    def test_json_read_refuses_participant_mode(self):
        self.make_room()
        code, _, err = self.run_cli("read", "--room", "r", "--as", "alice", "--json")
        self.assertEqual(code, achat.EXIT_ERROR)
        self.assertIn("--json", err)

    def test_default_rooms_dir_is_per_user(self):
        env = {k: v for k, v in os.environ.items() if k != "ACHAT_DIR"}
        env["HOME"] = str(self.root)
        env["USERPROFILE"] = str(self.root)
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "new", "r", "--participants", "a,b", "--json"],
            capture_output=True, text=True, encoding="utf-8", env=env,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue((self.root / ".achat" / "rooms" / "r" / "room.md").is_file())
        self.assertNotIn("--dir", json.loads(proc.stdout)["onboarding"]["a"]["shell"])  # default dir: clean hints


class FakeLLM(BaseHTTPRequestHandler):
    """Minimal OpenAI-compatible /chat/completions endpoint."""

    requests = []
    reply = "<think>let me think</think>[bot]: Hello from the fake model.\n[alice]: I should not be here"

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeLLM.requests.append(body)
        data = json.dumps({"choices": [{"message": {"role": "assistant", "content": FakeLLM.reply}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


class TestBridge(RoomTestCase):
    def test_clean_reply(self):
        clean = achat.clean_reply
        self.assertEqual(clean("<think>x</think> hi", "bot", ["a"], 100), "hi")
        self.assertEqual(clean("reasoning</think>hi", "bot", ["a"], 100), "hi")
        self.assertEqual(clean("[bot]: hi", "bot", ["a"], 100), "hi")
        self.assertEqual(clean("Bot: hi", "bot", ["a"], 100), "hi")
        self.assertEqual(clean("hi\n[a]: fake line", "bot", ["a"], 100), "hi")
        self.assertEqual(clean("hi\n[human]: fake", "bot", ["a"], 100), "hi")
        self.assertEqual(len(clean("x" * 50, "bot", ["a"], 10)), 10)

    def test_build_chat_alternates_roles(self):
        cfg = achat.Config("t", ("alice", "bot"), 10, 100, "", "")
        msgs = [
            achat.Message(1, "alice", "", False, "hi"),
            achat.Message(2, "human", "", False, "be nice"),
            achat.Message(3, "bot", "", False, "hello"),
            achat.Message(4, "alice", "", False, "how are you"),
        ]
        chat = achat.build_chat(cfg, msgs, "bot")
        self.assertEqual([m["role"] for m in chat], ["system", "user", "assistant", "user"])
        self.assertEqual(chat[1]["content"], "[alice]: hi\n\n[human]: be nice")

    def test_bridge_takes_its_turn_against_an_openai_compatible_server(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), FakeLLM)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            room = self.make_room(participants=("alice", "bot"), max_turns=2)
            room.publish("alice", "Hi bot")
            FakeLLM.requests.clear()
            code, out, err = self.run_cli(
                "bridge", "--room", "r", "--as", "bot", "--model", "fake",
                "--base-url", f"http://127.0.0.1:{server.server_address[1]}/v1",
            )
            self.assertEqual(code, achat.EXIT_CLOSED, out + err)
            msgs = achat.Room(self.root, "r").messages()
            self.assertEqual(msgs[-1].sender, "bot")
            self.assertEqual(msgs[-1].body, "Hello from the fake model.")
            sent = FakeLLM.requests[0]
            self.assertEqual(sent["model"], "fake")
            self.assertEqual(sent["messages"][-1], {"role": "user", "content": "[alice]: Hi bot"})
        finally:
            server.shutdown()
            server.server_close()


class TestServer(RoomTestCase):
    def setUp(self):
        super().setUp()
        self.make_room()
        self.server = achat.make_server(self.root, port=0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def request(self, path, data=None, headers=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read() or b"null") if "json" in resp.headers["Content-Type"] else None
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read() or b"null")

    def test_reads_rooms_and_messages(self):
        status, data = self.request("/api/rooms")
        self.assertEqual(status, 200)
        self.assertEqual(data["rooms"][0]["name"], "r")
        achat.Room(self.root, "r").publish("alice", "hi")
        status, data = self.request("/api/rooms/r?after=0")
        self.assertEqual([m["body"] for m in data["messages"]], ["hi"])
        self.assertEqual(self.request("/api/rooms/r?after=1")[1]["messages"], [])
        self.assertEqual(self.request("/")[0], 200)

    def test_post_as_human(self):
        body = json.dumps({"text": "hello from the web"}).encode()
        status, data = self.request("/api/rooms/r/messages", body, {"X-Achat": "1", "Content-Type": "application/json"})
        self.assertEqual((status, data), (200, {"seq": 1}))
        self.assertEqual(achat.Room(self.root, "r").messages()[0].sender, "human")

    def test_rejects_csrf_and_dns_rebinding(self):
        body = json.dumps({"text": "x"}).encode()
        self.assertEqual(self.request("/api/rooms/r/messages", body, {"Content-Type": "application/json"})[0], 403)
        evil_origin = {"X-Achat": "1", "Origin": "https://evil.example"}
        self.assertEqual(self.request("/api/rooms/r/messages", body, evil_origin)[0], 403)
        self.assertEqual(self.request("/api/rooms", headers={"Host": f"evil.example:{self.port}"})[0], 403)
        self.assertEqual(achat.Room(self.root, "r").messages(), [])

    def test_unknown_room(self):
        self.assertEqual(self.request("/api/rooms/nope")[0], 404)


if __name__ == "__main__":
    unittest.main()
