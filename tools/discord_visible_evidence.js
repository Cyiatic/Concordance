/*
 * Read-only evidence helper for an explicitly user-opened Discord Web DM.
 * Evaluate this source in the same visible tab as discord_visible_capture.js.
 * It returns a rendered DOM artifact; it never reads storage, cookies, tokens,
 * private APIs, or remote assets.
 */
async (options = {}) => {
  const clean = (value) => String(value ?? "").replace(/\s+/g, " ").trim();
  const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "'": "&#39;",
    '"': "&quot;",
  }[character]));
  const channelMatch = location.pathname.match(/^\/channels\/@me\/([^/]+)/);
  if (!channelMatch) {
    throw new Error("Open the intended Discord direct message before collecting evidence.");
  }
  const selector = typeof options.scope_selector === "string" && options.scope_selector.trim()
    ? options.scope_selector.trim()
    : "main";
  let root = null;
  try {
    root = document.querySelector(selector);
  } catch {
    throw new Error("Evidence scope selector is invalid.");
  }
  if (!root) throw new Error(`Evidence scope was not found: ${selector}`);

  const clone = root.cloneNode(true);
  clone.querySelectorAll("script, style, noscript, iframe, object, embed, link, meta").forEach((node) => node.remove());
  const inertUrlAttributes = new Set(["src", "srcset", "poster", "action", "formaction", "xlink:href", "data-src", "data-srcset"]);
  const sanitizeNode = (node) => {
    for (const attribute of Array.from(node.attributes || [])) {
      const name = attribute.name.toLowerCase();
      if (name.startsWith("on") || name === "style" || inertUrlAttributes.has(name)) {
        node.removeAttribute(attribute.name);
        continue;
      }
      if (name === "href" && /^(?:javascript|vbscript|data|blob):/i.test(attribute.value.trim())) {
        node.removeAttribute(attribute.name);
      }
    }
  };
  sanitizeNode(clone);
  clone.querySelectorAll("*").forEach(sanitizeNode);
  const messageIds = Array.from(root.querySelectorAll('[role="article"][data-list-item-id]'))
    .map((node) => node.getAttribute("data-list-item-id")?.match(/(\d+)$/)?.[1] || null)
    .filter(Boolean);
  let sourceUrl = location.href.split("#", 1)[0];
  try {
    const parsedSourceUrl = new URL(location.href);
    parsedSourceUrl.search = "";
    parsedSourceUrl.hash = "";
    sourceUrl = parsedSourceUrl.toString();
  } catch {
    // Keep the fragment-free fallback for unusual browser URL implementations.
  }
  const html = `<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; base-uri 'none'; form-action 'none'; script-src 'none'; style-src 'none'; img-src 'none'; media-src 'none'; font-src 'none'; connect-src 'none'; object-src 'none'; frame-src 'none';"><title>${escapeHtml(clean(document.title))}</title></head><body>${clone.outerHTML}</body></html>`;
  return {
    evidence_version: 1,
    kind: "dom",
    captured_at: new Date().toISOString(),
    source_url: sourceUrl,
    channel_id: channelMatch[1],
    title: clean(document.title),
    scope_selector: selector,
    rendered_message_ids: messageIds,
    html,
    note: "Rendered DOM snapshot from the user-opened Discord DM; save the html value under the private capture-session directory before attaching it to a range.",
  };
}
