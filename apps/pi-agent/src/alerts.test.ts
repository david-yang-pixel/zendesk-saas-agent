import { describe, it, expect, vi } from 'vitest';
import {
  formatFeishuAlertCard,
  formatFeishuAlertText,
  sendFeishuAlert,
  HumanAlertTracker,
} from './alerts.js';

describe('formatFeishuAlertText', () => {
  it('formats delivery failure alert with draft reply', () => {
    const text = formatFeishuAlertText({
      title: '🚨 Dify 工单投递失败警告',
      ticketId: 4277,
      status: '投递失败 (relay_error)',
      reason: 'Browser Relay 连接超时',
      draftReply: 'Hello Karl, I have recorded your request for billing review.',
    });

    expect(text).toContain('🚨 Dify 工单投递失败警告');
    expect(text).toContain('工单：#4277');
    expect(text).toContain('状态：投递失败 (relay_error)');
    expect(text).toContain('链接：https://langgenius.zendesk.com/agent/tickets/4277');
    expect(text).toContain('失败原因：\nBrowser Relay 连接超时');
    expect(text).toContain('AI 建议回复草稿（可直接复制发送）：\nHello Karl, I have recorded your request for billing review.');
  });

  it('formats human takeover alert with customer message', () => {
    const text = formatFeishuAlertText({
      title: '🙋 Dify 工单转人工接管提醒',
      ticketId: 4280,
      status: '模式 B 人工优先锁定（AI 保持静默）',
      customerMessage: 'Please transfer me to human agent',
      note: '检测到客户诉求转人工或已有真人介入，AI 已自动暂停并移交，请真人客服及时跟进。',
    });

    expect(text).toContain('🙋 Dify 工单转人工接管提醒');
    expect(text).toContain('工单：#4280');
    expect(text).toContain('状态：模式 B 人工优先锁定（AI 保持静默）');
    expect(text).toContain('链接：https://langgenius.zendesk.com/agent/tickets/4280');
    expect(text).toContain('客户最新诉求：\nPlease transfer me to human agent');
    expect(text).toContain('说明：\n检测到客户诉求转人工或已有真人介入，AI 已自动暂停并移交，请真人客服及时跟进。');
  });
});

describe('sendFeishuAlert', () => {
  it('sends correct JSON payload to webhook', async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ StatusCode: 0 }),
    } as any);

    const success = await sendFeishuAlert(
      'https://open.feishu.cn/mock-hook',
      {
        title: '🚨 Test Title',
        ticketId: 1001,
        status: 'failed',
      },
      mockFetch as any
    );

    expect(success).toBe(true);
    expect(mockFetch).toHaveBeenCalledTimes(1);
    const [url, init] = mockFetch.mock.calls[0];
    expect(url).toBe('https://open.feishu.cn/mock-hook');
    expect(init.method).toBe('POST');
    const body = JSON.parse(init.body);
    expect(body.msg_type).toBe('text');
    expect(body.content.text).toContain('🚨 Test Title');
    expect(body.content.text).toContain('工单：#1001');
  });

  it('handles fetch failure gracefully', async () => {
    const mockFetch = vi.fn().mockRejectedValue(new Error('Network error'));
    const success = await sendFeishuAlert(
      'https://open.feishu.cn/mock-hook',
      {
        title: '🚨 Test Title',
        ticketId: 1001,
        status: 'failed',
      },
      mockFetch as any
    );
    expect(success).toBe(false);
  });
});

describe('HumanAlertTracker', () => {
  it('deduplicates alerts within cooldown period', () => {
    const tracker = new HumanAlertTracker(1000); // 1s cooldown
    const t0 = 10000;

    expect(tracker.shouldAlert(4277, t0)).toBe(true);
    tracker.recordAlert(4277, t0);

    // Immediate repeat -> should NOT alert
    expect(tracker.shouldAlert(4277, t0 + 500)).toBe(false);

    // After cooldown -> should alert
    expect(tracker.shouldAlert(4277, t0 + 1500)).toBe(true);

    // Different ticket -> should alert
    expect(tracker.shouldAlert(9999, t0 + 500)).toBe(true);
  });

  it('allows immediate alert after clear()', () => {
    const tracker = new HumanAlertTracker(60000);
    const t0 = 10000;

    tracker.recordAlert(4277, t0);
    expect(tracker.shouldAlert(4277, t0 + 1000)).toBe(false);

    // Operator mentions @David Yang -> clears ticket lock & alert tracker
    tracker.clear(4277);
    expect(tracker.shouldAlert(4277, t0 + 1000)).toBe(true);
  });
});

describe('Feishu alert cards', () => {
  const params = {
    title: '🙋 工单 #5492 需要人工处理',
    color: 'red' as const,
    ticketId: 5492,
    status: '客户请求人工支持，AI 已停止回复。当前在支持时间内，请尽快跟进。',
    customerMessage: 'human **not bold** [link](https://evil.example)',
    note: '已给工单打标签 human_requested。',
  };

  it('builds a red card with the status first, plain customer text and a ticket button', () => {
    const card = formatFeishuAlertCard(params) as any;
    expect(card.header).toEqual({ template: 'red', title: { tag: 'plain_text', content: '🙋 工单 #5492 需要人工处理' } });
    expect(card.elements[0]).toEqual({ tag: 'div', text: { tag: 'lark_md', content: `**${params.status}**` } });
    expect(JSON.stringify(card.elements)).toContain('[#5492](https://langgenius.zendesk.com/agent/tickets/5492)');
    // Customer text is never rendered as Markdown.
    expect(card.elements).toContainEqual({ tag: 'div', text: { tag: 'plain_text', content: params.customerMessage } });
    const button = card.elements.at(-1).actions[0];
    expect(button).toMatchObject({ type: 'danger', url: 'https://langgenius.zendesk.com/agent/tickets/5492' });
  });

  it('sends an interactive card when a colour is set and plain text otherwise', async () => {
    const fetchFn = vi.fn(async () => new Response(JSON.stringify({ code: 0 }), { status: 200 }));
    await sendFeishuAlert('https://open.feishu.cn/hook/x', params, fetchFn as unknown as typeof fetch);
    const { color: _color, ...plainParams } = params;
    await sendFeishuAlert('https://open.feishu.cn/hook/x', plainParams, fetchFn as unknown as typeof fetch);
    const bodies = fetchFn.mock.calls.map(call => JSON.parse(String((call as unknown as [string, RequestInit])[1].body)));
    expect(bodies[0].msg_type).toBe('interactive');
    expect(bodies[0].card.header.template).toBe('red');
    expect(bodies[1].msg_type).toBe('text');
  });
});
