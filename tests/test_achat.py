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


class TestHelpers(unittest.TestCase):
    def test_parse_bool_is_strict(self):
        for v, want in [(None, False), (False, False), (True, True), ("false", False), ("TRUE", True), (0, False), (1, True)]:
            self.assertIs(achat.parse_bool(v, "end"), want, v)
        for bad in ("no", "", "yes", 2, 1.0, [], {}):
            with self.assertRaises(achat.ChatError):
                achat.parse_bool(bad, "end")

    def test_quoting_per_platform(self):
        self.assertEqual(achat.quote_arg("/a/b c/achat.py", windows=False), "'/a/b c/achat.py'")
        self.assertEqual(achat.quote_arg(r"C:\Users\me\achat.py", windows=True), r"C:\Users\me\achat.py")
        self.assertEqual(achat.quote_arg(r"C:\Program Files\achat.py", windows=True), r'"C:\Program Files\achat.py"')
        self.assertIn("PowerShell: write ' as ''", "\n".join(achat.quoting_help(windows=True)))
        self.assertIn("'\\''", "\n".join(achat.quoting_help(windows=False)))


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
        other.publish(achat.HUMAN, "that's enough", end=True)
        self.assertIn("ended by human", other.status().closed)  # the human closes at once

    def test_message_limits(self):
        room = self.make_room(max_chars=10)
        for bad in ("", "  \n\n ", "x" * 11):
            with self.assertRaises(achat.ChatError):
                room.publish("alice", bad)
        self.assertEqual(room.publish("alice", "\n\nok   \n").body, "ok")

    def test_seen_only_grows(self):
        room = self.make_room()
        for k in range(5):
            room.publish(achat.HUMAN, f"m{k}")
        room.set_cursor("bob", 5)
        room.set_cursor("bob", 2)
        self.assertEqual(room.cursor("bob"), 5)
        self.assertEqual(room.unseen("bob", room.messages()), [])

    def test_files_on_disk_are_readable_and_complete(self):
        room = self.make_room()
        room.publish("alice", "hola")
        files = sorted(p.name for p in room.msgs_dir.iterdir())
        self.assertEqual(len(files), 1)
        self.assertRegex(files[0], r"^000001-alice\.[0-9a-f]{6}\.md$")   # format v2: sync-safe name
        text = (room.msgs_dir / files[0]).read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\nseq: 1\nfrom: alice\nts: "))
        self.assertIn("\nbytes: 5\n", text)
        self.assertTrue(text.endswith("---\nhola\n"))


