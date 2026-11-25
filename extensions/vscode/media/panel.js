// Webview script for one conversation. Agent text is untrusted: render with textContent only.
(function () {
  "use strict";
  const vscode = window.__achatApi || acquireVsCodeApi(); // the inline bootstrap already acquired it
  const $ = (id) => document.getElementById(id);
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };
  let room = null;
  let sending = false; // a fast double Enter must not post twice

  function color(name) {
    if (!room || name === "human") return "var(--achat-human)";
    const i = room.participants.indexOf(name);
    return i < 0 ? "var(--achat-human)" : `var(--achat-p${i % 6})`;
  }

  function fmtTime(ts) {
    const d = new Date(ts);
    return isNaN(d.getTime()) ? ts : d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  function renderHead() {
    const h = $("head");
    const closed = !!room.status.closed;
    const title = el("div", "title");
    title.append(el("h1", null, room.name), el("span", "pill" + (closed ? " closed" : ""),
      closed ? `closed · ${room.status.closed}` : `turn ${room.status.turns}/${room.max_turns} · ${room.status.next}'s turn` +
        ((room.status.end_proposed_by || []).length ? ` · ${room.status.end_proposed_by.join(" and ")} proposed to end` : "")));
    h.replaceChildren(title, el("p", "topic", room.topic || "(no topic)"));

    const people = el("div", "people");
    room.participants.forEach((p, i) => {
      if (i) people.append(el("span", "arrow", "→"));
      const chip = el("span", "chip" + (!closed && room.status.next === p ? " next" : ""));
      chip.style.setProperty("--c", color(p));
      chip.append(el("span", "name", p));
      if (!closed) {
        const menu = el("span", "tools");
        for (const [kind, label, tip] of [["shell", "join", "Copy the instruction for an agent with a terminal"], ["mcp", "mcp", "Copy the instruction for an agent with achat's MCP server"]]) {
          const b = el("button", "link", label);
          b.title = tip;
          b.addEventListener("click", () => vscode.postMessage({ type: "copy", participant: p, kind }));
          menu.append(b);
        }
        const br = el("button", "link", "bridge");
        br.title = "Run a local model (Ollama, LM Studio) as this participant";
        br.addEventListener("click", () => vscode.postMessage({ type: "bridge", participant: p }));
        menu.append(br);
        chip.append(menu);
      }
      people.append(chip);
    });
    h.append(people);
    if (room.brief) {
      const det = el("details");
      det.append(el("summary", null, "Brief"), el("pre", null, room.brief));
      h.append(det);
    }
    for (const id of ["text", "send", "end"]) $(id).disabled = closed;
    $("text").placeholder = closed ? "This room is closed." : "Interject as human (Enter sends, Shift+Enter for a new line)";
  }

  function appendMessages(msgs, reset) {
    const s = $("stream");
    const nearBottom = s.scrollHeight - s.scrollTop - s.clientHeight < 120;
    if (reset) s.replaceChildren();
    const empty = document.getElementById("empty");
    if (msgs.length && empty) empty.remove();
    for (const m of msgs) {
      const box = el("article", "msg" + (m.from === "human" ? " human" : "") + (m.end ? " end" : ""));
      box.style.setProperty("--c", color(m.from));
      const who = el("div", "who");
      who.append(el("span", "from", m.from), el("span", "meta", `#${m.seq} · ${fmtTime(m.ts)}`));
      if (m.end) who.append(el("span", "ended", "closed the conversation"));
      else if (m.end_proposal) who.append(el("span", "proposal", "proposes to end"));
      box.append(who, el("div", "body", m.body));
      s.append(box);
    }
    if (!s.children.length) {
      const e = el("div", "empty", `Empty room. Waiting for ${room.participants[0]} to open the conversation…`);
      e.id = "empty";
      s.append(e);
    }
    if (reset || (msgs.length && nearBottom)) s.scrollTop = s.scrollHeight;
  }

  window.addEventListener("error", (e) => vscode.postMessage({ type: "webviewError", message: String(e.message) }));

  window.addEventListener("message", (ev) => {
    const m = ev.data;
    if (m.type === "update") {
      $("err").textContent = "";
      room = m.room;
      renderHead();
      appendMessages(m.messages, m.reset);
      vscode.postMessage({ type: "rendered", messages: document.querySelectorAll("#stream .msg").length });
    } else if (m.type === "sent") {
      $("text").value = "";
      done();
    } else if (m.type === "cancelled") {
      done(); // the user declined to close the room: keep the text
    } else if (m.type === "error") {
      $("err").textContent = m.message;
      done();
    }
  });

  function done() {
    sending = false;
    $("send").disabled = !!(room && room.status.closed);
  }

  function post(end) {
    if (sending) return;
    const text = $("text").value.trim() || (end ? "The human supervisor closed the conversation." : "");
    if (!text) return;
    sending = true;
    $("send").disabled = true;
    vscode.postMessage({ type: "send", text, end });
  }

  $("composer").addEventListener("submit", (e) => { e.preventDefault(); post(false); });
  $("end").addEventListener("click", () => post(true));
  $("text").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); post(false); }
  });
  vscode.postMessage({ type: "ready" });
})();
