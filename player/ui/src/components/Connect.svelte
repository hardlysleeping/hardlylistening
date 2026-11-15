<script lang="ts">
  /* The connect screen, ported from `player/shell/connect.html`.
   *
   * Same card, same fields, same words. The layout is `player/shell/connect.css`, moved into this
   * component's own scoped block: that file styled `p`, `h1`, `label` and `body` by element, which
   * the browser loads unlayered, so importing it here overrode the app's layered component rules
   * on every other screen — a queue row's padding changed because the sign-in page had an opinion
   * about paragraphs. Scoped, it cannot escape.
   *
   * The difference behind it: this talks to the typed client instead of a stub `fetch`, and the
   * answer it reports is the client's error, which already names codes 30/40/50 in a sentence a
   * person can act on.
   */
  import type { Session } from "../lib/app/session.svelte";

  interface Props {
    session: Session;
  }

  let { session }: Props = $props();

  let server = $state("");
  let username = $state("");
  let password = $state("");

  const busy = $derived(session.status === "connecting");
  const message = $derived(session.error ?? "");
  const kind = $derived(session.error ? "err" : "");

  async function submit(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    await session.connect({ server, username, password });
  }
</script>

<div class="connect-screen">
<main class="connect-card">
  <div class="brand">Hardly<span>Listening</span></div>
  <h1>Connect your server</h1>
  <p>This player asks a Subsonic or OpenSubsonic server for your music. Your library stays
    on that server: nothing is downloaded here, and no second catalogue is kept.</p>

  <form id="connect-form" novalidate onsubmit={submit}>
    <label for="server">Server address</label>
    <input id="server" name="server" type="text" inputmode="url" autocomplete="url"
      spellcheck="false" autocapitalize="off" placeholder="https://music.example.com" required
      bind:value={server}>

    <label for="username">Username</label>
    <input id="username" name="username" type="text" autocomplete="username"
      spellcheck="false" autocapitalize="off" required bind:value={username}>

    <label for="password">Password</label>
    <input id="password" name="password" type="password" autocomplete="current-password" required
      bind:value={password}>

    <button type="submit" id="connect-go" class="primary" disabled={busy}>
      {busy ? "Connecting…" : "Connect"}
    </button>
  </form>

  <div id="connect-msg" class="msg {kind}" role="status" aria-live="polite">{message}</div>

  <p class="fine">The server address and username are kept as a plain record; the password goes
    to the system keychain, and only there. A machine with no keychain keeps it in memory for
    this session and says so, rather than writing a secret to disk.</p>
</main>
</div>

<style>
  /* The page frame the host's `connect.html` gets from its own `body`. */
  .connect-screen {
    min-height: 100vh;
    background: var(--bg);
    color: var(--text);
    font: 15px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    display: grid;
    place-items: center;
    padding: 24px;
    padding-bottom: calc(24px + env(safe-area-inset-bottom, 0px));
    touch-action: manipulation;
  }

  .connect-card {
    width: 100%;
    max-width: 380px;
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: var(--radius);
    padding: 22px;
  }

  .brand {
    font-size: 19px;
    font-weight: 700;
    letter-spacing: -0.01em;
  }

  .brand span {
    color: var(--accent);
  }

  main h1 {
    font-size: 15px;
    font-weight: 600;
    margin: 18px 0 6px;
  }

  main p {
    margin: 0 0 14px;
    color: var(--dim);
    font-size: 13px;
  }

  label {
    display: block;
    font-size: 11px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--dim);
    margin: 12px 0 6px;
  }

  /* base.css names text and number inputs; a password field is the same control. */
  input[type="password"] {
    background: var(--panel);
    border: 1px solid var(--line);
    color: var(--text);
    border-radius: 10px;
    padding: 11px 12px;
    font: inherit;
    width: 100%;
    min-height: 42px;
    outline: none;
    -webkit-user-select: text;
    user-select: text;
  }

  input:focus {
    border-color: var(--accent);
  }

  button[type="submit"] {
    width: 100%;
    margin-top: 16px;
  }

  .msg {
    margin-top: 14px;
    font-size: 13px;
    line-height: 1.45;
    min-height: 1em;
  }

  .msg.err {
    color: var(--err);
  }

  .msg.ok {
    color: var(--ok);
  }

  .fine {
    margin: 18px 0 0;
    color: var(--dim);
    font-size: 11.5px;
    line-height: 1.45;
  }

  @media (max-width: 760px) {
    .connect-screen {
      touch-action: pan-x pan-y;
    }
  }
</style>