class TestEndProposals(RoomTestCase):
    """An agent can't close a conversation alone: ending needs every agent's agreement."""

    def say(self, room, who, text, end=False):
        room.set_cursor(who, room.status().last_seq)
        return room.publish(who, text, end=end)

    def test_proposal_then_agreement_closes(self):
        room = self.make_room()
        self.say(room, "alice", "shall we stop?", end=True)
        st = room.status()
        self.assertIsNone(st.closed)
        self.assertEqual(st.end_proposed_by, ("alice",))
        self.say(room, "bob", "yes, bye", end=True)
        self.assertEqual(room.status().closed, "all participants agreed to end at #2")

    def test_a_normal_reply_withdraws_the_proposal(self):
        room = self.make_room()
        self.say(room, "alice", "shall we stop?", end=True)
        self.say(room, "bob", "one more point first")
        st = room.status()
        self.assertIsNone(st.closed)
        self.assertEqual(st.end_proposed_by, ())

    def test_three_agents_need_everyone(self):
        room = self.make_room(participants=("a", "b", "c"))
        self.say(room, "a", "end?", end=True)
        self.say(room, "b", "agreed", end=True)
        self.assertEqual(room.status().end_proposed_by, ("a", "b"))
        self.say(room, "c", "agreed", end=True)
        self.assertIn("all participants agreed", room.status().closed)

    def test_human_messages_neither_agree_nor_withdraw(self):
        room = self.make_room()
        self.say(room, "alice", "end?", end=True)
        room.publish(achat.HUMAN, "fine by me")
        self.assertEqual(room.status().end_proposed_by, ("alice",))
        self.say(room, "bob", "ok", end=True)
        self.assertIn("all participants agreed", room.status().closed)

    def test_legacy_end_true_from_an_agent_still_closes(self):
        room = self.make_room()
        (room.msgs_dir / "000001-alice.md").write_text("---\nseq: 1\nfrom: alice\nts: x\nend: true\n---\nbye\n", encoding="utf-8")
        self.assertEqual(achat.Room(self.root, "r").status().closed, "ended by alice at #1")

    def test_on_disk_and_json(self):
        room = self.make_room()
        self.say(room, "alice", "end?", end=True)
        text = next(room.msgs_dir.glob("000001-alice.*.md")).read_text(encoding="utf-8")
        self.assertIn("\nend: propose\n", text)
        m = achat.Room(self.root, "r").messages()[0].to_json()
        self.assertEqual((m["end"], m["end_proposal"]), (False, True))

    def test_the_next_speaker_is_told_how_to_agree(self):
        self.make_room()
        self.assertEqual(self.run_cli("send", "--room", "r", "--as", "alice", "--end", "I think we're done")[0], 0)
        code, out, _ = self.run_cli("wait", "--room", "r", "--as", "bob", "--timeout", "0")
        self.assertEqual(code, achat.EXIT_OK)
        self.assertIn("alice proposed to end the conversation", out)
        self.assertIn("--end", out)
        code, out, _ = self.run_cli("send", "--room", "r", "--as", "bob", "--end", "agreed, bye")
        self.assertIn("STATUS: CLOSED — all participants agreed", out)


