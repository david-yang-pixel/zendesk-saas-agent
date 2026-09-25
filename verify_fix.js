// Directly verify sanitizeCustomerReply and extractDualTrackOutputs logic
function sanitizeCustomerReply(rawReply) {
  if (!rawReply) return '';
  let text = rawReply.trim();

  // Strip leading internal investigation headers like "## Investigation update — ticket #4396" or "Investigation update:"
  text = text.replace(/^#*\s*Investigation\s+update(?::|\s*[-—:]\s*(?:ticket\s*#?\d+)?|\s*[-—:])?\s*\n+/i, '');
  text = text.replace(/^#*\s*Ticket\s*#?\d+\s*Investigation(?:\s+Update)?(?::|\s*[-—:])?\s*\n+/i, '');

  // Strip leading "Suggested Reply:" / "### Suggested Reply:" / "Customer Reply:" wrapper headers
  text = text.replace(/^#*\s*(?:Suggested\s+Reply|Customer\s+Reply|Public\s+Reply)(?::|\s*[-—:]|\s*\n)?\s*\n+/i, '');

  // Truncate if internal analysis or internal memos follow (e.g. "🔍 Internal Analysis:", "--- \n### Internal Note:", etc.)
  const internalCutoffRegex = /(?:\n\s*[-—_]{3,}\s*)?\n\s*(?:#*\s*(?:🔍\s*)?Internal\s+Analysis|#*\s*Internal\s+(?:Note|Memo|Investigation)|#*\s*(?:🤖\s*)?\[?Dify(?:\s+AI)?(?:\s+Support)?(?:\s+Agent)?\s*(?:调查备忘录|内部备忘录|内部分析)\]?|#*\s*(?:内部调查备忘录|内部排查记录|技术排查备忘录))(?::|\s*[-—:]|\s*\n)/i;
  const cutoffMatch = text.match(internalCutoffRegex);
  if (cutoffMatch && cutoffMatch.index !== undefined) {
    text = text.substring(0, cutoffMatch.index).trim();
  }

  // Convert third-person handover prompts like "Ask Catherine for:" or "Ask the customer for:" to direct address
  text = text.replace(/(?:Ask\s+[A-Za-z0-9_.-]+\s+for|Ask\s+the\s+(?:customer|user)\s+for):\s*/gi, 'Could you please provide: ');
  text = text.replace(/(?:Ask\s+[A-Za-z0-9_.-]+|Ask\s+the\s+(?:customer|user))\s+to\s+/gi, 'Please ');

  return text.trim();
}

function extractDualTrackOutputs(rawText) {
  if (!rawText) return { customerReply: '' };

  const internalMarker = '=== INTERNAL NOTE ===';
  const customerMarker = '=== CUSTOMER REPLY ===';

  const internalIdx = rawText.indexOf(internalMarker);
  const customerIdx = rawText.indexOf(customerMarker);

  if (internalIdx !== -1 && customerIdx !== -1) {
    let internalContent = '';
    let customerContent = '';

    if (internalIdx < customerIdx) {
      internalContent = rawText.substring(internalIdx + internalMarker.length, customerIdx).trim();
      customerContent = rawText.substring(customerIdx + customerMarker.length).trim();
    } else {
      customerContent = rawText.substring(customerIdx + customerMarker.length, internalIdx).trim();
      internalContent = rawText.substring(internalIdx + internalMarker.length).trim();
    }

    return {
      customerReply: sanitizeCustomerReply(customerContent),
      internalNote: internalContent || undefined,
    };
  }

  // Fallback: If only customerMarker is found
  if (customerIdx !== -1) {
    const customerContent = rawText.substring(customerIdx + customerMarker.length).trim();
    return {
      customerReply: sanitizeCustomerReply(customerContent),
    };
  }

  // Fallback: If only internalMarker is found
  if (internalIdx !== -1) {
    const internalContent = rawText.substring(internalIdx + internalMarker.length).trim();
    return {
      customerReply: sanitizeCustomerReply(rawText.substring(0, internalIdx).trim()),
      internalNote: internalContent || undefined,
    };
  }

  // Secondary Fallback: Detect implicit internal analysis sections like "🔍 Internal Analysis" or "### Internal Note"
  const implicitInternalRegex = /(?:\n\s*[-—_]{3,}\s*)?\n\s*(?:#*\s*(?:🔍\s*)?Internal\s+Analysis|#*\s*Internal\s+(?:Note|Memo|Investigation)|#*\s*(?:🤖\s*)?\[?Dify(?:\s+AI)?(?:\s+Support)?(?:\s+Agent)?\s*(?:调查备忘录|内部备忘录|内部分析)\]?|#*\s*(?:内部调查备忘录|内部排查记录|技术排查备忘录))(?::|\s*[-—:]|\s*\n)/i;
  const implicitMatch = rawText.match(implicitInternalRegex);
  if (implicitMatch && implicitMatch.index !== undefined && implicitMatch.index > 0) {
    const beforeContent = rawText.substring(0, implicitMatch.index).trim();
    const afterContent = rawText.substring(implicitMatch.index).replace(/^\s*[-—_]{3,}\s*/, '').trim();
    return {
      customerReply: sanitizeCustomerReply(beforeContent),
      internalNote: afterContent || undefined,
    };
  }

  // Fallback: If no markers were emitted, sanitize whole text for customer
  return {
    customerReply: sanitizeCustomerReply(rawText),
  };
}

// Test cases matching leaked tickets #4366, #4377, #4350
const case4366 = `### Suggested Reply:
Hello,
Currently, Dify does not provide a global toggle to bulk disable or freeze plugin updates.

---
🔍 Internal Analysis:
- Ticket Summary: The customer wants a global setting to freeze all plugin versions.
- Root Cause / Findings: No bulk disable mechanism exists.
- Confidence Score: high
`;

const res4366 = extractDualTrackOutputs(case4366);
console.log('--- Test #4366 ---');
console.log('customerReply:\n', JSON.stringify(res4366.customerReply));
console.log('internalNote:\n', JSON.stringify(res4366.internalNote));

const caseChinese = `尊敬的用户您好，请尝试刷新页面并检查网络。

---
### 内部调查备忘录:
- 查询了 db_gateway 发现无报错记录
`;
const resChinese = extractDualTrackOutputs(caseChinese);
console.log('\n--- Test Chinese ---');
console.log('customerReply:\n', JSON.stringify(resChinese.customerReply));
console.log('internalNote:\n', JSON.stringify(resChinese.internalNote));

// Assertions
if (res4366.customerReply.includes('Internal Analysis') || res4366.customerReply.includes('Suggested Reply')) {
  throw new Error('Leak detected in 4366');
}
if (!res4366.internalNote || !res4366.internalNote.includes('Root Cause / Findings')) {
  throw new Error('Internal note missing in 4366');
}
if (resChinese.customerReply.includes('内部调查备忘录')) {
  throw new Error('Leak detected in Chinese case');
}
if (!resChinese.internalNote || !resChinese.internalNote.includes('db_gateway')) {
  throw new Error('Internal note missing in Chinese case');
}

console.log('\nAll assertions passed successfully!');
