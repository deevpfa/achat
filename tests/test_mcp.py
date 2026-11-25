"""Tests for the MCP server (`achat.py mcp`). Run with:  python3 -m unittest discover -s tests -v"""

import io
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from test_achat import SCRIPT, achat


class McpProc:
    """Drives `achat.py mcp` over real stdio, like an MCP client would."""

    def __init__(self, root: Path, wait_timeout: float = 5.0):
        self.proc = subprocess.Popen(
            [sys.executable, str(SCRIPT), "mcp", "--dir", str(root), "--wait-timeout", str(wait_timeout)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self.inbox: "queue.Queue[dict]" = queue.Queue()
        self.responses = {}
        self.notifications = []
        self._next_id = 0
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        for line in self.proc.stdout:
            self.inbox.put(json.loads(line))

    def _drain(self, timeout):
        msg = self.inbox.get(timeout=timeout)
        items = msg if isinstance(msg, list) else [msg]
        for m in items:
            if "id" in m and ("result" in m or "error" in m):
                self.responses[m["id"]] = m
            else:
                self.notifications.append(m)

    def send_raw(self, obj):
        self.proc.stdin.write((obj if isinstance(obj, str) else json.dumps(obj)) + "\n")
        self.proc.stdin.flush()

    def request_async(self, method, params=None, **extra):
        self._next_id += 1
        msg = {"jsonrpc": "2.0", "id": self._next_id, "method": method, **extra}
        if params is not None:
            msg["params"] = params
        self.send_raw(msg)
        return self._next_id

    def response(self, rid, timeout=10.0):
        deadline = time.monotonic() + timeout
        while rid not in self.responses:
            left = deadline - time.monotonic()
            if left <= 0:
                raise AssertionError(f"no response to request {rid} within {timeout}s")
            try:
                self._drain(left)
            except queue.Empty:
                pass
        return self.responses.pop(rid)

    def request(self, method, params=None, timeout=10.0):
        return self.response(self.request_async(method, params), timeout)

    def initialize(self, version="2025-06-18"):
        r = self.request("initialize", {"protocolVersion": version, "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}})
        self.send_raw({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return r["result"]

    def call_async(self, tool, args=None, meta=None):
        params = {"name": tool, "arguments": args or {}}
        if meta:
            params["_meta"] = meta
        return self.request_async("tools/call", params)

    def call(self, tool, args=None, timeout=10.0):
        """Returns (text, is_error)."""
        result = self.response(self.call_async(tool, args), timeout)["result"]
        return result["content"][0]["text"], result["isError"]

    def close(self):
        if not self.proc.stdin.closed:
            self.proc.stdin.close()
        if self.proc.poll() is None:
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self.proc.stdout.close()


class McpTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            p.close()
        self._tmp.cleanup()

    def client(self, **kw):
        c = McpProc(self.root, **kw)
        self.procs.append(c)
        c.initialize()
        return c


class TestHandshake(McpTestCase):
    def test_version_negotiation(self):
        c = McpProc(self.root)
        self.procs.append(c)
        self.assertEqual(c.initialize("2025-03-26")["protocolVersion"], "2025-03-26")
        r = c.request("initialize", {"protocolVersion": "2099-01-01", "capabilities": {}})["result"]
        self.assertEqual(r["protocolVersion"], achat.MCP_PROTOCOL_VERSIONS[0])
        self.assertIn("tools", r["capabilities"])
        self.assertIn("join", r["instructions"])

    def test_tools_list_is_portable(self):
        c = self.client()
        tools = c.request("tools/list")["result"]["tools"]
        self.assertEqual({t["name"] for t in tools}, {"list_rooms", "create_room", "join", "wait", "send", "read", "get_attachment"})
        for t in tools:
            schema = t["inputSchema"]
            self.assertEqual(schema["type"], "object", t["name"])
            self.assertTrue(t["description"])
            # Keep schemas to the subset every vendor's function calling accepts.
            dumped = json.dumps(schema)
            for risky in ('"pattern"', '"additionalProperties"', '"default"', '"$ref"', '"oneOf"'):
                self.assertNotIn(risky, dumped, t["name"])

    def test_ping(self):
        c = self.client()
        self.assertEqual(c.request("ping")["result"], {})


class TestConversation(McpTestCase):
    def test_two_mcp_agents_hold_a_conversation(self):
        alice, bob = self.client(), self.client()
        text, err = alice.call("create_room", {"name": "r", "participants": ["alice", "bob"], "topic": "t", "max_turns": 3})
        self.assertFalse(err, text)
        self.assertIn('Join the achat room "r"', text)
        self.assertFalse(alice.call("join", {"room": "r", "name": "alice"})[1])
        self.assertFalse(bob.call("join", {"room": "r", "name": "bob"})[1])

        bob_wait = bob.call_async("wait", {"timeout_seconds": 20})  # blocks until alice speaks

        text, _ = alice.call("wait")
        self.assertIn("you open the conversation", text)
        self.assertIn("STATUS: YOUR_TURN", text)
        hostile = "It's \"quoted\", $HOME, `uname`, $(whoami) — ñ 🦙"
        self.assertIn("Sent #1", alice.call("send", {"text": hostile})[0])

        text = bob.response(bob_wait, 15)["result"]["content"][0]["text"]
        self.assertIn(hostile, text)
        self.assertIn("STATUS: YOUR_TURN", text)
        bob.call("send", {"text": "hi alice"})

        text, _ = alice.call("wait")
        self.assertIn("hi alice", text)
        text, _ = alice.call("send", {"text": "bye"})
        self.assertIn("STATUS: CLOSED", text)  # turn limit (3)

        text, _ = bob.call("wait")
        self.assertIn("bye", text)
        self.assertIn("STATUS: CLOSED", text)
        self.assertNotIn("python3", text)  # MCP agents get tool hints, never shell commands

        msgs = achat.Room(self.root, "r").messages()
        self.assertEqual([(m.sender, m.body) for m in msgs], [("alice", hostile), ("bob", "hi alice"), ("alice", "bye")])

    def test_mcp_agent_and_shell_agent_share_a_room(self):
        alice = self.client()
        alice.call("create_room", {"name": "r", "participants": ["alice", "bob"], "topic": "interop"})
        alice.call("join", {"room": "r", "name": "alice"})
        alice.call("wait")
        alice.call("send", {"text": "hola desde MCP"})

        def cli(*args):
            return subprocess.run([sys.executable, str(SCRIPT), *args, "--dir", str(self.root)],
                                  capture_output=True, text=True, encoding="utf-8", timeout=30)

        r = cli("wait", "--room", "r", "--as", "bob", "--timeout", "5")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("hola desde MCP", r.stdout)
        self.assertIn("python3", r.stdout)  # the shell agent gets shell hints
        self.assertEqual(cli("send", "--room", "r", "--as", "bob", "hola desde la terminal").returncode, 0)

        text, _ = alice.call("wait")
        self.assertIn("hola desde la terminal", text)
        self.assertNotIn("python3", text)  # ...and the MCP agent gets tool hints

    def test_read_unseen_and_all(self):
        achat.Room.create(self.root, "r", topic="t", participants=["alice", "bob"])
        room = achat.Room(self.root, "r")
        room.publish("alice", "one")
        c = self.client()
        c.call("join", {"room": "r", "name": "bob"})
        text, _ = c.call("read")
        self.assertIn("one", text)
        self.assertIn("STATUS: YOUR_TURN", text)
        self.assertIn("No new messages", c.call("read")[0])
        text, _ = c.call("read", {"all": True})
        self.assertIn("Room 'r'", text)
        self.assertIn("one", text)

    def test_human_interjection_forces_a_reread(self):
        achat.Room.create(self.root, "r", topic="t", participants=["alice", "bob"])
        c = self.client()
        c.call("join", {"room": "r", "name": "alice"})
        c.call("wait")
        achat.Room(self.root, "r").publish(achat.HUMAN, "keep it short")
        text, err = c.call("send", {"text": "long answer"})
        self.assertTrue(err)
        self.assertIn("STATUS: REJECTED", text)
        self.assertIn("Call the `read` tool", text)
        self.assertIn("keep it short", c.call("read")[0])
        self.assertFalse(c.call("send", {"text": "short"})[1])


class TestInterop(McpTestCase):
    """Argument shapes real clients sent. Recorded from Codex 0.171 (OpenAI) talking to Claude."""

    def setUp(self):
        super().setUp()
        achat.Room.create(self.root, "r", topic="t", participants=["claude", "codex"])

    def test_replays_codex_calls(self):
        c = self.client()
        text, err = c.call("join", {"room": "r", "participant_name": "codex"})
        self.assertFalse(err, text)
        achat.Room(self.root, "r").publish("claude", "hola codex")
        text, err = c.call("wait", {"room": "r", "name": "codex", "timeout": 5})
        self.assertFalse(err, text)
        self.assertIn("hola codex", text)
        text, err = c.call("send", {"room": "r", "name": "codex", "message": "hola claude", "end": False})
        self.assertFalse(err, text)
        self.assertEqual(achat.Room(self.root, "r").messages()[-1].body, "hola claude")

    def test_stateless_client_joins_implicitly(self):
        c = self.client()
        achat.Room(self.root, "r").publish("claude", "first")
        text, err = c.call("read", {"room": "r", "name": "codex"})
        self.assertFalse(err, text)
        self.assertIn("first", text)
        self.assertFalse(c.call("send", {"room": "r", "name": "codex", "text": "reply"})[1])

    def test_no_silent_identity_switch(self):
        c = self.client()
        c.call("join", {"room": "r", "name": "codex"})
        text, err = c.call("send", {"name": "claude", "text": "pretending"})
        self.assertTrue(err)
        self.assertIn('joined room "r" as "codex"', text)
        self.assertEqual(achat.Room(self.root, "r").messages(), [])

    def test_end_sent_as_a_string_is_parsed_strictly(self):
        c = self.client()
        c.call("join", {"room": "r", "name": "claude"})
        c.call("wait")
        text, err = c.call("send", {"text": "not the end", "end": "false"})
        self.assertFalse(err, text)
        self.assertIsNone(achat.Room(self.root, "r").status().closed)

    def test_end_over_mcp_is_a_proposal_until_everyone_agrees(self):
        claude, codex = self.client(), self.client()
        claude.call("join", {"room": "r", "name": "claude"})
        codex.call("join", {"room": "r", "name": "codex"})
        claude.call("wait")
        text, _ = claude.call("send", {"text": "agreed on attachments, shall we close?", "end": True})
        self.assertIn("You proposed to end the conversation", text)
        self.assertIsNone(achat.Room(self.root, "r").status().closed)
        text, _ = codex.call("wait")
        self.assertIn("claude proposed to end the conversation", text)
        self.assertIn("end=true", text)
        text, _ = codex.call("send", {"text": "agreed", "end": True})
        self.assertIn("all participants agreed", text)

    def test_attachments_inline_only(self):
        import base64
        c = self.client()
        c.call("join", {"room": "r", "name": "claude"})
        c.call("wait")
        diff = "--- a/x\n+++ b/x\n@@\n-old\n+new\n"
        text, err = c.call("send", {"text": "see the diff", "attachments": [
            {"name": "fix.diff", "content": diff},
            {"name": "logo.bin", "content_base64": base64.b64encode(bytes(range(256))).decode()},
        ]})
        self.assertFalse(err, text)
        msg = achat.Room(self.root, "r").messages()[0]
        self.assertEqual([a.name for a in msg.attachments], ["fix.diff", "logo.bin"])
        text, err = c.call("get_attachment", {"id": msg.attachments[0].short_id})
        self.assertFalse(err, text)
        self.assertIn(diff, text)
        text, err = c.call("get_attachment", {"id": msg.attachments[1].sha256})
        self.assertTrue(err)
        self.assertIn("binary", text)

    def test_mcp_never_reads_paths(self):
        secret = self.root / "secret.txt"
        secret.write_text("TOP SECRET", encoding="utf-8")
        c = self.client()
        c.call("join", {"room": "r", "name": "claude"})
        c.call("wait")
        text, err = c.call("send", {"text": "here", "attachments": [{"name": "secret.txt", "path": str(secret)}]})
        self.assertTrue(err)
        self.assertIn("never reads files from disk", text)
        self.assertEqual(achat.Room(self.root, "r").messages(), [])

    def test_create_room_name_is_a_room_name(self):
        c = self.client()
        self.assertFalse(c.call("create_room", {"room_name": "x", "participants": ["a", "b"], "topic": "t"})[1])
        self.assertTrue((self.root / "x" / "room.md").is_file())


class TestConcurrencyAndLifecycle(McpTestCase):
    def setUp(self):
        super().setUp()
        achat.Room.create(self.root, "r", topic="t", participants=["bob", "alice"])  # alice speaks second

    def test_ping_is_answered_and_cancel_works_while_wait_blocks(self):
        c = self.client()
        c.call("join", {"room": "r", "name": "alice"})
        wait_id = c.call_async("wait", {"timeout_seconds": 60})
        time.sleep(0.5)

        started = time.monotonic()
        c.request("ping", timeout=3)
        self.assertLess(time.monotonic() - started, 2)

        c.send_raw({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": wait_id, "reason": "user"}})
        c.request("tools/list", timeout=5)  # the worker is free again
        with self.assertRaises(AssertionError):
            c.response(wait_id, timeout=1)  # a cancelled request gets no reply
        self.assertEqual(achat.Room(self.root, "r").cursor("alice"), 0)

        achat.Room(self.root, "r").publish("bob", "still delivered")
        self.assertIn("still delivered", c.call("wait")[0])

    def test_stdin_eof_stops_a_blocked_server(self):
        c = McpProc(self.root, wait_timeout=60)
        self.procs.append(c)
        c.initialize()
        c.call("join", {"room": "r", "name": "alice"})
        c.call_async("wait")
        time.sleep(0.5)
        c.proc.stdin.close()
        self.assertEqual(c.proc.wait(timeout=5), 0)

    def test_progress_notifications_during_wait(self):
        old = achat.MCP_PROGRESS_EVERY
        achat.MCP_PROGRESS_EVERY = 0.3
        r_fd, w_fd = os.pipe()
        instream = os.fdopen(r_fd, "r", encoding="utf-8")
        feed = os.fdopen(w_fd, "w", encoding="utf-8")
        outstream = io.StringIO()
        server = achat.McpServer(self.root, wait_timeout=5, instream=instream, outstream=outstream)
        t = threading.Thread(target=server.serve, daemon=True)
        t.start()
        try:
            for msg in (
                {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "join", "arguments": {"room": "r", "name": "alice"}}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                 "params": {"name": "wait", "arguments": {"timeout_seconds": 1.2}, "_meta": {"progressToken": "tok"}}},
            ):
                feed.write(json.dumps(msg) + "\n")
            feed.flush()
            deadline = time.monotonic() + 5
            while '"id": 2' not in outstream.getvalue() and time.monotonic() < deadline:
                time.sleep(0.05)
        finally:
            feed.close()
            t.join(timeout=5)
            instream.close()
            achat.MCP_PROGRESS_EVERY = old
        lines = [json.loads(l) for l in outstream.getvalue().splitlines()]
        progress = [m for m in lines if m.get("method") == "notifications/progress"]
        self.assertGreaterEqual(len(progress), 2)
        self.assertTrue(all(p["params"]["progressToken"] == "tok" for p in progress))
        values = [p["params"]["progress"] for p in progress]
        self.assertEqual(values, sorted(values))
        final = next(m for m in lines if m.get("id") == 2)
        self.assertIn("STATUS: TIMEOUT", final["result"]["content"][0]["text"])


class TestErrors(McpTestCase):
    def test_protocol_errors(self):
        c = self.client()
        self.assertEqual(c.request("tools/call", {"name": "nope", "arguments": {}})["error"]["code"], -32602)
        self.assertEqual(c.request("bogus/method")["error"]["code"], -32601)
        c.send_raw("this is not json")
        c.send_raw({"id": 99, "method": "ping"})  # missing "jsonrpc"
        c.response(None, timeout=5)  # parse error carries id null
        self.assertEqual(c.response(99, timeout=5)["error"]["code"], -32600)

    def test_batch(self):
        c = self.client()
        c.send_raw([{"jsonrpc": "2.0", "id": "a", "method": "tools/list"}, {"jsonrpc": "2.0", "id": "b", "method": "nope"}])
        self.assertIn("tools", c.response("a")["result"])
        self.assertEqual(c.response("b")["error"]["code"], -32601)

    def test_tool_errors_are_readable_results(self):
        achat.Room.create(self.root, "r", topic="t", participants=["alice", "bob"])
        c = self.client()
        cases = [
            ("send", {"text": "hi"}, "join"),
            ("join", {"room": "r", "name": "mallory"}, "not a participant"),
            ("join", {"room": "missing", "name": "alice"}, "not found"),
            ("join", {"room": "r"}, "`name` is required"),
            ("create_room", {"name": "x", "participants": "solo", "topic": "t"}, "at least 2"),
            ("create_room", {"name": "x", "participants": [1, 2], "topic": "t"}, "list of names"),
        ]
        for tool, args, expected in cases:
            text, err = c.call(tool, args)
            self.assertTrue(err, (tool, args))
            self.assertIn(expected, text, (tool, args))
        c.call("join", {"room": "r", "name": "alice"})
        self.assertIn("must be a number", c.call("wait", {"timeout_seconds": "soon"})[0])
        self.assertIn("must be a string", c.call("send", {"text": 42})[0])
        self.assertIn("empty", c.call("send", {"text": "   "})[0])


if __name__ == "__main__":
    unittest.main()
