import * as vscode from "vscode";
import { describeStatus, RoomSummary } from "./core/types";
import { AchatState } from "./state";

export class RoomItem extends vscode.TreeItem {
  constructor(readonly room: RoomSummary) {
    super(room.name, vscode.TreeItemCollapsibleState.Collapsed);
    const closed = !!room.status.closed;
    this.id = `room:${room.name}`;
    this.description = describeStatus(room);
    this.iconPath = new vscode.ThemeIcon(closed ? "pass" : "comment-discussion");
    this.contextValue = closed ? "achatRoomClosed" : "achatRoomOpen";
    const md = new vscode.MarkdownString();
    md.appendMarkdown(`**${room.name}**  \n`);
    md.appendText(`${room.topic || "(no topic)"}\n`);
    md.appendMarkdown(`\n${room.participants.join(" → ")}  \n`);
    md.appendText(closed ? `closed: ${room.status.closed}` : `turn ${room.status.turns}/${room.max_turns}, ${room.status.next} speaks next`);
    this.tooltip = md;
    this.command = { command: "achat.openRoom", title: "Open Conversation", arguments: [room.name] };
  }
}

export class ParticipantItem extends vscode.TreeItem {
  constructor(
    readonly room: RoomSummary,
    readonly participant: string,
  ) {
    super(participant, vscode.TreeItemCollapsibleState.None);
    const open = !room.status.closed;
    this.id = `participant:${room.name}:${participant}`;
    this.description = open && room.status.next === participant ? "speaks next" : undefined;
    this.iconPath = new vscode.ThemeIcon(open && room.status.next === participant ? "debug-stackframe-dot" : "person");
    this.contextValue = open ? "achatParticipantOpen" : "achatParticipantClosed";
    this.tooltip = `Copy the instruction that brings "${participant}" into room "${room.name}"`;
    this.command = { command: "achat.copyJoin", title: "Copy Join Instruction", arguments: [this] };
  }
}

class MessageItem extends vscode.TreeItem {
  constructor(label: string, icon: string, command?: vscode.Command) {
    super(label, vscode.TreeItemCollapsibleState.None);
    this.iconPath = new vscode.ThemeIcon(icon);
    this.command = command;
  }
}

type Node = RoomItem | ParticipantItem | MessageItem;

export class RoomsTreeProvider implements vscode.TreeDataProvider<Node>, vscode.Disposable {
  private readonly changed = new vscode.EventEmitter<Node | undefined>();
  readonly onDidChangeTreeData = this.changed.event;
  private readonly sub: vscode.Disposable;

  constructor(private readonly state: AchatState) {
    this.sub = state.onDidChangeRooms(() => this.changed.fire(undefined));
  }

  getTreeItem(node: Node): vscode.TreeItem {
    return node;
  }

  getChildren(node?: Node): Node[] {
    if (node instanceof RoomItem) {
      return node.room.participants.map((p) => new ParticipantItem(node.room, p));
    }
    if (node) {
      return [];
    }
    if (this.state.error) {
      return [
        new MessageItem(this.state.error, "error", { command: "achat.showLog", title: "Show Log" }),
        new MessageItem("Open achat settings", "gear", {
          command: "workbench.action.openSettings",
          title: "Settings",
          arguments: ["achat."],
        }),
      ];
    }
    // An empty array shows the viewsWelcome content (New Room / Set Up MCP).
    return (this.state.rooms?.rooms ?? []).map((r) => new RoomItem(r));
  }

  dispose(): void {
    this.sub.dispose();
    this.changed.dispose();
  }
}
