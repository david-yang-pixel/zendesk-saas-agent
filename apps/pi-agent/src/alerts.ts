export interface FeishuAlertParams {
  title: string;
  ticketId: number;
  status: string;
  reason?: string;
  customerMessage?: string;
  draftReply?: string;
  note?: string;
}

export function formatFeishuAlertText(params: FeishuAlertParams): string {
  const lines: string[] = [
    params.title,
    '',
    `工单：#${params.ticketId}`,
    `状态：${params.status}`,
    `链接：https://langgenius.zendesk.com/agent/tickets/${params.ticketId}`,
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

export async function sendFeishuAlert(
  webhookUrl: string,
  params: FeishuAlertParams,
  fetchFn: typeof fetch = fetch
): Promise<boolean> {
  if (!webhookUrl) {
    console.warn('[Feishu Alert] FEISHU_WEBHOOK_URL not configured, skipping alert.');
    return false;
  }

  const text = formatFeishuAlertText(params);

  try {
    const res = await fetchFn(webhookUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        msg_type: 'text',
        content: { text },
      }),
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