class TestAttachments(RoomTestCase):
    def test_stored_once_by_hash_and_read_back(self):
        room = self.make_room()
        m1 = room.publish(achat.HUMAN, "one", attachments=[("a.txt", b"same bytes")])
        m2 = room.publish(achat.HUMAN, "two", attachments=[("b.txt", b"same bytes")])
        self.assertEqual(m1.attachments[0].sha256, m2.attachments[0].sha256)
        self.assertEqual(len(list((room.dir / "attachments").iterdir())), 1)  # deduplicated within the room
        again = achat.Room(self.root, "r").messages()
        self.assertEqual([(a.name, a.size, a.type) for m in again for a in m.attachments], [("a.txt", 10, "text/plain"), ("b.txt", 10, "text/plain")])
        self.assertIn('\nattachments: [{"sha256": ', next(room.msgs_dir.glob("000001-human.*.md")).read_text(encoding="utf-8"))

    def test_limits_and_names(self):
        room = achat.Room.create(self.root, "r", topic="t", participants=["a", "b"], max_attachment_bytes=10)
        for files in ([("x", b"")], [("big", b"x" * 11)], [(f"f{i}", b"x") for i in range(11)]):
            with self.assertRaises(achat.ChatError):
                room.publish(achat.HUMAN, "hi", attachments=files)
        m = room.publish(achat.HUMAN, "hi", attachments=[("../../etc/passwd", b"root:x")])
        self.assertEqual(m.attachments[0].name, "passwd")
        self.assertTrue((room.dir / "attachments" / m.attachments[0].sha256).is_file())
        self.assertFalse((self.root / "etc").exists())

    def test_rejected_send_leaves_no_blob(self):
        room = self.make_room()
        with self.assertRaises(achat.ChatError):
            room.publish("bob", "not my turn", attachments=[("a.txt", b"data")])
        self.assertFalse((room.dir / "attachments").exists())

    def test_cli_attach_preview_and_fetch(self):
        self.make_room()
        src = self.root / "notes.md"
        src.write_text("\n".join(f"line {i}" for i in range(1, 31)) + "\n", encoding="utf-8")
        binf = self.root / "blob.bin"
        binf.write_bytes(bytes(range(256)))
        code, out, err = self.run_cli("send", "--room", "r", "--as", "alice", "--attach", str(src), "--attach", str(binf), "two files")
        self.assertEqual(code, 0, err)
        code, out, _ = self.run_cli("wait", "--room", "r", "--as", "bob", "--timeout", "0")
        self.assertIn("📎 notes.md · text/markdown", out)
        self.assertIn("   ┆ line 20", out)
        self.assertNotIn("line 21", out)
        self.assertIn("… 10 more lines", out)
        self.assertIn("attachment --dir", out)
        m = achat.Room(self.root, "r").messages()[0]
        code, out, _ = self.run_cli("attachment", "--room", "r", m.attachments[0].short_id[:8])
        self.assertEqual((code, out), (0, src.read_text(encoding="utf-8")))
        code, _, err = self.run_cli("attachment", "--room", "r", m.attachments[1].short_id)
        self.assertEqual(code, achat.EXIT_ERROR)
        self.assertIn("--out", err)
        dest = self.root / "copy.bin"
        self.assertEqual(self.run_cli("attachment", "--room", "r", "@attachment:" + m.attachments[1].short_id, "--out", str(dest))[0], 0)
        self.assertEqual(dest.read_bytes(), bytes(range(256)))
        for bad in ("zz", "1234", "deadbeefdeadbeef"):
            self.assertEqual(self.run_cli("attachment", "--room", "r", bad)[0], achat.EXIT_ERROR, bad)

    def test_json_has_previews(self):
        room = self.make_room()
        room.publish(achat.HUMAN, "see", attachments=[("a.py", b"print(1)\n")])
        code, out, _ = self.run_cli("read", "--room", "r", "--json")
        att = json.loads(out)["messages"][0]["attachments"][0]
        self.assertEqual((att["name"], att["preview"], att["lines"]), ("a.py", "print(1)", 1))

    def test_bridge_prompt_includes_small_text_files(self):
        room = self.make_room(participants=("alice", "bot"))
        room.publish("alice", "check this", attachments=[("x.txt", b"the content"), ("y.bin", bytes(range(256)))])
        chat = achat.build_chat(room.config, room.messages(), "bot", room=room)
        self.assertIn("[attached file x.txt]\nthe content", chat[-1]["content"])
        self.assertIn("[attached file y.bin: application/octet-stream", chat[-1]["content"])


