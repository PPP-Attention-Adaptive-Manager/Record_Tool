/**
 * Parses a raw URL string into the structured fields required by the data model.
 *
 * Domain alone is insufficient for downstream semantic analysis; we preserve
 * full path and query so pipeline stages can reconstruct intent (e.g.
 * github.com/user/repo vs github.com/explore are meaningfully different tasks).
 */

/** URL schemes that carry no useful behavioral signal. */
const SKIP_SCHEMES = new Set(['chrome:', 'chrome-extension:', 'devtools:', 'about:', 'data:']);

export function parseURL(rawUrl) {
  const empty = { full_url: rawUrl || '', domain: '', path: '', query_string: '' };

  if (!rawUrl) return empty;

  // Reject internal browser URLs early — they contain no useful domain signal
  try {
    const scheme = rawUrl.split(':')[0] + ':';
    if (SKIP_SCHEMES.has(scheme)) return empty;
  } catch {
    return empty;
  }

  try {
    const u = new URL(rawUrl);
    return {
      full_url:     rawUrl,
      domain:       u.hostname,          // e.g. "github.com"
      path:         u.pathname,          // e.g. "/anthropics/claude-code"
      query_string: u.search,            // e.g. "?tab=issues"
    };
  } catch {
    // Malformed URL — return best-effort
    return empty;
  }
}
