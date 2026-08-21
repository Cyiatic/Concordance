async () => {
  const clean = (value) => String(value ?? "").replace(/\s+/g, " ").trim();
  const classes = (element) =>
    element && typeof element.className === "string" ? element.className : "";
  const isHttp = (value) => /^https?:\/\//i.test(String(value ?? ""));
  const slug = (value) => clean(value).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  const displayTimezone = typeof Intl !== "undefined" && Intl.DateTimeFormat
    ? Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"
    : "UTC";
  const mimeFor = (value) => {
    const extension = String(value ?? "").split(/[?#]/, 1)[0].split(".").pop().toLowerCase();
    return {
      png: "image/png",
      jpg: "image/jpeg",
      jpeg: "image/jpeg",
      gif: "image/gif",
      webp: "image/webp",
      svg: "image/svg+xml",
      mp3: "audio/mpeg",
      wav: "audio/wav",
      ogg: "audio/ogg",
      mp4: "video/mp4",
      webm: "video/webm",
      mov: "video/quicktime",
      pdf: "application/pdf",
      txt: "text/plain",
      zip: "application/zip",
    }[extension] || "application/octet-stream";
  };
  const fileName = (value) => {
    try {
      const path = new URL(value, location.href).pathname;
      return path.split("/").filter(Boolean).pop() || "attachment";
    } catch {
      return "attachment";
    }
  };
  const messageId = (article) => {
    const content = article.querySelector('[id^="message-content-"]');
    const fromContent = content?.id?.match(/^message-content-(.+)$/)?.[1];
    if (fromContent) return fromContent;
    return article.getAttribute("data-list-item-id")?.match(/(\d+)$/)?.[1] || null;
  };
  const authorIdFromAvatar = (src) => {
    try {
      return new URL(src, location.href).pathname.match(/\/avatars\/([^/]+)\//)?.[1] || null;
    } catch {
      return null;
    }
  };
  const channelMatch = location.pathname.match(/^\/channels\/@me\/([^/]+)/);
  if (!channelMatch) {
    throw new Error("Open the intended Discord direct message before capturing.");
  }
  const channelId = channelMatch[1];
  const messageNodes = Array.from(document.querySelectorAll('[role="article"][data-list-item-id]'));
  if (!messageNodes.length) {
    throw new Error("No rendered messages were found in the open direct message.");
  }
  let messageScroller = messageNodes[0]?.parentElement || null;
  while (messageScroller && messageScroller !== document.body) {
    const style = getComputedStyle(messageScroller);
    if (messageScroller.scrollHeight > messageScroller.clientHeight + 80 && /(auto|scroll)/.test(style.overflowY)) break;
    messageScroller = messageScroller.parentElement;
  }
  const scrollTop = Number(messageScroller?.scrollTop || 0);
  const scrollHeight = Number(messageScroller?.scrollHeight || 0);
  const viewportHeight = Number(messageScroller?.clientHeight || 0);
  const scrollPosition = {
    scroll_top: scrollTop,
    scroll_height: scrollHeight,
    viewport_height: viewportHeight,
    at_start: !messageScroller || scrollTop <= 4,
    at_end: !messageScroller || scrollTop + viewportHeight >= scrollHeight - 4,
  };

  const participants = new Map();
  const messages = [];
  const attachmentsByMessage = new Map();
  let currentAuthor = null;

  for (const article of messageNodes) {
    const id = messageId(article);
    const timeElement = article.querySelector('time[datetime]');
    const timestamp = timeElement?.getAttribute("datetime");
    if (!id || !timestamp) continue;
    const sourceTimestampLabel = clean(
      timeElement?.parentElement?.parentElement?.querySelector('[class*="hiddenVisually"]')?.innerText
      || timeElement?.parentElement?.parentElement?.querySelector('[class*="hiddenVisually"]')?.textContent,
    );
    const sourceDisplay = {};
    if (sourceTimestampLabel) {
      sourceDisplay.label = sourceTimestampLabel;
      const separator = sourceTimestampLabel.match(/^(.*)\s+at\s+(.+)$/i);
      if (separator) {
        sourceDisplay.date = separator[1].trim();
        sourceDisplay.time = separator[2].trim();
      }
    }

    const usernameContainer = article.querySelector('[id^="message-username-"]');
    const usernameElement = usernameContainer?.querySelector('[class*="username"]') || usernameContainer;
    const avatarElement = article.querySelector('img[class*="avatar"]');
    const avatarRef = avatarElement?.getAttribute("src");
    const avatarId = isHttp(avatarRef) ? authorIdFromAvatar(avatarRef) : null;
    const displayName = clean(usernameElement?.innerText || usernameContainer?.innerText);
    if (usernameContainer || avatarId) {
      const authorId = avatarId || `author-${slug(displayName) || participants.size + 1}`;
      currentAuthor = {
        id: authorId,
        displayName: displayName || authorId,
        avatarRef: isHttp(avatarRef) ? avatarRef : null,
      };
      const participant = participants.get(authorId) || {
        id: authorId,
        display_name: currentAuthor.displayName,
        username: currentAuthor.displayName,
      };
      if (currentAuthor.displayName) {
        participant.display_name = currentAuthor.displayName;
        participant.username = currentAuthor.displayName;
      }
      if (currentAuthor.avatarRef) participant.avatar_ref = currentAuthor.avatarRef;
      participants.set(authorId, participant);
    }
    if (!currentAuthor) continue;

    const contentElement = article.querySelector('[id^="message-content-"]');
    const content = clean(contentElement?.innerText || contentElement?.textContent);
    const message = {
      id: String(id),
      author_id: currentAuthor.id,
      timestamp,
      content,
      grouped: !/\bgroupStart(?:_|\b)/i.test(classes(article)),
      channel_id: channelId,
      attachments: [],
      reactions: [],
      embeds: [],
      message_link: `${location.origin}/channels/@me/${channelId}/${id}`,
    };
    if (Object.keys(sourceDisplay).length) message.source_display = sourceDisplay;

    const accessories = article.querySelector('[id^="message-accessories-"]');
    const attachments = new Map();
    const addAttachment = (url) => {
      if (!isHttp(url) || attachments.has(url)) return;
      const name = fileName(url);
      attachments.set(url, { name, url, mime: mimeFor(name) });
    };
    if (accessories) {
      for (const anchor of accessories.querySelectorAll('a[href]')) {
        const href = anchor.href;
        if (/\/attachments\/\d+\/\d+/i.test(href)) {
          addAttachment(href);
        }
      }
      for (const media of accessories.querySelectorAll('audio[src], video[src], source[src]')) {
        const src = media.getAttribute("src");
        if (/\/attachments\/\d+\/\d+/i.test(src || "")) addAttachment(src);
      }
      if (!attachments.size) {
        for (const image of accessories.querySelectorAll('img[src]')) {
          const src = image.getAttribute("src");
          if (isHttp(src) && /\/attachments\/\d+\/\d+/i.test(src || "")) addAttachment(src);
        }
      }
    }
    message.attachments = Array.from(attachments.values());

    if (accessories) {
      const embedRoots = new Set();
      for (const titleElement of accessories.querySelectorAll('[class*="embedTitle"]')) {
        const root = titleElement.closest('[class*="embed"]') || titleElement.parentElement;
        if (root) embedRoots.add(root);
      }
      for (const root of embedRoots) {
        const titleElement = root.querySelector('[class*="embedTitle"]');
        const descriptionElement = root.querySelector('[class*="embedDescription"]');
        const providerElement = root.querySelector('[class*="embedProvider"]');
        const link = titleElement?.closest('a[href]')?.href || root.querySelector('a[href]')?.href;
        const image = Array.from(root.querySelectorAll('img[src]'))
          .map((element) => element.getAttribute("src"))
          .find((src) => isHttp(src));
        const embed = {};
        const title = clean(titleElement?.innerText || titleElement?.textContent);
        const description = clean(descriptionElement?.innerText || descriptionElement?.textContent);
        const provider = clean(providerElement?.innerText || providerElement?.textContent);
        if (title) embed.title = title;
        if (description) embed.description = description;
        if (provider) embed.site_name = provider;
        if (isHttp(link)) embed.url = link;
        if (isHttp(image) && !/\/attachments\/\d+\/\d+/i.test(image)) embed.image_url = image;
        if (Object.keys(embed).length) message.embeds.push(embed);
      }
    }

    const reactionRoots = Array.from(article.querySelectorAll('[class*="reaction"]')).filter((element) => {
      const text = clean(element.innerText || element.textContent);
      return element.querySelector('[class*="reactionCount"]') || (/\d/.test(text) && text.length < 40);
    });
    const seenReactions = new Set();
    for (const root of reactionRoots) {
      const countElement = root.querySelector('[class*="reactionCount"]');
      const emojiElement = root.querySelector('img[alt], [class*="emoji"], [data-name]');
      const emoji = clean(emojiElement?.getAttribute("alt") || emojiElement?.getAttribute("data-name") || emojiElement?.textContent || root.textContent).replace(/\d+$/, "").trim();
      const countMatch = clean(countElement?.innerText || root.innerText).match(/\d+/);
      const key = `${emoji}:${countMatch?.[0] || "1"}`;
      if (!emoji || seenReactions.has(key)) continue;
      seenReactions.add(key);
      message.reactions.push({
        emoji,
        count: countMatch ? Number.parseInt(countMatch[0], 10) : 1,
        me: /reactionMe|isMe|selected/i.test(classes(root)),
      });
    }

    const replyElement = article.querySelector('[class*="repliedMessage"], [class*="repliedText"]');
    const replyLink = replyElement?.querySelector('a[href*="/channels/"]')?.href;
    const replyId = replyLink?.match(/\/([^/]+?)(?:[?#]|$)/)?.[1];
    if (replyId) message.reply_to = replyId;
    messages.push(message);
  }

  const orderedMessages = [...messages].sort((a, b) => String(a.timestamp).localeCompare(String(b.timestamp)) || String(a.id).localeCompare(String(b.id)));
  const oldestMessage = orderedMessages[0] || null;
  const newestMessage = orderedMessages[orderedMessages.length - 1] || null;
  const captureRange = {
    version: 1,
    message_count: messages.length,
    oldest_message_id: oldestMessage?.id || null,
    oldest_timestamp: oldestMessage?.timestamp || null,
    newest_message_id: newestMessage?.id || null,
    newest_timestamp: newestMessage?.timestamp || null,
    ...scrollPosition,
  };
  const headingElement = document.querySelector('main h1, h1');
  const headingLines = String(headingElement?.innerText || document.title.replace(/^•\s*/, ""))
    .split(/\r?\n+/)
    .map(clean)
    .filter(Boolean);
  const heading = headingLines[0] || `Discord DM ${channelId}`;
  const channelHandle = headingLines[1] || null;
  return {
    metadata: {
      kind: "direct_message",
      title: heading || `Discord DM ${channelId}`,
      channel_handle: channelHandle,
      channel_id: channelId,
      display_timezone: displayTimezone,
      capture_range: captureRange,
      source: {
        label: "Discord visible conversation capture",
        url: location.href.split("#", 1)[0],
        notes: [
          "Captured from the user-opened direct message rendered in the browser.",
          "This capture contains only the messages currently rendered by Discord; load overlapping ranges and merge them to cover a virtualized history.",
          `Capture range: ${messages.length} rendered message(s), ${scrollPosition.at_start ? "at the beginning" : "not at the beginning"}, ${scrollPosition.at_end ? "at the end" : "not at the end"}.`,
          "No login, user token, message sending, account search, or remote asset download was used.",
          "Remote media is preserved as reference-only URLs until explicitly copied into a private archive.",
        ],
      },
    },
    participants: Array.from(participants.values()),
    messages,
  };
}
