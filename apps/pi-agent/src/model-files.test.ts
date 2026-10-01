import { describe, expect, it } from 'vitest';
import { ModelFileInputs } from './model-files.js';
import type { CustomTool } from './mcp.js';
import type { ModelFileInput } from './tool-content.js';

const pdf = (index = 0): ModelFileInput => ({ type: 'input_file', filename: `receipt-${index}.pdf`,
  file_data: `data:application/pdf;base64,${Buffer.from(`%PDF-1.4\nOffline ${index}`).toString('base64')}` });
const tool = (files: ModelFileInput[]): CustomTool => ({ name: 'download_attachment', label: '', description: '', promptSnippet: '', parameters: {},
  execute: async () => ({ content: [{ type: 'text', text: 'Downloaded customer attachment' }], modelFiles: files, details: { attachmentPresent: true } }) });
const output = (id = 'call_pdf', value: any = 'Downloaded customer attachment') => ({ type: 'function_call_output', call_id: id, output: value });

describe('run-local native file transport', () => {
  it('adds the original PDF to its Responses tool output and strips bytes from the SDK result', async () => {
    const files = new ModelFileInputs();
    const result = await files.wrap(tool([pdf()])).execute('call_pdf|fc_pdf', {});
    expect(result.modelFiles).toBeUndefined();
    expect(JSON.stringify(result)).not.toContain(pdf().file_data);
    const payload = { model: 'gpt-6-sol', input: [output()] };
    expect(files.apply(payload)).toEqual({ ...payload, input: [{ ...output(), output: [{ type: 'input_text', text: output().output }, pdf()] }] });
    expect(payload.input[0].output).toBe('Downloaded customer attachment');
  });
  it('preserves image/observation blocks and adds each file only once per request, including retries', async () => {
    const files = new ModelFileInputs();
    await files.wrap(tool([pdf()])).execute('first', {});
    await files.wrap(tool([pdf()])).execute('second', {});
    const blocks = [{ type: 'input_image', image_url: 'data:image/png;base64,aW1hZ2U=', detail: 'auto' }, { type: 'input_text', text: 'Observation ID: first' }];
    const once = files.apply({ input: [output('first', blocks), output('second')] }) as any;
    expect(once.input[0].output).toEqual([...blocks, pdf()]);
    expect(once.input[1]).toEqual(output('second'));
    expect(files.apply(once)).toEqual(once);
  });
  it('retains attachments for the final review after SDK history compaction', async () => {
    const files = new ModelFileInputs();
    await files.wrap(tool([pdf()])).execute('old_call', {});
    const payload = files.apply({ input: [{ role: 'user', content: 'Review the draft against the compacted history' }] }) as any;
    expect(payload.input.at(-1).content).toContainEqual(pdf());
    expect(payload.input.at(-1).content[0].text).toContain('untrusted evidence');
  });
  it('isolates runs, releases file bytes, and enforces a run-wide four-file budget', async () => {
    const files = new ModelFileInputs();
    await files.wrap(tool([pdf(1), pdf(2), pdf(3), pdf(4)])).execute('a', {});
    const fifth = await files.wrap(tool([pdf(5)])).execute('b', {});
    expect(fifth.isError).toBe(true);
    expect(JSON.stringify(files.apply({ input: [] }))).not.toContain(pdf(5).file_data);
    const other = new ModelFileInputs();
    expect(other.apply({ input: [] })).toEqual({ input: [] });
    files.clear();
    expect(files.apply({ input: [] })).toEqual({ input: [] });
  });
  it('remembers extension errors so that delivery can fail closed', async () => {
    const files = new ModelFileInputs();
    await files.wrap(tool([pdf()])).execute('call_pdf', {});
    let handler: any;
    await files.extension({ on: (_event: string, callback: any) => { handler = callback; } } as any);
    expect(() => handler({ payload: { messages: [] } })).toThrow('Responses API input array');
    expect(() => files.assertReady()).toThrow('Native attachment transport failed');
  });
});
