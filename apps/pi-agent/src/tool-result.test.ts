import { describe, expect, it } from 'vitest';
import { mergeToolCalls, normalizeToolResult } from './tool-result.js';

const payload = { success: true, data: { ticket: { id: 3726 } } };
const mcp = { structuredContent: payload, content: [{ type: 'text', text: JSON.stringify(payload) }] };
const call = (id: string, result: any) => ({ toolCallId: id, toolName: 'execute_action', service: 'zendesk', arguments: { input: { toolName: 'get_ticket' } }, result });

describe('canonical tool outcomes', () => {
  it.each([payload, mcp, { details: mcp, content: [] }, { content: [{ type: 'text', text: JSON.stringify(mcp) }] }])('normalizes every live/history envelope', value => {
    expect(normalizeToolResult(value)).toEqual({ status: 'succeeded', data: { ticket: { id: 3726 } }, error: undefined });
  });
  it('does not mistake outer isError=false for business success', () => {
    const value = { isError: false, content: [{ type: 'text', text: JSON.stringify({ ok: false, error: { code: 'invalid_input', message: 'bad cursor' } }) }] };
    expect(normalizeToolResult(value)).toMatchObject({ status: 'failed', error: 'bad cursor' });
  });
  it('keeps an outer failure even when the nested data looks successful', () => {
    expect(normalizeToolResult({ isError: true, details: mcp }).status).toBe('failed');
  });
  it('recognizes transport errors wrapped by Pi', () => {
    expect(normalizeToolResult({ details: { error: 'HTTP 503' } })).toMatchObject({ status: 'failed', error: 'HTTP 503' });
    expect(normalizeToolResult('Tool error: timeout').status).toBe('failed');
  });
  it('does not turn missing results into successful calls', () => {
    expect(normalizeToolResult(null).status).toBe('unknown');
    expect(normalizeToolResult({ content: [] }).status).toBe('unknown');
  });
  it('bounds recursive envelopes', () => {
    const cyclic: any = {}; cyclic.details = cyclic;
    expect(normalizeToolResult(cyclic).status).toBe('failed');
  });
  it('retains ALL live calls after partial history compaction and deduplicates IDs', () => {
    const live = [call('a', { details: mcp }), call('b', { details: { error: 'bad cursor' } }), call('c', { details: mcp })];
    const merged = mergeToolCalls([call('c', mcp)], live);
    expect(new Set(merged.map(c => c.toolCallId))).toEqual(new Set(['a', 'b', 'c']));
    expect(merged).toHaveLength(3);
    expect(merged.find(c => c.toolCallId === 'a')?.outcome?.data.ticket.id).toBe(3726);
    expect(merged.find(c => c.toolCallId === 'b')?.status).toBe('failed');
  });
  it('supports fully compacted and history-only sessions', () => {
    expect(mergeToolCalls([], [call('a', { details: mcp })])[0].outcome?.data.ticket.id).toBe(3726);
    expect(mergeToolCalls([call('a', mcp)], [])[0].outcome?.data.ticket.id).toBe(3726);
  });
});
