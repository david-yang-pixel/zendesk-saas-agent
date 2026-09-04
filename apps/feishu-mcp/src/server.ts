import { createServer as createHttpServer, IncomingMessage, ServerResponse } from 'node:http';
import { executeTool, getToolDefinitions } from './tools.js';
import type { JsonRpcRequest, JsonRpcResponse } from './types.js';

export const DEFAULT_PORT = 8115;

function parseJsonBody(req: IncomingMessage): Promise<any> {
  return new Promise((resolve, reject) => {
    let body = '';
    req.setEncoding('utf8');
    req.on('data', (chunk) => {
      body += chunk;
      if (body.length > 10 * 1024 * 1024) {
        reject(new Error('Request body too large'));
      }
    });
    req.on('end', () => {
      if (!body.trim()) {
        resolve({});
        return;
      }
      try {
        resolve(JSON.parse(body));
      } catch (err: any) {
        reject(new Error(`Invalid JSON: ${err.message}`));
      }
    });
    req.on('error', reject);
  });
}

function verifyAuth(req: IncomingMessage): boolean {
  const expectedKey = (process.env.FEISHU_MCP_API_KEY || '').trim();
  if (!expectedKey) {
    // If no API key configured, allow internal requests
    return true;
  }
  const authHeader = req.headers.authorization || '';
  if (!authHeader.startsWith('Bearer ')) {
    return false;
  }
  const token = authHeader.slice(7).trim();
  return token === expectedKey;
}

export function createServer() {
  return createHttpServer(async (req: IncomingMessage, res: ServerResponse) => {
    const url = new URL(req.url || '/', `http://${req.headers.host || 'localhost'}`);

    // Health check endpoints
    if (url.pathname === '/readyz' || url.pathname === '/healthz') {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ status: 'ready', service: 'feishu-mcp', tools: getToolDefinitions().length }));
      return;
    }

    // MCP JSON-RPC endpoint (supports /mcp and /)
    if ((url.pathname === '/mcp' || url.pathname === '/') && req.method === 'POST') {
      if (!verifyAuth(req)) {
        res.writeHead(401, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: 'Unauthorized: Invalid or missing Bearer token' }));
        return;
      }

      try {
        const body: JsonRpcRequest = await parseJsonBody(req);
        const reqId = body.id !== undefined ? body.id : 1;

        if (body.method === 'tools/list') {
          const response: JsonRpcResponse = {
            jsonrpc: '2.0',
            id: reqId,
            result: {
              tools: getToolDefinitions(),
            },
          };
          res.writeHead(200, { 'Content-Type': 'application/json' });
          res.end(JSON.stringify(response));
          return;
        }

        if (body.method === 'tools/call') {
          const toolName = body.params?.name;
          const toolArgs = body.params?.arguments || {};

          if (!toolName) {
            const response: JsonRpcResponse = {
              jsonrpc: '2.0',
              id: reqId,
              error: {
                code: -32602,
                message: 'Invalid params: name is required',
              },
            };
            res.writeHead(400, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify(response));
            return;
          }

          try {
            const toolResult = await executeTool(toolName, toolArgs);
            const response: JsonRpcResponse = {
              jsonrpc: '2.0',
              id: reqId,
              result: toolResult,
            };
            res.writeHead(200, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify(response));
            return;
          } catch (execErr: any) {
            const response: JsonRpcResponse = {
              jsonrpc: '2.0',
              id: reqId,
              error: {
                code: -32000,
                message: execErr.message || 'Tool execution failed',
              },
            };
            res.writeHead(200, { 'Content-Type': 'application/json' });
            res.end(JSON.stringify(response));
            return;
          }
        }

        const response: JsonRpcResponse = {
          jsonrpc: '2.0',
          id: reqId,
          error: {
            code: -32601,
            message: `Method not found: ${body.method}`,
          },
        };
        res.writeHead(404, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify(response));
      } catch (err: any) {
        res.writeHead(400, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: err.message || 'Bad Request' }));
      }
      return;
    }

    res.writeHead(404, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ error: 'Not Found' }));
  });
}

const isDirectRun = process.argv[1]?.endsWith('server.js') || process.argv[1]?.endsWith('server.ts');
if (isDirectRun) {
  const port = parseInt(process.env.FEISHU_MCP_PORT || process.env.PORT || String(DEFAULT_PORT), 10);
  const server = createServer();
  server.listen(port, '0.0.0.0', () => {
    console.log(`[Feishu MCP] Server listening on http://0.0.0.0:${port}/mcp (health: /readyz)`);
  });

  const shutdown = () => {
    console.log('[Feishu MCP] Shutting down server...');
    server.close(() => {
      process.exit(0);
    });
  };
  process.on('SIGINT', shutdown);
  process.on('SIGTERM', shutdown);
}
