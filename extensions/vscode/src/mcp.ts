import * as vscode from "vscode";
import { AchatState } from "./state";

/**
 * Registers achat as an MCP server with the editor's own agent (VS Code ≥ 1.101).
 * Feature-detected: forks on an older VS Code base simply don't get it, and users there
 * use "achat: Set Up MCP for Agents" instead. Typed loosely on purpose, because
 * @types/vscode is pinned to the oldest engine we support.
 */
export function registerNativeMcp(context: vscode.ExtensionContext, state: AchatState): boolean {
  const api = vscode as unknown as {
    lm?: { registerMcpServerDefinitionProvider?: (id: string, provider: unknown) => vscode.Disposable };
    McpStdioServerDefinition?: new (label: string, command: string, args?: string[], env?: Record<string, string>, version?: string) => unknown;
  };
  const register = api.lm?.registerMcpServerDefinitionProvider;
  const Definition = api.McpStdioServerDefinition;
  if (!register || !Definition) {
    state.log.appendLine("This editor has no native MCP provider API; use “achat: Set Up MCP for Agents”.");
    return false;
  }
  const changed = new vscode.EventEmitter<void>();
  context.subscriptions.push(
    changed,
    state.onDidChangeConfig(() => changed.fire()),
    register.call(api.lm, "achat", {
      onDidChangeMcpServerDefinitions: changed.event,
      provideMcpServerDefinitions: () => {
        const cli = state.cli;
        return cli ? [new Definition("achat", cli.config.python, cli.mcpArgs(), {}, "0.2.0")] : [];
      },
      resolveMcpServerDefinition: (def: unknown) => def,
    }),
  );
  state.log.appendLine("Registered achat as an MCP server for this editor's agent mode.");
  return true;
}
