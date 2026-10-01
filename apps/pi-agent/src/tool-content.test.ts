import { expect, it } from 'vitest';
import { modelToolContent } from './tool-content.js';

// A tiny generated PDF, with no customer data or external file access.
function receiptPdf(text = 'BT /F1 12 Tf 30 60 Td (Team plan paid USD 159) Tj ET'): string {
  const objects = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 100] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    `<< /Length ${text.length} >>\nstream\n${text}\nendstream`,
  ];
  let pdf = '%PDF-1.4\n';
  const offsets = [0];
  objects.forEach((object, index) => { offsets.push(Buffer.byteLength(pdf)); pdf += `${index + 1} 0 obj\n${object}\nendobj\n`; });
  const xref = Buffer.byteLength(pdf);
  pdf += `xref\n0 6\n0000000000 65535 f \n${offsets.slice(1).map(offset => String(offset).padStart(10, '0') + ' 00000 n ').join('\n')}\ntrailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF`;
  return Buffer.from(pdf).toString('base64');
}

it('forwards a nested MCP screenshot as native image content instead of JSON base64', async () => {
  const image = { type: 'image', mimeType: 'image/png', data: 'aW1hZ2U=' };
  const result = await modelToolContent({ content: [{ type: 'text', text: JSON.stringify({ ok: true, data: { content: [image] } }) }] });
  expect(result.content).toContainEqual(image);
  expect(result.content.filter(block => block.type === 'text').map(block => block.text).join('')).not.toContain('aW1hZ2U=');
  expect(result.attachmentErrors).toBe(false);
});
it('preserves the original nested PDF as a native file, without putting its bytes in text or telemetry', async () => {
  const pdf = receiptPdf();
  const result = await modelToolContent({ structuredContent: { ok: true, data: {
    content: [{ type: 'resource', resource: { uri: 'attachment://receipt.pdf', mimeType: 'application/pdf', blob: pdf } }],
  } } });
  expect(result.attachmentErrors).toBe(false);
  expect(result.modelFiles).toEqual([{ type: 'input_file', filename: 'receipt.pdf', file_data: `data:application/pdf;base64,${pdf}` }]);
  expect(JSON.stringify(result.content)).not.toContain(pdf);
  expect(JSON.stringify(result.details)).not.toContain(pdf);
});
it('supplies a PDF without a text layer directly instead of requiring local OCR', async () => {
  const pdf = receiptPdf('');
  const result = await modelToolContent({ mimeType: 'application/pdf', base64: pdf });
  expect(result.attachmentErrors).toBe(false);
  expect(result.modelFiles[0].file_data).toBe(`data:application/pdf;base64,${pdf}`);
});
it('supports a downloaded text/office attachment by its MIME type and inherited filename', async () => {
  const base64 = Buffer.from('Offline file fixture').toString('base64');
  const result = await modelToolContent({ filename: 'document.docx', content: [{ type: 'resource', resource: {
    mimeType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', blob: base64,
  } }] });
  expect(result.modelFiles[0]).toMatchObject({ type: 'input_file', filename: 'document.docx' });
  expect(result.attachmentErrors).toBe(false);
});
it('recovers generic download MIME types from filenames and supplies an API-compatible filename', async () => {
  const pdf = receiptPdf();
  const generic = await modelToolContent({ filename: 'receipt.pdf', mimeType: 'application/octet-stream', base64: pdf });
  expect(generic.modelFiles[0]).toMatchObject({ filename: 'receipt.pdf', file_data: `data:application/pdf;base64,${pdf}` });
  const noExtension = await modelToolContent({ name: 'receipt', contentType: 'application/pdf', base64: pdf });
  expect(noExtension.modelFiles[0].filename).toBe('receipt.pdf');
});
it('reports unreadable PDFs as an internal reading failure rather than a missing attachment', async () => {
  const result = await modelToolContent({ content: [{ type: 'resource', resource: {
    mimeType: 'application/pdf', blob: Buffer.from('not a PDF').toString('base64'),
  } }] });
  expect(result.attachmentErrors).toBe(true);
  expect(result.modelFiles).toEqual([]);
  expect(result.content.some(block => block.type === 'text' && block.text.includes('Attachment is present but could not be supplied'))).toBe(true);
});
it('bounds native file sizes and counts and reports an explicit internal gap', async () => {
  const oversized = await modelToolContent({ mimeType: 'application/pdf', base64: 'A'.repeat(14 * 1024 * 1024) });
  expect(oversized.modelFiles).toEqual([]);
  expect(oversized.attachmentErrors).toBe(true);
  const many = await modelToolContent(Array.from({ length: 5 }, (_, index) => ({ mimeType: 'application/pdf', base64: receiptPdf(`% ${index}`) })));
  expect(many.modelFiles).toHaveLength(4);
  expect(many.attachmentErrors).toBe(true);
});
it('honors cancellation before processing a PDF', async () => {
  const controller = new AbortController(); controller.abort();
  await expect(modelToolContent({ mimeType: 'application/pdf', base64: receiptPdf() }, controller.signal)).rejects.toThrow();
});
