import { registerHandler } from '@jboltai/tokui';

interface MCPClient {
  callTool(toolName: string, args: Record<string, unknown>): Promise<unknown>;
}

let _mcpClient: MCPClient | null = null;

export function setMCPClient(client: MCPClient): void {
  _mcpClient = client;
}

async function callMCP(toolName: string, args: Record<string, unknown>): Promise<unknown> {
  if (!_mcpClient) {
    console.warn(`[GalaxyOS] MCP client not set, cannot call ${toolName}`);
    return null;
  }
  return _mcpClient.callTool(toolName, args);
}

export function registerGalaxyOSEventHandlers(): { registered: string[]; failed: string[] } {
  const registered: string[] = [];
  const failed: string[] = [];

  const handlers: Record<string, (...args: unknown[]) => unknown> = {
    'clk:memory-search': async (query: unknown, filterType?: unknown) => {
      try {
        return await callMCP('memory_recall', {
          query: String(query),
          top_k: 10,
          semantic_enhancement: true,
          dag_context: true,
          workspace_id: String(filterType || 'default'),
        });
      } catch (e) {
        console.warn('[GalaxyOS] memory-search handler failed:', e);
        return null;
      }
    },

    'clk:rccam-pause': async () => {
      try {
        return await callMCP('claw_rccam', { action: 'pause' });
      } catch (e) {
        console.warn('[GalaxyOS] rccam-pause handler failed:', e);
        return null;
      }
    },

    'clk:rccam-resume': async () => {
      try {
        return await callMCP('claw_rccam', { action: 'resume' });
      } catch (e) {
        console.warn('[GalaxyOS] rccam-resume handler failed:', e);
        return null;
      }
    },

    'clk:rccam-depth': async (depth: unknown) => {
      try {
        return await callMCP('claw_rccam', { action: 'depth', value: Number(depth) });
      } catch (e) {
        console.warn('[GalaxyOS] rccam-depth handler failed:', e);
        return null;
      }
    },

    'clk:rccam-strategy': async (strategy: unknown) => {
      try {
        return await callMCP('claw_rccam', { action: 'strategy', value: String(strategy) });
      } catch (e) {
        console.warn('[GalaxyOS] rccam-strategy handler failed:', e);
        return null;
      }
    },

    'clk:dag-expand': async (nodeId: unknown) => {
      try {
        return await callMCP('claw_node_invoke', { action: 'expand', params: { node_id: String(nodeId) } });
      } catch (e) {
        console.warn('[GalaxyOS] dag-expand handler failed:', e);
        return null;
      }
    },

    'clk:dag-collapse': async (nodeId: unknown) => {
      try {
        return await callMCP('claw_node_invoke', { action: 'collapse', params: { node_id: String(nodeId) } });
      } catch (e) {
        console.warn('[GalaxyOS] dag-collapse handler failed:', e);
        return null;
      }
    },

    'clk:dag-summary': async (nodeId: unknown) => {
      try {
        return await callMCP('claw_node_invoke', { action: 'summary', params: { node_id: String(nodeId) } });
      } catch (e) {
        console.warn('[GalaxyOS] dag-summary handler failed:', e);
        return null;
      }
    },

    'sub:memory-filter': (filterType: unknown) => {
      // Frontend-only filter, no MCP call needed
      return { filtered: true, type: String(filterType) };
    },
  };

  for (const [name, handler] of Object.entries(handlers)) {
    try {
      registerHandler(name, handler);
      registered.push(name);
    } catch (e) {
      console.warn(`[GalaxyOS] Failed to register event handler "${name}":`, e);
      failed.push(name);
    }
  }

  return { registered, failed };
}

export default registerGalaxyOSEventHandlers;