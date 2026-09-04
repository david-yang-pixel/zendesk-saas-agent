import {
  handleDocRead,
  handleDocSearch,
  handleWikiGetNode,
  handleWikiListNodes,
  handleWikiListSpaces,
} from './lark.js';
import type { ToolDefinition, ToolCallResult } from './types.js';

export const FEISHU_TOOLS: ToolDefinition[] = [
  {
    name: 'feishu_doc_read',
    description:
      'Fetch and read the content of a Feishu (Lark) document or DocX in Markdown or XML format using read-only bot credentials.',
    inputSchema: {
      type: 'object',
      properties: {
        doc_token: {
          type: 'string',
          description: 'Document URL or token (e.g. docx token or full Feishu doc URL)',
        },
        doc_format: {
          type: 'string',
          enum: ['markdown', 'xml', 'im-markdown'],
          description: 'Output format (default: markdown)',
        },
        scope: {
          type: 'string',
          enum: ['full', 'outline', 'keyword', 'range', 'section'],
          description: 'Read scope (default: full). Use "outline" for headings or "keyword" to filter sections.',
        },
        keyword: {
          type: 'string',
          description: 'Keyword filter query when scope is "keyword"',
        },
      },
      required: ['doc_token'],
    },
  },
  {
    name: 'feishu_wiki_list_spaces',
    description:
      'List all Feishu (Lark) Wiki knowledge spaces accessible to the bot.',
    inputSchema: {
      type: 'object',
      properties: {
        page_token: {
          type: 'string',
          description: 'Pagination token from previous page response',
        },
        page_size: {
          type: 'number',
          description: 'Number of spaces to return per page (1-50, default: 50)',
        },
      },
    },
  },
  {
    name: 'feishu_wiki_list_nodes',
    description:
      'List Feishu Wiki catalog nodes under a space or parent node (read-only).',
    inputSchema: {
      type: 'object',
      properties: {
        space_id: {
          type: 'string',
          description: 'The Wiki space ID',
        },
        parent_node_token: {
          type: 'string',
          description: 'Optional parent node token. If omitted, lists root-level nodes.',
        },
        page_token: {
          type: 'string',
          description: 'Pagination token from previous page response',
        },
        page_size: {
          type: 'number',
          description: 'Number of nodes to return per page (1-50, default: 50)',
        },
      },
      required: ['space_id'],
    },
  },
  {
    name: 'feishu_wiki_get_node',
    description:
      'Get details of a Feishu Wiki node by node_token, obj_token, or Lark URL.',
    inputSchema: {
      type: 'object',
      properties: {
        node_token: {
          type: 'string',
          description: 'Wiki node_token, obj_token, or Feishu Wiki document URL',
        },
        obj_type: {
          type: 'string',
          description: 'Optional object type (doc, docx, sheet, bitable, mindnote, slides, file)',
        },
      },
      required: ['node_token'],
    },
  },
  {
    name: 'feishu_doc_search',
    description:
      'Search Feishu documents, Wiki, and spreadsheets by keyword (read-only query).',
    inputSchema: {
      type: 'object',
      properties: {
        query: {
          type: 'string',
          description: 'Search keyword',
        },
        page_token: {
          type: 'string',
          description: 'Pagination token',
        },
        page_size: {
          type: 'number',
          description: 'Page size (1-20, default: 15)',
        },
      },
      required: ['query'],
    },
  },
];

export function getToolDefinitions(): ToolDefinition[] {
  return FEISHU_TOOLS;
}

export async function executeTool(name: string, args: any): Promise<ToolCallResult> {
  switch (name) {
    case 'feishu_doc_read':
      return await handleDocRead(args || {});
    case 'feishu_wiki_list_spaces':
      return await handleWikiListSpaces(args || {});
    case 'feishu_wiki_list_nodes':
      return await handleWikiListNodes(args || {});
    case 'feishu_wiki_get_node':
      return await handleWikiGetNode(args || {});
    case 'feishu_doc_search':
      return await handleDocSearch(args || {});
    default:
      throw new Error(`Tool not found or not allowed in read-only mode: ${name}`);
  }
}
