import * as vscode from "vscode";
import { registerCommands } from "./commands";
import { registerNativeMcp } from "./mcp";
import { PanelManager } from "./panel";
import { RoomsTreeProvider } from "./roomsTree";
import { AchatState } from "./state";

/** Returned from activate() so integration tests can inspect the extension's state. */
export interface AchatApi {
  state: AchatState;
  panels: PanelManager;
  tree: RoomsTreeProvider;
  nativeMcp: boolean;
}

export async function activate(context: vscode.ExtensionContext): Promise<AchatApi> {
  const log = vscode.window.createOutputChannel("achat");
  const state = new AchatState(context, log);
  const panels = new PanelManager(context, state);
  const tree = new RoomsTreeProvider(state);
  context.subscriptions.push(log, state, panels, tree, vscode.window.createTreeView("achat.rooms", { treeDataProvider: tree }));
  registerCommands(context, state, panels);
  await state.init();
  const nativeMcp = registerNativeMcp(context, state);
  return { state, panels, tree, nativeMcp };
}

export function deactivate(): void {}
