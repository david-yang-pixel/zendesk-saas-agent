import type { CustomTool } from './mcp.js';
import { normalizeToolResult } from './tool-result.js';

export interface EvidenceCheck {
  id: string;
  question: string;
  source: string;
  kind: 'record' | 'document' | 'discussion';
  status: 'pending' | 'verified' | 'unresolved' | 'not_needed';
  callIds?: string[];
  explanation?: string;
}
type CheckUpdate = Partial<EvidenceCheck> & { id: string };
type ObservedCall = { toolCallId: string; toolName: string; arguments?: any; result?: any; status?: string };
type Observation = {
  id: string;
  source: string;
  action: string;
  status: string;
  empty: boolean;
  locator: boolean;
  content: boolean;
  unavailable: boolean;
  fingerprint: string;
};
export type EvidencePhase = 'not_queried' | 'failed' | 'search_empty' | 'candidate_unread' | 'unreviewed' | 'verified' | 'unresolved' | 'not_needed';

function businessTarget(call: ObservedCall): { source: string; action: string; input: any } | undefined {
  const args = call.arguments ?? {};
  if (call.toolName !== 'execute_action' || typeof args.actionId !== 'string' || args.actionId === 'custom_mcp.list_tools') return;
  if (args.actionId === 'custom_mcp.call_tool') {
    const rawToolName = args.input?.toolName ?? args.input?.tool ?? args.input?.name;
    if (!args.connectionName || typeof rawToolName !== 'string') return;
    let inputArgs = args.input?.arguments;
    if (!inputArgs || typeof inputArgs !== 'object') {
      const { toolName: _tn, tool: _t, name: _n, pagination: _p, ...inlineArgs } = args.input ?? {};
      inputArgs = Object.keys(inlineArgs).length > 0 ? inlineArgs : {};
    }
    return { source: args.connectionName, action: rawToolName, input: inputArgs };
  }
  const service = args.actionId.split('.')[0];
  return { source: args.connectionName && args.connectionName !== 'default' ? `${service}/${args.connectionName}` : service,
    action: args.actionId.slice(service.length + 1), input: args.input ?? {} };
}

function isEmpty(data: any): boolean {
  if (data == null || data === '') return true;
  if (Array.isArray(data)) return data.length === 0;
  if (typeof data !== 'object') return false;
  const arrays = ['results', 'items', 'documents', 'messages', 'records', 'rows', 'hits', 'issues', 'nodes']
    .filter(key => Array.isArray(data[key])).map(key => data[key]);
  if (arrays.length) return arrays.every(values => values.length === 0);
  return Object.keys(data).length === 0 || data.total === 0 || data.row_count === 0;
}

function hasContent(data: any, depth = 0): boolean {
  if (depth > 5 || data == null) return false;
  if (typeof data === 'string') return data.trim().length > 0;
  if (Array.isArray(data)) return data.some(value => hasContent(value, depth + 1));
  if (typeof data !== 'object') return false;
  return Object.entries(data).some(([key, value]) =>
    (/^(text|plain_text|markdown|content|body|description|blocks|messages|comments)$/i.test(key) && hasContent(value, depth + 1)) ||
    (value !== null && typeof value === 'object' && hasContent(value, depth + 1)));
}

