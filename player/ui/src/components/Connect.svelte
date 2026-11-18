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

  /* A record outlives a sign-out: the address, the username and the keychain's secret are still
     there, so the screen offers them back rather than asking a person to retype their server.
     The form is prefilled from the newest one for the same reason — after a sign-out the only
     thing missing is usually the password. Read once, not per render: this is a starting value. */
  const saved = $derived(session.records);

  let server = $state("");
  let username = $state("");
  let password = $state("");
  let primed = false;

  $effect(() => {
    if (primed) return;
    primed = true;
    const records = session.records;
    const newest = records.length ? records[records.length - 1] : undefined;
    if (!newest) return;
    if (!server) server = newest.server;
    if (!username) username = newest.username;
  });

  /* A saved server can be wrong: a typo that pinged, a host that moved, a machine that is gone.
     A list that only grows keeps the wrong one for good and it fails on every tap, so a row can
     be forgotten. It asks first, the way the sign-out chip does, because it takes the keychain
     entry with it: the first tap turns the ✕ into "Forget?", the second removes the record and
     its secret, and ignoring it puts the ✕ back. */
  let forgetPending = $state<string | null>(null);
  let forgetTimer: ReturnType<typeof setTimeout> | null = null;

  function forgetTap(id: string): void {
    if (forgetPending !== id) {
      forgetPending = id;
      if (forgetTimer) clearTimeout(forgetTimer);
      forgetTimer = setTimeout(() => (forgetPending = null), 5000);
      return;
    }
    if (forgetTimer) clearTimeout(forgetTimer);
    forgetTimer = null;
    forgetPending = null;
    void session.forget(id);
  }

  function forgetKey(event: KeyboardEvent): void {
    if (event.key !== "Escape" || !forgetPending) return;
    event.preventDefault();
    if (forgetTimer) clearTimeout(forgetTimer);
    forgetTimer = null;
    forgetPending = null;
  }

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

  {#if saved.length}
    <div class="saved">
      <p class="saved-title">Saved servers</p>
      {#each saved as account (account.id)}
        <div class="saved-row">
          <button
            type="button"
            class="saved-open"
            disabled={busy}
            onclick={() => void session.activate(account.id)}>
            <span class="saved-name">{account.label}</span>
            <span class="saved-server">{account.server}</span>
          </button>
          <button
            type="button"
            class="saved-forget"
            class:confirming={forgetPending === account.id}
            disabled={busy}
            title={forgetPending === account.id
              ? "Tap again to forget this server"
              : "Forget this server"}
            aria-label={forgetPending === account.id
              ? `Forget ${account.label} and its saved password`
              : `Forget saved server ${account.label}`}
            onkeydown={forgetKey}
            onclick={() => forgetTap(account.id)}>
            {forgetPending === account.id ? "Forget?" : "×"}
          </button>
        </div>
      {/each}
    </div>
    <p class="saved-or">Or connect another</p>
  {/if}

  <form id="connect-form" novalidate onsubmit={submit}>
    <label for="server">Server address</label>
    <input id="server" name="server" type="text" inputmode="url" autocomplete="url"
      spellcheck="false" autocapitalize="off" autocorrect="off" enterkeyhint="next"
      placeholder="https://music.example.com" required bind:value={server}>

    <label for="username">Username</label>
    <input id="username" name="username" type="text" autocomplete="username"
      spellcheck="false" autocapitalize="off" autocorrect="off" enterkeyhint="next" required
      bind:value={username}>

    <label for="password">Password</label>
    <input id="password" name="password" type="password" autocomplete="current-password"
      enterkeyhint="go" required bind:value={password}>

    <button type="submit" id="connect-go" class="primary" disabled={busy}>
      {busy ? "Connecting…" : "Connect"}
    </button>
  </form>

  <div id="connect-msg" class="msg {kind}" role="status" aria-live="polite">{message}</div>

  <p class="fine">The server address and username are kept as a plain record.
    {#if session.vaultMode === "keychain"}
      The password goes to the system keychain, and only there.
    {:else}
      This machine has no keychain this build can reach, so the password is kept in memory for
      this session only — it is never written to disk.
    {/if}</p>
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

  /* The saved servers are a list of buttons, not a menu: one tap is the whole interaction, and
     each row says both what it is called and where it points. */
  .saved {
    display: flex;
    flex-direction: column;
    gap: 6px;
    margin: 4px 0 0;
  }

  .saved-title {
    margin: 0 0 2px;
    font-size: 11px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--dim);
  }

  .saved-row {
    display: flex;
    align-items: stretch;
    gap: 6px;
    width: 100%;
  }

  .saved-open {
    display: flex;
    flex: 1 1 auto;
    flex-direction: column;
    align-items: flex-start;
    gap: 2px;
    min-width: 0;
    padding: 10px 12px;
    text-align: left;
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: 10px;
    color: var(--text);
    font: inherit;
    cursor: pointer;
  }

  .saved-open:hover:not(:disabled) {
    border-color: var(--accent);
  }

  .saved-open:disabled {
    opacity: 0.6;
    cursor: default;
  }

  /* A fixed width on purpose: it is a thumb target on the screen a phone opens on, and it holds
     "Forget?" without the row reflowing under the finger that is about to press it again. */
  .saved-forget {
    flex: 0 0 74px;
    width: 74px;
    padding: 0 6px;
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: 10px;
    color: var(--dim);
    font: inherit;
    font-size: 12px;
    white-space: nowrap;
    cursor: pointer;
  }

  .saved-forget:hover:not(:disabled) {
    color: var(--text);
  }

  .saved-forget.confirming {
    color: var(--text);
    border-color: var(--accent);
  }

  .saved-forget:disabled {
    opacity: 0.6;
    cursor: default;
  }

  .saved-name {
    font-size: 13.5px;
  }

  .saved-server {
    font-size: 11.5px;
    color: var(--dim);
    overflow-wrap: anywhere;
  }

  .saved-or {
    margin: 14px 0 0;
    font-size: 11px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--dim);
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