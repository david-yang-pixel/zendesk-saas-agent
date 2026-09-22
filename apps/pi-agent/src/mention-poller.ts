/** Keep the legacy scan enabled until real webhook acceptance is complete. */
export function mentionPollerEnabled(value?: string): boolean {
  const normalized = (value ?? '').trim().toLowerCase();
  if (!normalized || normalized === 'true') return true;
  if (normalized === 'false') return false;
  throw new Error('MENTION_POLLER_ENABLED must be true or false');
}

export async function runMentionPoller(options: {
  enabled: boolean;
  running: () => boolean;
  initialize: () => Promise<void>;
  scan: () => Promise<void>;
  wait: () => Promise<void>;
}): Promise<void> {
  if (!options.enabled) return;
  await options.initialize();
  while (options.running()) {
    await options.scan();
    await options.wait();
  }
}