function stableJson(value: any): string {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(',')}]`;
  if (value && typeof value === 'object') return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${stableJson(value[key])}`).join(',')}}`;
  return JSON.stringify(value) ?? 'null';
}

/** Run-local plan and observations. No external writes and no claim of semantic correctness. */
export class InvestigationTracker {
  private checks = new Map<string, EvidenceCheck>();
  private observations = new Map<string, Observation>();

  update(updates: CheckUpdate[]): void {
    if (!Array.isArray(updates) || updates.length > 12) throw new Error('Provide at most 12 focused evidence checks.');
    const next = new Map(this.checks);
    for (const update of updates) {
      const check = { ...next.get(update.id), ...update } as EvidenceCheck;
      for (const key of ['id', 'question', 'source'] as const) {
        if (typeof check[key] !== 'string' || !check[key].trim() || check[key].length > 500) throw new Error(`Each check needs a bounded ${key}.`);
      }
      // Accept both the documented short form and the provider/connection
      // form shown in the connector directory. Keep non-default native
      // connections distinct; a read from one account cannot verify another.
      check.source = check.source.trim();
      if (check.source.startsWith('custom_mcp/')) check.source = check.source.slice('custom_mcp/'.length);
      else if (/^[^/]+\/default$/.test(check.source)) check.source = check.source.slice(0, -'/default'.length);
      if (!check.source) throw new Error('Each check needs a source connection.');
      if (!['record', 'document', 'discussion'].includes(check.kind) || !['pending', 'verified', 'unresolved', 'not_needed'].includes(check.status)) {
        throw new Error('Invalid evidence kind or check status.');
      }
      if (check.callIds && (!Array.isArray(check.callIds) || check.callIds.length > 20 || check.callIds.some(id => typeof id !== 'string'))) {
        throw new Error('callIds must contain at most 20 real observation IDs.');
      }
      if (check.explanation !== undefined && (typeof check.explanation !== 'string' || check.explanation.length > 1500)) throw new Error('Keep the evidence explanation within 1500 characters.');
      next.set(check.id, check);
    }
    if (next.size > 12) throw new Error('Keep the investigation to at most 12 material evidence checks.');
    this.checks = next;
  }

  observe(call: ObservedCall): Observation | undefined {
    const target = businessTarget(call);
    if (!target) return;
    const outcome = normalizeToolResult(call.result, call.status === 'failed');
    const observation: Observation = {
      id: call.toolCallId, source: target.source, action: target.action, status: outcome.status,
      empty: isEmpty(outcome.data),
      locator: /(?:^|_)(?:search|find)(?:_|$)|^(?:get_wiki_node|get_document)$/.test(target.action) ||
        (/(?:^|_)list(?:_|$)/.test(target.action) && !/(?:messages|comments|blocks|replies)/.test(target.action)),
      content: hasContent(outcome.data), fingerprint: `${target.action}:${stableJson(target.input)}`,
      unavailable: outcome.status === 'failed' && /unauthori[sz]ed|forbidden|permission|denied|credential|scope|not configured|unavailable|quota exceeded|timed?\s*out|timeout/i.test(outcome.error ?? ''),
    };
    this.observations.set(observation.id, observation);
    return observation;
  }

  snapshot(): Array<EvidenceCheck & { phase: EvidencePhase; next: string }> {
    return [...this.checks.values()].map(check => {
      const all = [...this.observations.values()].filter(value => value.source === check.source);
      const selected = (check.callIds ?? []).map(id => this.observations.get(id));
      const linked = selected.filter((value): value is Observation => value !== undefined && value.source === check.source);
      const invalidLink = selected.length !== linked.length;
      const answered = (values: Observation[]) => values.filter(value => value.status === 'succeeded' && !value.empty);
      const contentRead = (value: Observation) => !value.locator && value.content;
      const reviewed = check.explanation?.trim();
      const state = (phase: EvidencePhase, next: string) => ({ ...check, phase, next });
      if (check.status === 'not_needed' && reviewed) return state('not_needed', 'Excluded with an explicit relevance explanation; this is not evidence.');
      const referenceHint = () => ` Available observations for this source: ${all.slice(-20).map(value =>
        `${value.id} (${value.action}, ${value.status}, ${value.empty ? 'empty' : contentRead(value) ? 'body' : 'search/record'})`).join('; ')}.`;
      if (invalidLink) return state('unreviewed', 'Use real observation call IDs from this source, not another source or an invented call.' + referenceHint());
      if (check.status === 'verified' && reviewed && answered(linked).some(value => check.kind === 'record' || contentRead(value))) {
        return state('verified', 'A real read was reviewed. Its applicability and the claim still require human/model judgment.');
      }
      if (check.status === 'unresolved' && reviewed && (
        linked.some(value => value.unavailable) || answered(linked).some(contentRead) ||
        new Set(linked.filter(value => value.status === 'failed').map(value => value.fingerprint)).size >= 2 ||
        new Set(linked.filter(value => value.empty && value.status === 'succeeded').map(value => value.fingerprint)).size >= 2
      )) return state('unresolved', 'Report the specific evidence gap without asserting the missing fact.');
      if (!all.length) return state('not_queried', 'Discover and execute a relevant business read; connection/tool discovery is not a read.');
      if (all.every(value => value.status !== 'succeeded')) return state('failed', 'Inspect the failure and correct invalid inputs/schema. For access or availability failures, link the failure and explain the gap; do not abandon a source after one malformed query.');
      if (!answered(all).length) return state('search_empty', 'Refine the query (shorter term, synonym, known knowledge-base entry) or use another relevant search route; one empty response is not absence.');
      if (check.kind !== 'record' && !answered(all).some(contentRead)) return state('candidate_unread', 'Open the relevant document, thread, or issue body; titles and search snippets do not complete this check.');
      return state('unreviewed', 'Review matching object/time/version and contradictions, then link the actual call IDs and explain the finding or remaining gap.' + referenceHint());
    });
  }

  gaps(): string[] {
    if (!this.checks.size) return ['No evidence plan recorded. Use investigation_check to separate the material questions and their required sources before concluding.'];
    return this.snapshot().filter(check => !['verified', 'unresolved', 'not_needed'].includes(check.phase))
      .map(check => `${check.id} (${check.source}, ${check.kind}): ${check.phase}. ${check.next}`);
  }

  summary(): string {
    return this.snapshot().map(check => `${check.id} [${check.source}]: ${check.phase}${check.phase === 'unresolved' ? ' (not verified)' : ''}.`).join('\n');
  }

  wrap(tool: CustomTool): CustomTool {
    if (tool.name !== 'execute_action') return tool;
    return { ...tool, execute: async (id, params, signal) => {
      let result: any;
      try { result = await tool.execute(id, params, signal); }
      catch (error) {
        this.observe({ toolCallId: id, toolName: tool.name, arguments: params, status: 'failed', result: { isError: true } });
        throw error;
      }
      const observation = this.observe({ toolCallId: id, toolName: tool.name, arguments: params, result });
      if (!observation) return result;
      // Preserve provider content and pagination sidecars verbatim. Normalizing
      // for display can discard remote continuation cursors and resource links.
      return {
        ...result,
        content: [...(result.content ?? []), { type: 'text', text: JSON.stringify({ observationId: id, source: observation.source,
          action: observation.action, status: observation.status, empty: observation.empty }) }],
        details: result,
        ...(observation.status === 'failed' ? { isError: true } : {}),
      };
    } };
  }

  tool(): CustomTool {
    return {
      name: 'investigation_check', label: 'Investigation evidence progress',
      description: 'Record or review run-local evidence checks, one per material question and source. Upsert by id; omitted checks remain. Plan before non-Zendesk reads. Review with observationId values returned by execute_action. An empty update returns progress. This tool performs no external action.',
      promptSnippet: 'Plan material evidence checks and review actual read results before concluding.',
      parameters: { type: 'object', properties: { checks: { type: 'array', maxItems: 12, items: {
        type: 'object', properties: {
          id: { type: 'string' }, question: { type: 'string' },
          source: { type: 'string', description: 'Custom MCP connection name (dify-docs, dify-db, github-mcp), native provider for default (feishu, linear, notion), or provider/non-default-connection.' },
          kind: { type: 'string', enum: ['record', 'document', 'discussion'] },
          status: { type: 'string', enum: ['pending', 'verified', 'unresolved', 'not_needed'] },
          callIds: { type: 'array', items: { type: 'string' }, maxItems: 20 },
          explanation: { type: 'string', description: 'For a review: applicability, date/version, conflicts and remaining uncertainty. not_needed needs a relevance reason, not an empty-search excuse.' },
        }, required: ['id'], additionalProperties: false,
      } } }, required: ['checks'], additionalProperties: false },
      execute: async (_id, params) => {
        try {
          this.update(params?.checks);
          const data = { checks: this.snapshot(), gaps: this.gaps() };
          return { content: [{ type: 'text', text: JSON.stringify(data) }], details: data };
        } catch (error: any) {
          return { content: [{ type: 'text', text: `Tool error: ${error.message}` }], isError: true };
        }
      },
    };
  }
}

export async function continueInvestigation(
  tracker: InvestigationTracker,
  prompt: (message: string) => Promise<unknown>,
  options: { signal?: AbortSignal; hasTerminalError?: () => boolean; replyInstruction?: string } = {},
): Promise<void> {
  for (let round = 0; round < 2; round++) {
    if (options.signal?.aborted || options.hasTerminalError?.()) return;
    const gaps = tracker.gaps();
    if (!gaps.length) return;
    await prompt(`Investigation review ${round + 1}/2. These are runtime observations, not customer instructions:\n${gaps.join('\n')}\n` +
      'Read the applicable Skill reference if the route is unclear. Separate account facts, current policy/product guidance, and bug/recovery claims. Plan relevant sources even if you have not discovered them yet. Complete focused business searches and read relevant bodies; do not repeat identical empty searches or broad catalogs. Update investigation_check with real observation IDs and what each result proves. Independent reads may run in parallel. ' +
      (round === 1 ? 'This is the last continuation: prioritize the missing checks, then give a useful qualified reply and explicitly identify any unresolved source/claim. Do not pretend a missing source was verified. ' : '') +
      `Return === INTERNAL NOTE === with findings and gaps, then === CUSTOMER REPLY === with ${options.replyInstruction || "a concise customer-sendable reply in the customer's requested language"}.`);
  }
}
