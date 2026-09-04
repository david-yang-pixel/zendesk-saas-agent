import { execFile } from 'node:child_process';
import { existsSync } from 'node:fs';
import { promisify } from 'node:util';
import type {
  DocReadArgs,
  DocSearchArgs,
  ToolCallResult,
  WikiGetNodeArgs,
  WikiListNodesArgs,
  WikiListSpacesArgs,
} from './types.js';

const execFileAsync = promisify(execFile);

const ALLOWED_COMMAND_PREFIXES = [
  ['docs', '+fetch'],
  ['wiki', '+space-list'],
  ['wiki', '+node-list'],
  ['wiki', '+node-get'],
  ['docs', '+search'],
  ['whoami'],
];

const FORBIDDEN_TOKENS = new Set([
  '--yes',
  '--force',
  '--overwrite',
  '+create',
  '+update',
  '+delete',
  '+delete-space',
  '+node-delete',
  '+node-create',
  '+node-copy',
  '+space-create',
  '+member-add',
  '+member-remove',
  '+move',
  '+move-to-drive',
  '+history-revert',
  '+resource-delete',
  '+resource-update',
  '+media-insert',
  '+media-upload',
  '+whiteboard-update',
]);

export function findLarkCliPath(): string {
  if (process.env.LARK_CLI_PATH && existsSync(process.env.LARK_CLI_PATH)) {
    return process.env.LARK_CLI_PATH;
  }
  const candidates = [
    '/usr/local/bin/lark-cli',
    '/usr/bin/lark-cli',
    '/usr/local/lib/node_modules/@larksuite/cli/bin/lark-cli',
    'lark-cli',
  ];
  for (const p of candidates) {
    if (p === 'lark-cli' || existsSync(p)) {
      return p;
    }
  }
  return 'lark-cli';
}

export function validateReadOnlyCommand(args: string[]): void {
  for (const arg of args) {
    if (FORBIDDEN_TOKENS.has(arg)) {
      throw new Error(`Forbidden argument in read-only mode: ${arg}`);
    }
    if (arg.startsWith('--yes') || arg.startsWith('--force') || arg.startsWith('--overwrite')) {
      throw new Error(`Write mutation flags not permitted: ${arg}`);
    }
  }

  const matchesAllowed = ALLOWED_COMMAND_PREFIXES.some((prefix) => {
    return prefix.every((token, idx) => args[idx] === token);
  });

  if (!matchesAllowed) {
    throw new Error(`Command not permitted in read-only mode: ${args.join(' ')}`);
  }
}

export async function runLarkCli(args: string[]): Promise<{ stdout: string; stderr: string }> {
  validateReadOnlyCommand(args);

  const binPath = findLarkCliPath();
  const timeout = 30000;

  try {
    const { stdout, stderr } = await execFileAsync(binPath, args, {
      timeout,
      maxBuffer: 16 * 1024 * 1024,
      env: {
        ...process.env,
        LANG: 'en_US.UTF-8',
        LC_ALL: 'en_US.UTF-8',
      },
    });
    return { stdout: stdout.trim(), stderr: stderr.trim() };
  } catch (err: any) {
    const stdout = (err.stdout || '').toString().trim();
    const stderr = (err.stderr || '').toString().trim();
    const exitCode = err.code || err.status || 1;

    // lark-cli prints JSON error to stdout even when exit code is non-zero
    if (stdout) {
      return { stdout, stderr };
    }
    throw new Error(`lark-cli exited with code ${exitCode}: ${stderr || err.message}`);
  }
}

export async function handleDocRead(args: DocReadArgs): Promise<ToolCallResult> {
  const docToken = (args.doc_token || '').trim();
  if (!docToken) {
    return {
      content: [{ type: 'text', text: 'Error: doc_token is required' }],
      isError: true,
    };
  }

  const format = args.doc_format || 'markdown';
  const scope = args.scope || 'full';

  const cmdArgs = [
    'docs',
    '+fetch',
    '--doc',
    docToken,
    '--doc-format',
    format,
    '--scope',
    scope,
    '--as',
    'bot',
    '--json',
  ];

  if (args.keyword && scope === 'keyword') {
    cmdArgs.push('--keyword', args.keyword);
  }

  const { stdout } = await runLarkCli(cmdArgs);
  return {
    content: [{ type: 'text', text: stdout }],
    isError: false,
  };
}

export async function handleWikiListSpaces(args: WikiListSpacesArgs): Promise<ToolCallResult> {
  const cmdArgs = ['wiki', '+space-list', '--as', 'bot', '--json'];

  if (args.page_token) {
    cmdArgs.push('--page-token', args.page_token);
  }
  if (args.page_size && args.page_size > 0) {
    cmdArgs.push('--page-size', String(Math.min(args.page_size, 50)));
  }

  const { stdout } = await runLarkCli(cmdArgs);
  return {
    content: [{ type: 'text', text: stdout }],
    isError: false,
  };
}

export async function handleWikiListNodes(args: WikiListNodesArgs): Promise<ToolCallResult> {
  const spaceId = (args.space_id || '').trim();
  if (!spaceId) {
    return {
      content: [{ type: 'text', text: 'Error: space_id is required' }],
      isError: true,
    };
  }

  const cmdArgs = ['wiki', '+node-list', '--space-id', spaceId, '--as', 'bot', '--json'];

  if (args.parent_node_token) {
    cmdArgs.push('--parent-node-token', args.parent_node_token);
  }
  if (args.page_token) {
    cmdArgs.push('--page-token', args.page_token);
  }
  if (args.page_size && args.page_size > 0) {
    cmdArgs.push('--page-size', String(Math.min(args.page_size, 50)));
  }

  const { stdout } = await runLarkCli(cmdArgs);
  return {
    content: [{ type: 'text', text: stdout }],
    isError: false,
  };
}

export async function handleWikiGetNode(args: WikiGetNodeArgs): Promise<ToolCallResult> {
  const nodeToken = (args.node_token || '').trim();
  if (!nodeToken) {
    return {
      content: [{ type: 'text', text: 'Error: node_token is required' }],
      isError: true,
    };
  }

  const cmdArgs = ['wiki', '+node-get', '--node-token', nodeToken, '--as', 'bot', '--json'];

  if (args.obj_type) {
    cmdArgs.push('--obj-type', args.obj_type);
  }

  const { stdout } = await runLarkCli(cmdArgs);
  return {
    content: [{ type: 'text', text: stdout }],
    isError: false,
  };
}

export async function handleDocSearch(args: DocSearchArgs): Promise<ToolCallResult> {
  const query = (args.query || '').trim();
  if (!query) {
    return {
      content: [{ type: 'text', text: 'Error: query is required' }],
      isError: true,
    };
  }

  const cmdArgs = ['docs', '+search', '--query', query, '--json'];

  if (args.page_token) {
    cmdArgs.push('--page-token', args.page_token);
  }
  if (args.page_size && args.page_size > 0) {
    cmdArgs.push('--page-size', String(Math.min(args.page_size, 20)));
  }

  const { stdout } = await runLarkCli(cmdArgs);
  return {
    content: [{ type: 'text', text: stdout }],
    isError: false,
  };
}
