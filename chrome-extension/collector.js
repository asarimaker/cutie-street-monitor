(() => {
  const marker = /^#cutie-monitor=([A-Za-z0-9_-]{20,})$/.exec(location.hash);
  if (!marker) {
    return;
  }

  const token = marker[1];
  history.replaceState(null, "", `${location.pathname}${location.search}`);

  const sleep = (milliseconds) =>
    new Promise((resolve) => setTimeout(resolve, milliseconds));

  const unique = (values) => [...new Set(values.filter(Boolean))];

  const request = (method, path, body = null) =>
    new Promise((resolve, reject) => {
      chrome.runtime.sendMessage(
        { type: "local-monitor-request", method, path, token, body },
        (response) => {
          if (chrome.runtime.lastError) {
            reject(new Error(chrome.runtime.lastError.message));
          } else if (!response || !response.ok) {
            reject(new Error(response?.error || response?.body?.error || "Local monitor request failed."));
          } else {
            resolve(response.body);
          }
        },
      );
    });

  const statusBox = document.createElement("div");
  Object.assign(statusBox.style, {
    position: "fixed",
    right: "16px",
    bottom: "16px",
    zIndex: "2147483647",
    padding: "12px 16px",
    borderRadius: "10px",
    color: "white",
    background: "rgba(29, 155, 240, 0.95)",
    font: "14px sans-serif",
    boxShadow: "0 4px 18px rgba(0, 0, 0, 0.25)",
  });
  statusBox.textContent = "CUTIE STREET: preparing collection...";
  document.documentElement.appendChild(statusBox);

  const snowflakeDate = (postId) => {
    const timestamp = Number((BigInt(postId) >> 22n) + 1288834974657n);
    return new Date(timestamp).toISOString();
  };

  const parseArticle = (article, username) => {
    if (article.parentElement?.closest("article")) {
      return null;
    }

    const body = article.innerText || "";
    const candidates = [];
    const seen = new Set();
    for (const anchor of article.querySelectorAll('a[href*="/status/"]')) {
      const match = (anchor.getAttribute("href") || "").match(/\/([^/]+)\/status\/(\d+)/i);
      if (!match) continue;
      const key = `${match[1].toLowerCase()}:${match[2]}`;
      if (!seen.has(key)) {
        candidates.push({ author: match[1], id: match[2] });
        seen.add(key);
      }
    }
    if (!candidates.length) return null;

    const firstLines = body.split("\n").slice(0, 5).join("\n");
    const reposted = /reposted|repost|リポストしました|さんがリポスト/i.test(firstLines);
    const own = candidates.find(
      (candidate) => candidate.author.toLowerCase() === username.toLowerCase(),
    );
    const selected = own || (reposted ? candidates[0] : null);
    if (!selected) return null;

    const textNode = article.querySelector('[data-testid="tweetText"], div[lang]');
    const images = unique(
      [...article.querySelectorAll('img[src*="pbs.twimg.com/media/"]')].map(
        (image) => image.currentSrc || image.src,
      ),
    );
    const videos = unique(
      [...article.querySelectorAll("video")]
        .map((video) => video.currentSrc || video.src)
        .filter((url) => url && url.startsWith("http")),
    );
    const replying = /返信先:|Replying to/i.test(body);
    const pinned = /(^|\n)(固定|Pinned)(\n|$)/i.test(firstLines);
    let type = "post";
    if (reposted) type = "repost";
    else if (replying) type = "reply";
    else if (candidates.length > 1) type = "quote";

    return {
      id: selected.id,
      author: selected.author,
      created_at: snowflakeDate(selected.id),
      text: (textNode?.innerText || "").trim(),
      type,
      pinned,
      images,
      has_video: article.querySelector("video, [data-testid='videoPlayer']") !== null,
      videos,
      url: `https://x.com/${selected.author}/status/${selected.id}`,
    };
  };

  const waitForArticles = async (timeoutMilliseconds) => {
    const deadline = Date.now() + timeoutMilliseconds;
    while (Date.now() < deadline) {
      if (document.querySelector("article")) return;
      await sleep(500);
    }
    throw new Error("No posts appeared on the X profile page.");
  };

  const run = async () => {
    const session = await request("GET", "/session");
    const previousIds = new Set(session.previous_ids || []);
    const posts = new Map();
    let overlapFound = false;
    let stalledScrolls = 0;
    let maximumArticleCount = 0;

    await waitForArticles(60_000);
    for (let scrollIndex = 0; scrollIndex <= session.max_scrolls; scrollIndex += 1) {
      const countBefore = posts.size;
      const articles = [...document.querySelectorAll("article")];
      maximumArticleCount = Math.max(maximumArticleCount, articles.length);
      for (const article of articles) {
        const post = parseArticle(article, session.username);
        if (post) posts.set(post.id, post);
      }

      overlapFound = [...posts.values()].some(
        (post) => !post.pinned && previousIds.has(post.id),
      );
      statusBox.textContent = `CUTIE STREET: ${posts.size} posts, scroll ${scrollIndex}`;
      if (overlapFound || scrollIndex >= session.max_scrolls) break;

      stalledScrolls = posts.size === countBefore ? stalledScrolls + 1 : 0;
      if (stalledScrolls >= session.stalled_scroll_limit) break;

      window.scrollBy(0, Math.max(window.innerHeight * 0.85, 800));
      await sleep(session.scroll_delay_ms);
    }

    await request("POST", "/result", {
      token,
      status: "success",
      posts: [...posts.values()],
      overlap_found: overlapFound,
      raw_article_count: maximumArticleCount,
    });
    statusBox.style.background = "rgba(0, 150, 90, 0.95)";
    statusBox.textContent = `CUTIE STREET: completed (${posts.size} posts)`;
  };

  run().catch(async (error) => {
    statusBox.style.background = "rgba(190, 30, 45, 0.95)";
    statusBox.textContent = `CUTIE STREET: ${error.message}`;
    try {
      await request("POST", "/result", {
        token,
        status: "error",
        error: String(error),
      });
    } catch (_) {
      // The PowerShell window will report a missing extension or local server.
    }
  });
})();
