export interface ToolDefinition {
  name: string;
  description: string;
  inputSchema: {
    type: 'object';
    properties: Record<string, any>;
    required?: string[];
  };
}

export interface ToolCallResult {
  content: Array<{
    type: 'text';
    text: string;
  }>;
  isError?: boolean;
}

export interface JsonRpcRequest {
  jsonrpc: '2.0';
  id?: string | number | null;
  method: string;
  params?: any;
}

export interface JsonRpcResponse {
  jsonrpc: '2.0';
  id: string | number | null;
  result?: any;
  error?: {
    code: number;
    message: string;
    data?: any;
  };
}

export interface DocReadArgs {
  doc_token: string;
  doc_format?: 'markdown' | 'xml' | 'im-markdown';
  scope?: 'full' | 'outline' | 'keyword' | 'range' | 'section';
  keyword?: string;
}

export interface WikiListSpacesArgs {
  page_token?: string;
  page_size?: number;
}

export interface WikiListNodesArgs {
  space_id: string;
  parent_node_token?: string;
  page_token?: string;
  page_size?: number;
}

export interface WikiGetNodeArgs {
  node_token: string;
  obj_type?: string;
}

export interface DocSearchArgs {
  query: string;
  page_token?: string;
  page_size?: number;
}
