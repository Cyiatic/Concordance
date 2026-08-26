/*
 * Read-only evidence helper for an explicitly user-opened Discord Web DM.
 * Evaluate this source in the same visible tab as discord_visible_capture.js.
 * It returns a rendered DOM artifact; it never reads storage, cookies, tokens,
 * private APIs, or remote assets.
 */
async (options = {}) => {
  const clean = (value) => String(value ?? "").replace(/\s+/g, " ").trim();
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
  clone.querySelectorAll("script, style, noscript").forEach((node) => node.remove());
  const messageIds = Array.from(root.querySelectorAll('[role="article"][data-list-item-id]'))
    .map((node) => node.getAttribute("data-list-item-id")?.match(/(\d+)$/)?.[1] || null)
    .filter(Boolean);
  const html = `<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><title>${clean(document.title)}</title></head><body>${clone.outerHTML}</body></html>`;
  return {
    evidence_version: 1,
    kind: "dom",
    captured_at: new Date().toISOString(),
    source_url: location.href.split("#", 1)[0],
    channel_id: channelMatch[1],
    title: clean(document.title),
    scope_selector: selector,
    rendered_message_ids: messageIds,
    html,
    note: "Rendered DOM snapshot from the user-opened Discord DM; save the html value under the private capture-session directory before attaching it to a range.",
  };
}
