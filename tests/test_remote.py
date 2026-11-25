"""Remote participants: an agent on another machine joins a room hosted here, through an invite.
Run with:  python3 -m unittest discover -s tests -v"""

import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from test_achat import SCRIPT, achat


class Stdio:
    """Minimal MCP client over a subprocess's stdio."""

    def __init__(self, args, env):
        self.p = subprocess.Popen([sys.executable, str(SCRIPT), *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1, env=env)
        self.q, self.n = queue.Queue(), 0
        threading.Thread(target=lambda: [self.q.put(json.loads(l)) for l in self.p.stdout], daemon=True).start()

    def request(self, method, params=None):
        self.n += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params or {}}) + "\n")
        self.p.stdin.flush()
        while True:
            m = self.q.get(timeout=20)
            if m.get("id") == self.n:
                return m

    def call(self, tool, args=None):
        r = self.request("tools/call", {"name": tool, "arguments": args or {}})["result"]
        return r["content"][0]["text"], r["isError"]

    def close(self):
        self.p.stdin.close()
        self.p.wait(timeout=10)
        self.p.stdout.close()


class RemoteCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name).resolve()
        self.root, self.home = base / "host-rooms", base / "guest-home"
        self.room = achat.Room.create(self.root, "charla", topic="remoto", participants=["local", "guest"])
        achat.Room.create(self.root, "privada", topic="secreta", participants=["a", "b"])
        self.server = achat.make_server(self.root, port=0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.token = achat.create_invite(self.room, "guest")
        self.code = achat.encode_invite(self.url, "charla", "guest", self.token)
        self.env = {**os.environ, "ACHAT_HOME": str(self.home)}

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self._tmp.cleanup()

    def guest_room(self, token=None):
        return achat.RemoteRoom({"url": self.url, "room": "charla", "as": "guest", "token": token or self.token})


class TestInvites(RemoteCase):
    def test_token_is_stored_hashed_and_private(self):
        path = self.room.state_dir / "invites.json"
        text = path.read_text(encoding="utf-8")
        self.assertNotIn(self.token, text)
        self.assertIn(achat._token_hash(self.token), text)
        if os.name != "nt":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_invite_code_roundtrip_and_validation(self):
        inv = achat.decode_invite(self.code)
        self.assertEqual((inv["url"], inv["room"], inv["as"], inv["token"]), (self.url, "charla", "guest", self.token))
        for bad in ("hola", "achat1:%%%", achat.INVITE_PREFIX + "e30"):
            with self.assertRaises(achat.ChatError):
                achat.decode_invite(bad)

    def test_wrong_or_revoked_token_is_rejected(self):
        with self.assertRaises(achat.ChatError) as ctx:
            self.guest_room("not-the-token").config
        self.assertIn("rejected", str(ctx.exception))
        self.assertEqual(self.guest_room().config.participants, ("local", "guest"))
        achat.revoke_invite(self.room, "guest")
        with self.assertRaises(achat.ChatError):
            self.guest_room().config

    def test_a_new_invite_replaces_the_old_one(self):
        new = achat.create_invite(self.room, "guest")
        with self.assertRaises(achat.ChatError):
            self.guest_room().config
        self.assertEqual(self.guest_room(new).config.topic, "remoto")


class TestScope(RemoteCase):
    def test_the_guest_can_only_be_itself_in_its_room(self):
        r = self.guest_room()
        with self.assertRaises(achat.ChatError):
            r.publish("local", "pretending to be the host's agent")
        backend = achat.RemoteBackend(achat.decode_invite(self.code))
        with self.assertRaises(achat.ChatError):
            backend.open("privada")
        with self.assertRaises(achat.ChatError):
            backend.create("otra", topic="", participants=["x", "y"])
        self.assertEqual([x.name for x in backend.rooms()], ["charla"])
        self.assertEqual(self.room.messages(), [])

    def test_viewer_is_only_for_this_machine(self):
        for a in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            self.assertTrue(achat.is_loopback(a), a)
        for a in ("100.101.102.103", "192.168.1.5", "8.8.8.8"):
            self.assertFalse(achat.is_loopback(a), a)
        self.assertTrue(achat.is_private_net("100.101.102.103"))   # Tailscale
        self.assertFalse(achat.is_private_net("8.8.8.8"))

    def test_messages_endpoint_uses_etags(self):
        r = self.guest_room()
        r.messages()
        payload, etag = r._call("GET", "messages", etag=r._etag)
        self.assertIsNone(payload)      # 304: nothing new
        self.room.publish("local", "nuevo")
        self.assertEqual([m.body for m in r.messages()], ["nuevo"])


class TestRemoteConversation(RemoteCase):
    def test_guest_agent_talks_over_mcp(self):
        add = subprocess.run([sys.executable, str(SCRIPT), "remote", "add", self.code], capture_output=True, text=True,
                             encoding="utf-8", env=self.env, timeout=30)
        self.assertEqual(add.returncode, 0, add.stderr)
        self.assertIn("mcp --remote charla", add.stdout)
        saved = self.home / "remotes" / "charla.json"
        self.assertTrue(saved.is_file())
        if os.name != "nt":
            self.assertEqual(saved.stat().st_mode & 0o777, 0o600)

        self.room.publish("local", "hola desde el host", attachments=[("plan.md", ("# plan\n" + "linea\n" * 30).encode())])
        g = Stdio(["mcp", "--remote", "charla"], self.env)
        try:
            g.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
            text, err = g.call("join", {"room": "charla", "name": "local"})
            self.assertTrue(err)
            self.assertIn('invite for "guest"', text)
            text, err = g.call("join", {"room": "charla", "name": "guest"})
            self.assertFalse(err, text)
            text, err = g.call("wait", {"timeout_seconds": 5})
            self.assertFalse(err, text)
            self.assertIn("hola desde el host", text)
            self.assertIn("📎 plan.md", text)
            att = self.room.messages()[0].attachments[0]
            text, err = g.call("get_attachment", {"id": att.short_id})
            self.assertIn("linea", text)
            text, err = g.call("send", {"text": "hola desde lejos", "attachments": [{"name": "fix.diff", "content": "-a\n+b\n"}]})
            self.assertFalse(err, text)
            text, err = g.call("send", {"text": "otra vez"})
            self.assertTrue(err)
            self.assertIn("Call the `wait` tool", text)   # the host words rejections for an MCP agent
        finally:
            g.close()
        msgs = self.room.messages()
        self.assertEqual([(m.sender, m.body) for m in msgs], [("local", "hola desde el host"), ("guest", "hola desde lejos")])
        self.assertEqual(self.room.attachment_bytes(msgs[1].attachments[0].sha256), b"-a\n+b\n")
        self.assertEqual([m.body for m in self.room.unseen("local", msgs)], ["hola desde lejos"])

    def test_invite_and_remote_cli(self):
        inv = subprocess.run([sys.executable, str(SCRIPT), "invite", "--room", "charla", "--as", "guest", "--url", self.url,
                              "--dir", str(self.root)], capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(inv.returncode, 0, inv.stderr)
        code = next(l for l in inv.stdout.splitlines() if l.startswith(achat.INVITE_PREFIX))
        self.assertEqual(achat.decode_invite(code)["as"], "guest")
        lst = subprocess.run([sys.executable, str(SCRIPT), "remote", "list"], capture_output=True, text=True, encoding="utf-8",
                             env=self.env, timeout=30)
        self.assertIn("No remotes yet", lst.stdout)


if __name__ == "__main__":
    unittest.main()
