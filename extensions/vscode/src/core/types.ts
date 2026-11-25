// Shapes of `achat.py ... --json` output. achat.py owns the protocol; this file only
// mirrors its JSON contract (tested on the Python side in TestJsonContract).

export interface RoomStatus {
  turns: number;
  next: string;
  closed: string | null;
  last_seq: number;
  /** Agents currently proposing to end (achat ≥ 0.3). */
  end_proposed_by: string[];
}

export interface AttachmentInfo {
  sha256: string;
  name: string;
  size: number;
  type: string;
  /** First lines of a text file; null for binary files. */
  preview: string | null;
  lines: number | null;
}

export interface ChatMessage {
  seq: number;
  from: string;
  ts: string;
  end: boolean;
  /** An agent proposed ending with this message (achat ≥ 0.3). */
  end_proposal: boolean;
  body: string;
  attachments: AttachmentInfo[];
}

export interface Onboarding {
  shell: string;
  mcp: string;
  bridge: string;
}

export interface RoomSummary {
  name: string;
  topic: string;
  participants: string[];
  max_turns: number;
  max_chars: number;
  brief: string;
  created: string;
  status: RoomStatus;
}

export interface RoomDetail extends RoomSummary {
  messages: ChatMessage[];
  onboarding: Record<string, Onboarding>;
}

export interface RoomList {
  achat_version: string;
  rooms_dir: string;
  rooms: RoomSummary[];
}

export class ProtocolError extends Error {}

function isObj(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function need<T>(ok: boolean, what: string, value: T): T {
  if (!ok) {
    throw new ProtocolError(`unexpected achat output: ${what}`);
  }
  return value;
}

function parseStatus(v: unknown): RoomStatus {
  need(isObj(v), "status", v);
  const s = v as Record<string, unknown>;
  return {
    turns: need(typeof s.turns === "number", "status.turns", s.turns as number),
    next: need(typeof s.next === "string", "status.next", s.next as string),
    closed: need(s.closed === null || typeof s.closed === "string", "status.closed", s.closed as string | null),
    last_seq: need(typeof s.last_seq === "number", "status.last_seq", s.last_seq as number),
    end_proposed_by: Array.isArray(s.end_proposed_by) ? s.end_proposed_by.filter((x): x is string => typeof x === "string") : [],
  };
}

function parseSummary(v: unknown): RoomSummary {
  need(isObj(v), "room", v);
  const r = v as Record<string, unknown>;
  const participants = need(
    Array.isArray(r.participants) && r.participants.every((p) => typeof p === "string"),
    "room.participants",
    r.participants as string[],
  );
  return {
    name: need(typeof r.name === "string", "room.name", r.name as string),
    topic: typeof r.topic === "string" ? r.topic : "",
    participants,
    max_turns: need(typeof r.max_turns === "number", "room.max_turns", r.max_turns as number),
    max_chars: need(typeof r.max_chars === "number", "room.max_chars", r.max_chars as number),
    brief: typeof r.brief === "string" ? r.brief : "",
    created: typeof r.created === "string" ? r.created : "",
    status: parseStatus(r.status),
  };
}

function parseMessage(v: unknown): ChatMessage {
  need(isObj(v), "message", v);
  const m = v as Record<string, unknown>;
  return {
    seq: need(typeof m.seq === "number", "message.seq", m.seq as number),
    from: need(typeof m.from === "string", "message.from", m.from as string),
    ts: typeof m.ts === "string" ? m.ts : "",
    end: m.end === true,
    end_proposal: m.end_proposal === true,
    body: need(typeof m.body === "string", "message.body", m.body as string),
    attachments: (Array.isArray(m.attachments) ? m.attachments : []).filter(isObj).flatMap((a) =>
      typeof a.sha256 === "string" && /^[0-9a-f]{64}$/.test(a.sha256) && typeof a.name === "string" && typeof a.size === "number"
        ? [{
            sha256: a.sha256,
            name: a.name,
            size: a.size,
            type: typeof a.type === "string" ? a.type : "application/octet-stream",
            preview: typeof a.preview === "string" ? a.preview : null,
            lines: typeof a.lines === "number" ? a.lines : null,
          }]
        : [],
    ),
  };
}

export function parseRoomList(v: unknown): RoomList {
  need(isObj(v), "list", v);
  const l = v as Record<string, unknown>;
  return {
    achat_version: typeof l.achat_version === "string" ? l.achat_version : "",
    rooms_dir: need(typeof l.rooms_dir === "string", "rooms_dir", l.rooms_dir as string),
    rooms: need(Array.isArray(l.rooms), "rooms", l.rooms as unknown[]).map(parseSummary),
  };
}

export function parseRoomDetail(v: unknown): RoomDetail {
  const summary = parseSummary(v);
  const r = v as Record<string, unknown>;
  const onboarding: Record<string, Onboarding> = {};
  if (isObj(r.onboarding)) {
    for (const [who, how] of Object.entries(r.onboarding)) {
      if (isObj(how) && typeof how.shell === "string" && typeof how.mcp === "string" && typeof how.bridge === "string") {
        onboarding[who] = { shell: how.shell, mcp: how.mcp, bridge: how.bridge };
      }
    }
  }
  return {
    ...summary,
    messages: need(Array.isArray(r.messages), "messages", r.messages as unknown[]).map(parseMessage),
    onboarding,
  };
}

export function describeStatus(room: RoomSummary): string {
  if (room.status.closed) {
    return "closed";
  }
  return `turn ${room.status.turns}/${room.max_turns} · ${room.status.next}'s turn`;
}
