/**
 * Rule-based semantic classification.
 *
 * These are intentionally simple heuristics — the purpose is to give the GNN
 * pipeline human-readable labels without requiring ML in the extension.
 *
 * Both classifiers are pure functions: same input → same output, no side-effects.
 */
import { SITE_TYPES, TASK_HINTS } from '../shared/constants.js';

// ─────────────────────────────────────────────────────────────────────────────
// Site-type rules (ordered; first match wins)
// ─────────────────────────────────────────────────────────────────────────────
const SITE_TYPE_RULES = [
  // Development — match before social to handle github correctly
  {
    pattern: /^(github|gitlab|bitbucket)\.(?:com|org)|stackoverflow\.com|codepen\.io|codesandbox\.io|replit\.com|jsfiddle\.net/,
    type: SITE_TYPES.DEVELOPMENT,
  },
  // Search engines
  {
    pattern: /^(www\.)?google\.[a-z.]+\/search|^(www\.)?bing\.com\/search|^(www\.)?duckduckgo\.com|^(www\.)?search\.yahoo\.com/,
    type: SITE_TYPES.SEARCH,
  },
  // Communication (before social — Slack/Discord have social-like domains)
  {
    pattern: /^(mail\.google|gmail)\.com|^(app\.)?slack\.com|^discord\.com|^teams\.microsoft\.com|^(outlook|mail)\.(live|microsoft|office)\.com/,
    type: SITE_TYPES.COMMUNICATION,
  },
  // Social
  {
    pattern: /^(www\.)?(twitter|x)\.com|^(www\.)?facebook\.com|^(www\.)?instagram\.com|^(www\.)?reddit\.com|^(www\.)?linkedin\.com|^(www\.)?tiktok\.com/,
    type: SITE_TYPES.SOCIAL,
  },
  // Entertainment
  {
    pattern: /^(www\.)?youtube\.com|^(www\.)?netflix\.com|^(www\.)?twitch\.tv|^(www\.)?vimeo\.com|^(www\.)?dailymotion\.com|^(www\.)?spotify\.com/,
    type: SITE_TYPES.ENTERTAINMENT,
  },
  // Productivity / docs
  {
    pattern: /^docs\.google\.com|^(www\.)?notion\.so|confluence|jira\.|^(app\.)?trello\.com|^(app\.)?asana\.com|^airtable\.com|^(www\.)?figma\.com/,
    type: SITE_TYPES.PRODUCTIVITY,
  },
  // Shopping
  {
    pattern: /^(www\.)?amazon\.|^(www\.)?ebay\.com|^(www\.)?etsy\.com|^(www\.)?shopify\.com|^(www\.)?aliexpress\.com/,
    type: SITE_TYPES.SHOPPING,
  },
  // Education
  {
    pattern: /^(www\.)?coursera\.org|^(www\.)?udemy\.com|^(www\.)?edx\.org|^(www\.)?khanacademy\.org|^(en\.)?wikipedia\.org|^(www\.)?arxiv\.org/,
    type: SITE_TYPES.EDUCATION,
  },
  // News / long-form reading
  {
    pattern: /^(www\.)?medium\.com|^(www\.)?substack\.com|^(www\.)?bbc\.(co\.uk|com)|^(www\.)?cnn\.com|^(www\.)?nytimes\.com|^(www\.)?theguardian\.com/,
    type: SITE_TYPES.NEWS,
  },
];

// ─────────────────────────────────────────────────────────────────────────────
// Task-hint rules (ordered; domain + path checked together)
// ─────────────────────────────────────────────────────────────────────────────
const TASK_HINT_RULES = [
  { pattern: /github\.com|gitlab\.com|bitbucket\.org|stackoverflow\.com/,          hint: TASK_HINTS.CODING        },
  { pattern: /youtube\.com\/watch|netflix\.com\/watch|twitch\.tv\//,                hint: TASK_HINTS.WATCHING      },
  { pattern: /google\.[a-z.]+\/search|bing\.com\/search|duckduckgo\.com\/?[?&]q=/, hint: TASK_HINTS.SEARCHING     },
  { pattern: /docs\.google\.com\/document|notion\.so|medium\.com\/@|substack\.com/, hint: TASK_HINTS.WRITING      },
  { pattern: /gmail\.com|outlook\.|slack\.com|discord\.com/,                        hint: TASK_HINTS.COMMUNICATING },
  { pattern: /wikipedia\.org|coursera\.org|udemy\.com|medium\.com|arxiv\.org/,      hint: TASK_HINTS.READING      },
  { pattern: /youtube\.com|netflix\.com|spotify\.com|twitch\.tv/,                   hint: TASK_HINTS.WATCHING     },
];

/**
 * Classify site type from the domain string.
 * @param {string} domain  e.g. "github.com"
 * @returns {string}  one of SITE_TYPES values
 */
export function classifySiteType(domain) {
  if (!domain) return SITE_TYPES.UNKNOWN;
  for (const { pattern, type } of SITE_TYPE_RULES) {
    if (pattern.test(domain)) return type;
  }
  return SITE_TYPES.UNKNOWN;
}

/**
 * Infer task hint from domain + path.
 * @param {string} domain  e.g. "github.com"
 * @param {string} path    e.g. "/user/repo/issues"
 * @returns {string}  one of TASK_HINTS values
 */
export function classifyTaskHint(domain, path) {
  const subject = (domain || '') + (path || '');
  if (!subject) return TASK_HINTS.UNKNOWN;
  for (const { pattern, hint } of TASK_HINT_RULES) {
    if (pattern.test(subject)) return hint;
  }
  return TASK_HINTS.BROWSING;
}
