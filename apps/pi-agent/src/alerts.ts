/** Header colours a Feishu message card supports. */
export type FeishuCardColor = 'red' | 'orange' | 'yellow' | 'green' | 'blue' | 'grey';

export interface FeishuAlertParams {
  title: string;
  ticketId: number;
  status: string;
  reason?: string;
  customerMessage?: string;
  draftReply?: string;
  note?: string;
  /** Send a message card with this header colour instead of plain text. */
  color?: FeishuCardColor;
}

const ticketUrl = (ticketId: number) => `https://langgenius.zendesk.com/agent/tickets/${ticketId}`;

export function formatFeishuAlertText(params: FeishuAlertParams): string {
  const lines: string[] = [
    params.title,
    '',
    `工单：#${params.ticketId}`,
    `状态：${params.status}`,
    `链接：${ticketUrl(params.ticketId)}`,
  ];

  if (params.reason) {
    lines.push('', `失败原因：\n${params.reason.trim()}`);
  }

  if (params.customerMessage) {
    lines.push('', `客户最新诉求：\n${params.customerMessage.trim()}`);
  }

  if (params.draftReply) {
    lines.push('', `AI 建议回复草稿（可直接复制发送）：\n${params.draftReply.trim()}`);
  }

  if (params.note) {
    lines.push('', `说明：\n${params.note.trim()}`);
  }

  return lines.join('\n');
}

/**
 * A Feishu interactive card: a coloured header, the status in bold, then each
 * section. Customer and model text goes in plain_text blocks so its Markdown is
 * shown as written, never rendered.
 */
export function formatFeishuAlertCard(params: FeishuAlertParams): Record<string, unknown> {
  const markdown = (content: string) => ({ tag: 'div', text: { tag: 'lark_md', content } });
  const plain = (content: string) => ({ tag: 'div', text: { tag: 'plain_text', content } });
  const elements: unknown[] = [
    markdown(`**${params.status}**`),
    markdown(`工单：[#${params.ticketId}](${ticketUrl(params.ticketId)})`),
  ];
  const sections: Array<[string, string | undefined]> = [
    ['失败原因', params.reason],
    ['客户最新诉求', params.customerMessage],
    ['AI 建议回复草稿（可直接复制发送）', params.draftReply],
    ['说明', params.note],
  ];
  for (const [label, value] of sections) {
    if (!value?.trim()) continue;
    elements.push({ tag: 'hr' }, markdown(`**${label}**`), plain(value.trim()));
  }
  elements.push({
    tag: 'action',
    actions: [{
      tag: 'button',
      text: { tag: 'plain_text', content: '打开工单' },
      type: params.color === 'red' ? 'danger' : 'primary',
      url: ticketUrl(params.ticketId),
    }],
  });
  return {
    config: { wide_screen_mode: true },
    header: { template: params.color ?? 'blue', title: { tag: 'plain_text', content: params.title } },
    elements,
  };
}

export async function sendFeishuAlert(
  webhookUrl: string,
  params: FeishuAlertParams,
  fetchFn: typeof fetch = fetch
): Promise<boolean> {
  if (!webhookUrl) {
    console.warn('[Feishu Alert] FEISHU_WEBHOOK_URL not configured, skipping alert.');
    return false;
  }

  const message = params.color
    ? { msg_type: 'interactive', card: formatFeishuAlertCard(params) }
    : { msg_type: 'text', content: { text: formatFeishuAlertText(params) } };

  try {
    const res = await fetchFn(webhookUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(message),
    });

    if (!res.ok) {
      const errText = await res.text();
      console.warn(`[Feishu Alert] Failed to send alert HTTP ${res.status}: ${errText}`);
      return false;
    }

    const data = (await res.json()) as any;
    if (data.StatusCode !== 0 && data.code !== 0 && data.code !== undefined) {
      console.warn(`[Feishu Alert] Feishu returned error code:`, data);
      return false;
    }

    console.log(`[Feishu Alert] Successfully sent alert for ticket #${params.ticketId} (${params.title})`);
    return true;
  } catch (err: any) {
    console.warn(`[Feishu Alert] Error sending alert to Feishu:`, err.message);
    return false;
  }
}

export class HumanAlertTracker {
  private alerts = new Map<number, number>();
  private readonly cooldownMs: number;

  constructor(cooldownMs: number = 30 * 60 * 1000) {
    this.cooldownMs = cooldownMs;
  }

  shouldAlert(ticketId: number, now: number = Date.now()): boolean {
    if (!this.alerts.has(ticketId)) {
      return true;
    }
    const last = this.alerts.get(ticketId)!;
    return now - last >= this.cooldownMs;
  }

  recordAlert(ticketId: number, now: number = Date.now()): void {
    this.alerts.set(ticketId, now);
  }

  clear(ticketId: number): void {
    this.alerts.delete(ticketId);
  }

  has(ticketId: number): boolean {
    return this.alerts.has(ticketId);
  }
}
