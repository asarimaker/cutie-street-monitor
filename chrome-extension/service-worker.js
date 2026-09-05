const LOCAL_API = "http://127.0.0.1:8765";

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message || message.type !== "local-monitor-request") {
    return false;
  }

  const allowedPaths = new Set(["/session", "/result"]);
  if (!allowedPaths.has(message.path)) {
    sendResponse({ ok: false, error: "Unsupported local API path." });
    return false;
  }

  const options = {
    method: message.method === "POST" ? "POST" : "GET",
    headers: { "Content-Type": "application/json" },
  };
  if (options.method === "POST") {
    options.body = JSON.stringify(message.body || {});
  }

  const query = new URLSearchParams({ token: message.token }).toString();
  fetch(`${LOCAL_API}${message.path}?${query}`, options)
    .then(async (response) => {
      const body = await response.json();
      sendResponse({ ok: response.ok, body });
    })
    .catch((error) => sendResponse({ ok: false, error: String(error) }));

  return true;
});