class TestSyncedFolders(unittest.TestCase):
    """Two machines sharing a room through a synced folder (Syncthing, Dropbox...): no shared lock,
    files arrive late and out of order. Simulated with two roots and an explicit copy."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name).resolve()
        self.A, self.B = base / "a", base / "b"
        achat.Room.create(self.A, "r", topic="t", participants=["claude", "codex"])
        import shutil
        shutil.copytree(self.A / "r", self.B / "r")

    def tearDown(self):
        self._tmp.cleanup()

    def sync(self, src, dst, only=None):
        """Copy message and attachment files that dst doesn't have yet (arrival time = now)."""
        import shutil
        for sub in ("msgs", "attachments"):
            d = src / "r" / sub
            if not d.is_dir():
                continue
            (dst / "r" / sub).mkdir(exist_ok=True)
            for f in d.iterdir():
                if f.name.startswith(".") or (only and not f.name.startswith(only)):
                    continue
                if not (dst / "r" / sub / f.name).exists():
                    shutil.copyfile(f, dst / "r" / sub / f.name)

    def room(self, root):
        return achat.Room(root, "r")

    def test_same_seq_on_two_machines_loses_nothing(self):
        a, b = self.room(self.A), self.room(self.B)
        a.publish("claude", "uno")
        self.sync(self.A, self.B)
        b.set_cursor("codex", 1)
        m_human = a.publish(achat.HUMAN, "interjection from A")      # seq 2 on A ...
        m_codex = b.publish("codex", "reply from B")                 # ... and seq 2 on B, at the same time
        self.assertEqual((m_human.seq, m_codex.seq), (2, 2))
        self.sync(self.A, self.B); self.sync(self.B, self.A)
        order_a = [(m.seq, m.sender, m.body) for m in self.room(self.A).messages()]
        order_b = [(m.seq, m.sender, m.body) for m in self.room(self.B).messages()]
        self.assertEqual(order_a, order_b)                            # same order everywhere
        self.assertEqual(len(order_a), 3)
        self.assertEqual(self.room(self.A).status().last_seq, 2)
        self.assertEqual(self.room(self.A).publish(achat.HUMAN, "next").seq, 3)

    def test_a_late_message_with_an_already_seen_seq_is_still_delivered(self):
        a, b = self.room(self.A), self.room(self.B)
        a.publish("claude", "uno")
        self.sync(self.A, self.B)
        b.mark_seen("codex", b.messages())
        b.publish("codex", "dos")
        self.sync(self.B, self.A)
        # both humans interject at once: two different #3s, one per machine
        self.room(self.A).publish(achat.HUMAN, "from A")
        self.room(self.B).publish(achat.HUMAN, "from B")
        a = self.room(self.A)
        a.mark_seen("claude", a.messages())          # claude has read #3 "from A" ...
        self.sync(self.B, self.A)                     # ... then B's #3 lands late
        d = achat.await_turn(self.room(self.A), "claude", 0)
        self.assertEqual([m.body for m in d.messages], ["from B"])   # a "read up to #3" cursor would skip it

    def test_out_of_order_sync_waits_for_the_missing_message(self):
        a, b = self.room(self.A), self.room(self.B)
        a.publish("claude", "uno")                               # A: claude #1
        self.sync(self.A, self.B)
        b.mark_seen("codex", b.messages())
        b.publish("codex", "dos")                                # B: codex #2
        self.sync(self.B, self.A)
        a = self.room(self.A)
        a.publish(achat.HUMAN, "be brief")                       # A: the human interjects, #3
        a.mark_seen("claude", a.messages())
        a.publish("claude", "cuatro")                            # A: claude #4
        self.sync(self.A, self.B, only="000004")                 # B gets #4 before #3
        d = achat.await_turn(self.room(self.B), "codex", 0)
        self.assertEqual((d.state, d.syncing), ("timeout", 3))   # waits: #3 is still syncing
        self.assertIn("message #3 is still syncing", achat.render_delivery(d, self.room(self.B), achat.McpHints()))
        with self.assertRaises(achat.ChatError) as ctx:
            self.room(self.B).publish("codex", "would ignore the human")
        self.assertIn("hasn't synced", str(ctx.exception))
        self.sync(self.A, self.B)
        d = achat.await_turn(self.room(self.B), "codex", 0)
        self.assertEqual(d.state, "your_turn")
        self.assertEqual([m.body for m in d.messages], ["be brief", "cuatro"])

    def test_a_gap_that_never_fills_is_ignored_after_the_grace_period(self):
        room = self.room(self.A)
        msgs = [achat.Message(1, "claude", "", "", "a", id="000001-claude.aaaaaa", arrived=100.0),
                achat.Message(3, "codex", "", "", "c", id="000003-codex.bbbbbb", arrived=100.0)]
        self.assertEqual(room.sync_gap(msgs, now=100.0 + 5), 2)
        self.assertIsNone(room.sync_gap(msgs, now=100.0 + achat.SYNC_GRACE + 1))

    def test_a_half_synced_file_is_skipped_until_complete(self):
        a = self.room(self.A)
        a.publish("claude", "a complete message with some length")
        src = next((self.A / "r" / "msgs").glob("000001-*.md"))
        data = src.read_bytes()
        dst = self.B / "r" / "msgs" / src.name
        dst.write_bytes(data[: len(data) - 10])
        b = self.room(self.B)
        self.assertEqual(b.messages(), [])          # partial: not read, and not cached
        dst.write_bytes(data)
        self.assertEqual([m.body for m in b.messages()], ["a complete message with some length"])

    def test_refuses_to_write_a_newer_format(self):
        p = self.A / "r" / "room.md"
        p.write_text(p.read_text(encoding="utf-8").replace("format: 2", "format: 3"), encoding="utf-8")
        with self.assertRaises(achat.ChatError) as ctx:
            self.room(self.A).publish(achat.HUMAN, "hola")
        self.assertIn("format 3", str(ctx.exception))

    def test_legacy_v1_rooms_keep_working_and_stay_v1(self):
        root = Path(self._tmp.name).resolve() / "legacy"
        (root / "old" / "msgs").mkdir(parents=True)
        (root / "old" / ".state").mkdir()
        (root / "old" / "room.md").write_text("---\nformat: 1\nparticipants: alice, bob\nmax_turns: 9\n---\n", encoding="utf-8")
        (root / "old" / "msgs" / "000001-alice.md").write_text("---\nseq: 1\nfrom: alice\nts: x\n---\nhola\n", encoding="utf-8")
        (root / "old" / ".state" / "bob.cursor").write_text("1\n")
        room = achat.Room(root, "old")
        self.assertEqual([m.body for m in room.messages()], ["hola"])
        self.assertEqual(room.unseen("bob", room.messages()), [])    # the legacy cursor still counts
        m = room.publish("bob", "chau")
        self.assertTrue((root / "old" / "msgs" / "000002-bob.md").is_file())   # v1 names for v1 readers
        self.assertEqual(m.seq, 2)


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
        self.assertIn(f"--dir {achat.quote_arg(str(self.root))}", out)

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
        self.assertEqual(listing["rooms"][0]["status"], {"turns": 0, "next": "alice", "closed": None, "last_seq": 0, "end_proposed_by": []})

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
            achat.Message(1, "alice", "", "", "hi"),
            achat.Message(2, "human", "", "", "be nice"),
            achat.Message(3, "bot", "", "", "hello"),
            achat.Message(4, "alice", "", "", "how are you"),
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

    def test_end_as_string_false_does_not_close(self):
        hdrs = {"X-Achat": "1", "Content-Type": "application/json"}
        self.assertEqual(self.request("/api/rooms/r/messages", json.dumps({"text": "a", "end": "false"}).encode(), hdrs)[0], 200)
        self.assertIsNone(achat.Room(self.root, "r").status().closed)
        self.assertEqual(self.request("/api/rooms/r/messages", json.dumps({"text": "b", "end": "maybe"}).encode(), hdrs)[0], 409)

    def test_attachment_download_is_never_html(self):
        m = achat.Room(self.root, "r").publish(achat.HUMAN, "x", attachments=[("evil.html", b"<script>alert(1)</script>")])
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/rooms/r/attachments/{m.attachments[0].short_id}")
        with urllib.request.urlopen(req, timeout=5) as resp:
            self.assertEqual(resp.read(), b"<script>alert(1)</script>")
            self.assertEqual(resp.headers["Content-Type"], "text/plain; charset=utf-8")
            self.assertTrue(resp.headers["Content-Disposition"].startswith("attachment;"))
            self.assertIn("sandbox", resp.headers["Content-Security-Policy"])
            self.assertEqual(resp.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(self.request("/api/rooms/r/attachments/deadbeefdeadbeef")[0], 404)

    def test_recreated_room_is_not_served_from_cache(self):
        achat.Room(self.root, "r").publish("alice", "old message")
        self.assertEqual(self.request("/api/rooms/r")[1]["messages"][0]["body"], "old message")
        import shutil
        shutil.rmtree(self.root / "r")
        self.assertEqual(self.request("/api/rooms/r")[0], 404)
        achat.Room.create(self.root, "r", topic="new", participants=["x", "y"])
        achat.Room(self.root, "r").publish("x", "new message")
        data = self.request("/api/rooms/r")[1]
        self.assertEqual((data["participants"], [m["body"] for m in data["messages"]]), (["x", "y"], ["new message"]))


if __name__ == "__main__":
    unittest.main()
