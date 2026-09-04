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
  ['drive', '+search'],
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
    const output = stdout || stderr;
    if (output) {
      return { stdout: output, stderr };
    }
    const exitCode = err.code || err.status || 1;
    throw new Error(`lark-cli exited with code ${exitCode}: ${stderr || err.message}`);
  }
}

export function formatToolOutput(rawOutput: string): ToolCallResult {
  let isError = false;
  try {
    const parsed = JSON.parse(rawOutput);
    if (parsed && (parsed.ok === false || parsed.error || (parsed.code !== undefined && parsed.code !== 0))) {
      isError = true;
    }
  } catch {
    // Non-JSON output (e.g. plain text or markdown)
  }
  return {
    content: [{ type: 'text', text: rawOutput }],
    isError,
  };
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
  return formatToolOutput(stdout);
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
  return formatToolOutput(stdout);
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
  return formatToolOutput(stdout);
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
  return formatToolOutput(stdout);
}

const DEFAULT_SAAS_FOLDERS = [
  'MGC0fbkWwloYg3dmI6pc5oWLnVe', // Root SaaS知识库
  'AVkLfqLmmlCFAIdjX6yc1z1pnYd', // 01-产品功能
  'T5AffWkitlimvkdWlvHczIZbnYe', // 应用与工作流
  'Cm5RfA9yPljnv1dG643cKVeFnHg', // API与集成
  'PI1AfGzUel50TadiAu1c7muBnYb', // 模型与插件
  'FZdcfv0YnljNSLdbvOScyvtJnyg', // 知识库与RAG
  'Hl66fzSADlKYjfdaBTucXkEonFg', // 02-运行与排障
  'Hqr4ffyHflmyCQdOCIecKCasnie', // 异常与恢复
  'S1WjfyBj3lfYJkd6DQGcfCDunkh', // 执行与性能
  'MwHOfYhSVlY0fydSkUycURTtnZb', // 可观测性与日志
  'G4sFfTE4mlZN3gd5eTucDjgFnSg', // 03-账号与商业
  'XgN1fksWbl5l6udry6mcQrONnKb', // 工作空间与权限
  'H28RfJVZKl8glWdpBhPcWNSln9f', // 套餐账单与配额
  'UX3Tf0hX3ljBAbdTiz4cefJcnuf', // 04-使用指南与SOP
];

export async function handleDocSearch(args: DocSearchArgs): Promise<ToolCallResult> {
  const query = (args.query || '').trim();
  if (!query) {
    return {
      content: [{ type: 'text', text: 'Error: query is required' }],
      isError: true,
    };
  }

  const folderTokens = process.env.FEISHU_FOLDER_TOKENS || DEFAULT_SAAS_FOLDERS.join(',');
  const cmdArgs = [
    'drive',
    '+search',
    '--query',
    query,
    '--folder-tokens',
    folderTokens,
    '--as',
    'bot',
    '--json',
  ];

  if (args.page_token) {
    cmdArgs.push('--page-token', args.page_token);
  }
  if (args.page_size && args.page_size > 0) {
    cmdArgs.push('--page-size', String(Math.min(args.page_size, 20)));
  }

  const { stdout } = await runLarkCli(cmdArgs);
  return formatToolOutput(stdout);
}
