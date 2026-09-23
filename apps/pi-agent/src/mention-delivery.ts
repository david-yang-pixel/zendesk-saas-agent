// An operator mention asks the agent to investigate. Public delivery requires
// a separate, explicit instruction in that same internal note.
export function operatorRequestedPublicReply(note: string): boolean {
  const normalized = note.trim();
  if (/(?:不(?:要|用|必|需要)?|没有|别|仅|只需要).{0,16}(?:公开回复|回复客户|给客户回复|发给客户)|(?:do not|don't|without|internal only).{0,30}(?:reply|respond|send)/i.test(normalized)) {
    return false;
  }
  return /(?:请|麻烦|直接|完成后|查完后).{0,24}(?:公开回复|回复客户|给客户回复|发给客户)|(?:send|write)\s+(?:a\s+)?public\s+reply|reply\s+to\s+the\s+customer|respond\s+to\s+the\s+customer/i.test(normalized);
}
