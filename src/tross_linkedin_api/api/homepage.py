"""Self-contained evaluator-facing demo page."""

HOMEPAGE_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="Browserless LinkedIn profile retrieval API demo">
  <title>Tross LinkedIn Profile API</title>
  <style>
    :root {
      color-scheme: light;
      --ink: #171717;
      --muted: #606060;
      --line: #dedbd7;
      --paper: #fffdfa;
      --wash: #f5f2ed;
      --accent: #f4511e;
      --accent-dark: #b82f08;
      --code: #181716;
      --code-ink: #f7f4ef;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      color: var(--ink);
      background:
        radial-gradient(circle at 15% 0%, #fff2e8 0, transparent 35rem),
        var(--wash);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont,
        "Segoe UI", sans-serif;
      line-height: 1.5;
    }
    a { color: var(--accent-dark); text-underline-offset: 0.2em; }
    a:hover { text-decoration-thickness: 0.14em; }
    a:focus-visible, button:focus-visible, input:focus-visible, pre:focus-visible {
      outline: 3px solid #1967d2;
      outline-offset: 3px;
    }
    .shell { width: min(1080px, calc(100% - 32px)); margin: 0 auto; }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 24px;
      padding: 24px 0;
    }
    .brand { display: inline-flex; align-items: center; gap: 10px; font-weight: 800; }
    .mark {
      width: 34px;
      height: 12px;
      background: var(--accent);
      clip-path: polygon(0 70%, 100% 0, 62% 100%, 41% 100%, 54% 54%);
    }
    nav { display: flex; gap: 20px; flex-wrap: wrap; }
    main { padding: 64px 0 80px; }
    .eyebrow {
      margin: 0 0 12px;
      color: var(--accent-dark);
      font-size: 0.82rem;
      font-weight: 800;
      letter-spacing: 0.12em;
      text-transform: uppercase;
    }
    h1 {
      max-width: 820px;
      margin: 0;
      font-size: clamp(2.4rem, 7vw, 5.4rem);
      line-height: 0.98;
      letter-spacing: -0.055em;
    }
    .lede {
      max-width: 700px;
      margin: 24px 0 0;
      color: var(--muted);
      font-size: clamp(1rem, 2vw, 1.22rem);
    }
    .panel {
      margin-top: 48px;
      padding: clamp(22px, 4vw, 38px);
      border: 1px solid var(--line);
      border-radius: 20px;
      background: color-mix(in srgb, var(--paper) 94%, transparent);
      box-shadow: 0 24px 70px rgb(32 25 19 / 9%);
    }
    label { display: block; margin-bottom: 9px; font-weight: 750; }
    .hint { margin: 0 0 16px; color: var(--muted); font-size: 0.92rem; }
    .controls { display: grid; grid-template-columns: 1fr auto; gap: 12px; }
    input, button { min-height: 50px; border-radius: 10px; font: inherit; }
    input {
      width: 100%;
      border: 1px solid #aaa49d;
      padding: 0 15px;
      color: var(--ink);
      background: white;
    }
    input:invalid:not(:placeholder-shown) { border-color: #b42318; }
    button {
      min-width: 156px;
      border: 0;
      padding: 0 22px;
      color: white;
      background: var(--accent-dark);
      font-weight: 800;
      cursor: pointer;
    }
    button:hover { background: #8f2406; }
    button:disabled { cursor: wait; opacity: 0.68; }
    .status { min-height: 24px; margin: 16px 0 0; color: var(--muted); }
    .status.error { color: #a32016; font-weight: 650; }
    .result { margin-top: 22px; }
    .result[hidden] { display: none; }
    .result h2 { margin: 0 0 10px; font-size: 1rem; }
    pre {
      max-height: 560px;
      margin: 0;
      overflow: auto;
      border-radius: 12px;
      padding: 20px;
      color: var(--code-ink);
      background: var(--code);
      font: 0.86rem/1.55 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    .facts {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 1px;
      margin-top: 24px;
      overflow: hidden;
      border: 1px solid var(--line);
      border-radius: 14px;
      background: var(--line);
    }
    .fact { padding: 18px; background: var(--paper); }
    .fact strong { display: block; margin-bottom: 4px; }
    .fact span { color: var(--muted); font-size: 0.9rem; }
    footer { padding: 0 0 36px; color: var(--muted); font-size: 0.9rem; }
    @media (max-width: 700px) {
      header { align-items: flex-start; flex-direction: column; }
      main { padding-top: 36px; }
      .controls, .facts { grid-template-columns: 1fr; }
      button { width: 100%; }
    }
    @media (prefers-reduced-motion: no-preference) {
      .panel { animation: enter 420ms ease-out both; }
      @keyframes enter { from { opacity: 0; transform: translateY(10px); } }
    }
  </style>
</head>
<body>
  <header class="shell">
    <div class="brand"><span class="mark" aria-hidden="true"></span>Tross API</div>
    <nav aria-label="Project links">
      <a href="/docs">API documentation</a>
      <a href="https://github.com/Sanskar84/tross-linkedin-profile-api">Source code</a>
    </nav>
  </header>
  <main class="shell">
    <p class="eyebrow">Engineering hiring challenge</p>
    <h1>LinkedIn profiles,<br>returned as JSON.</h1>
    <p class="lede">
      A browserless FastAPI service that reverse engineers LinkedIn's current
      SSR and SDUI profile flow using direct HTTP requests.
    </p>

    <section class="panel" aria-labelledby="demo-title">
      <h2 id="demo-title">Try the hosted API</h2>
      <form id="profile-form" action="/v1/linkedin/profile" method="post">
        <label for="profile-url">LinkedIn profile URL</label>
        <p class="hint" id="profile-hint">Enter a public linkedin.com/in/ profile URL.</p>
        <div class="controls">
          <input
            id="profile-url"
            name="profile_url"
            type="url"
            inputmode="url"
            autocomplete="url"
            placeholder="https://www.linkedin.com/in/example/"
            aria-describedby="profile-hint"
            required
            pattern="https?://([a-zA-Z0-9-]+\\.)?linkedin\\.com/in/.+"
          >
          <button id="submit-button" type="submit">Retrieve profile</button>
        </div>
      </form>
      <p id="status" class="status" role="status" aria-live="polite"></p>
      <section id="result" class="result" aria-labelledby="result-title" hidden>
        <h2 id="result-title">API response</h2>
        <pre id="json-output" tabindex="0"></pre>
      </section>
    </section>

    <section class="facts" aria-label="Implementation summary">
      <div class="fact">
        <strong>No browser runtime</strong>
        <span>Direct SSR and SDUI requests via curl_cffi.</span>
      </div>
      <div class="fact">
        <strong>Structured output</strong>
        <span>Profile sections normalized into a stable schema.</span>
      </div>
      <div class="fact">
        <strong>Explore the contract</strong>
        <span>Open Swagger docs for schemas and error responses.</span>
      </div>
    </section>
  </main>
  <footer class="shell">Built for the Tross Software Engineering Hiring Challenge.</footer>

  <script>
    const form = document.getElementById("profile-form");
    const input = document.getElementById("profile-url");
    const button = document.getElementById("submit-button");
    const status = document.getElementById("status");
    const result = document.getElementById("result");
    const output = document.getElementById("json-output");

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!form.reportValidity()) return;

      button.disabled = true;
      button.textContent = "Retrieving…";
      status.classList.remove("error");
      status.textContent = "Fetching LinkedIn profile sections. This may take several seconds.";
      result.hidden = true;

      try {
        const response = await fetch(form.action, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ profile_url: input.value.trim() }),
        });
        const payload = await response.json();
        output.textContent = JSON.stringify(payload, null, 2);
        result.hidden = false;
        if (!response.ok) {
          const message = payload?.error?.message || "The API could not retrieve this profile.";
          throw new Error(message);
        }
        status.textContent = "Profile retrieved successfully.";
        output.focus();
      } catch (error) {
        status.classList.add("error");
        status.textContent = error instanceof Error
          ? error.message
          : "Request failed. Please try again.";
      } finally {
        button.disabled = false;
        button.textContent = "Retrieve profile";
      }
    });
  </script>
</body>
</html>
"""
