type TextBlock = { type: 'text'; text: string };
type ImageBlock = { type: 'image'; data: string; mimeType: string };
export interface ModelFileInput {
  type: 'input_file';
  filename: string;
  file_data: string;
}

export const MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024;
export const MAX_MODEL_FILES = 4;
const fileExtensions: Record<string, string> = {
  'application/pdf': 'pdf',
  'text/plain': 'txt',
  'text/markdown': 'md',
  'text/csv': 'csv',
  'application/json': 'json',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'docx',
  'application/vnd.openxmlformats-officedocument.presentationml.presentation': 'pptx',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'xlsx',
};

function filename(value: any, fallback: string): string {
  const candidate = value.filename || value.name || value.uri || fallback;
  return typeof candidate === 'string'
    ? candidate.split(/[\\/]/).at(-1)!.split(/[?#]/)[0].replace(/[\x00-\x1f\x7f]/g, '').slice(-160) || fallback
    : fallback;
}

/** Keep files out of text tokens; the run's Responses adapter supplies their original bytes. */
export async function modelToolContent(result: any, signal?: AbortSignal): Promise<{
  content: Array<TextBlock | ImageBlock>; modelFiles: ModelFileInput[];
  details: unknown; attachmentErrors: boolean;
}> {
  signal?.throwIfAborted();
  const images: ImageBlock[] = [];
  const modelFiles: ModelFileInput[] = [];
  const errors: string[] = [];
  const seen = new Set<object>();
  const binaries = new Set<string>();
  let nodes = 0;
  const visit = (value: any, depth = 0, inheritedName = ''): any => {
    signal?.throwIfAborted();
    if (depth > 12 || ++nodes > 5000) return '[Tool result nesting/size limit reached]';
    if (!value || typeof value !== 'object') return value;
    if (seen.has(value)) return '[Repeated content]';
    seen.add(value);
    const name = filename(value, inheritedName);
    let mime = String(value.mimeType || value.mime_type || value.contentType || value.content_type || value.media_type || '').split(';')[0].trim().toLowerCase();
    if (!mime || mime === 'application/octet-stream') {
      mime = Object.entries(fileExtensions).find(([, extension]) => name.toLowerCase().endsWith(`.${extension}`))?.[0] || mime;
    }
    const binary = ['image', 'file'].includes(value.type) ? value.data || value.base64 : value.blob || value.base64;
    if (typeof binary === 'string') {
      if (!binaries.has(binary)) {
        binaries.add(binary);
        try {
          if (binary.length > Math.ceil(MAX_ATTACHMENT_BYTES * 4 / 3) + 256) throw new Error('Attachment exceeded the 10 MiB processing limit');
          const base64 = binary.replace(/^data:[^,]*;base64,/, '').replace(/\s/g, '');
          if (!base64 || !/^[A-Za-z0-9+/]*={0,2}$/.test(base64) || base64.length % 4 === 1) throw new Error('Attachment has invalid base64 data');
          const bytes = Buffer.from(base64, 'base64');
          if (bytes.length > MAX_ATTACHMENT_BYTES) throw new Error('Attachment exceeded the 10 MiB processing limit');
          if (/^image\/(?:png|jpeg|webp|gif)$/.test(mime)) {
            images.push({ type: 'image', data: bytes.toString('base64'), mimeType: mime });
          } else if (fileExtensions[mime]) {
            if (modelFiles.length >= MAX_MODEL_FILES) throw new Error('Attachment exceeded the four-file processing limit');
            if (mime === 'application/pdf' && !bytes.subarray(0, 1024).toString('latin1').includes('%PDF-')) throw new Error('Attachment is not a PDF');
            const extension = fileExtensions[mime];
            const fileName = name ? name.toLowerCase().endsWith(`.${extension}`) ? name : `${name}.${extension}` : `attachment-${modelFiles.length + 1}.${extension}`;
            modelFiles.push({ type: 'input_file', filename: fileName,
              file_data: `data:${mime};base64,${bytes.toString('base64')}` });
          } else throw new Error(`Unsupported attachment type: ${mime || 'unknown'}`);
        } catch (error: any) {
          errors.push(error.message);
        }
      }
      // Never retain raw file bytes in the SDK transcript, telemetry, or text input.
      return Object.fromEntries(Object.entries(value).map(([key, child]) =>
        [key, ['data', 'blob', 'base64'].includes(key) && child === binary ? '[Binary attachment handled separately; see attachment status]' : visit(child, depth + 1, name)]));
    }
    if (value.type === 'text' && typeof value.text === 'string') {
      let parsed: any;
      try { parsed = JSON.parse(value.text); } catch { return value; }
      return { ...value, text: JSON.stringify(visit(parsed, depth + 1, name)) };
    }
    if (Array.isArray(value)) return value.map(child => visit(child, depth + 1, inheritedName));
    return Object.fromEntries(Object.entries(value).map(([key, child]) => [key, visit(child, depth + 1, name)]));
  };
  let source = result;
  if (typeof source === 'string') {
    try { source = JSON.parse(source); } catch { /* Plain text result. */ }
  }
  const details = visit(source);
  const content: Array<TextBlock | ImageBlock> = [{ type: 'text', text: typeof details === 'string' ? details : JSON.stringify(details) ?? 'Empty tool response' }, ...images];
  if (modelFiles.length) content.push({ type: 'text', text: `Original attachments available as native file inputs: ${modelFiles.map(file => file.filename).join(', ')}. Inspect the file contents before relying on them; file delivery alone is not verification.` });
  for (const error of errors) content.push({ type: 'text', text: `Attachment is present but could not be supplied to the model: ${error}. Do not ask the customer to reattach it solely because of this internal reading failure.` });
  return { content, modelFiles, details, attachmentErrors: errors.length > 0 };
}
