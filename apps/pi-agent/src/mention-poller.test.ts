import { expect, it, vi } from 'vitest';
import { mentionPollerEnabled, runMentionPoller } from './mention-poller.js';

it('defaults on, accepts only explicit booleans', () => {
  expect(mentionPollerEnabled()).toBe(true);
  expect(mentionPollerEnabled('')).toBe(true);
  expect(mentionPollerEnabled('true')).toBe(true);
  expect(mentionPollerEnabled(' false ')).toBe(false);
  expect(() => mentionPollerEnabled('no')).toThrow();
});
it('disabled poller performs no initialization, scan, or timer', async () => {
  const initialize = vi.fn(), scan = vi.fn(), wait = vi.fn();
  await runMentionPoller({ enabled: false, running: () => true, initialize, scan, wait });
  expect(initialize).not.toHaveBeenCalled();
  expect(scan).not.toHaveBeenCalled();
  expect(wait).not.toHaveBeenCalled();
});
it('enabled poller initializes and scans until stopped', async () => {
  let running = true;
  const initialize = vi.fn(), scan = vi.fn();
  await runMentionPoller({ enabled: true, running: () => running, initialize, scan,
    wait: async () => { running = false; } });
  expect(initialize).toHaveBeenCalledOnce();
  expect(scan).toHaveBeenCalledOnce();
});
