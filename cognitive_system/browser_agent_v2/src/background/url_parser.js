export function parseUrl(urlString) {
  if (!urlString) {
    return {
      full_url: "",
      domain: "",
      path: ""
    };
  }

  try {
    const parsed = new URL(urlString);
    return {
      full_url: parsed.href,
      domain: parsed.hostname,
      path: parsed.pathname || "/"
    };
  } catch (_error) {
    return {
      full_url: String(urlString),
      domain: "",
      path: ""
    };
  }
}

