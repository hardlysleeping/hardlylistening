/* The window: paint what the modules decided, send what the buttons mean.
 *
 * There is no logic here worth testing — the reducer and the address formatter are in `state.js`,
 * the two choices in `mode.js`, the reachable address in `address.js`, the lifecycle wording in
 * `lifecycle.js` and the first run in `wizard.js`, precisely so that they can be. If you find
 * yourself adding a rule to this file, it belongs in one of those, with its test.
 */
(function () {
  'use strict';

  var Host = window.Host;
  var Mode = window.Mode;
  var HostAddress = window.HostAddress;
  var Lifecycle = window.Lifecycle;
  var Wizard = window.Wizard;
  var POLL_MS = 2000;
  var bridge = (window.__TAURI__ && window.__TAURI__.core) || null;

  function $(id) { return document.getElementById(id); }

  var els = {
    statusPill: $('status-pill'),
    statusText: $('status-text'),
    address: $('address'),
    addressHint: $('address-hint'),
    share: $('share-address'),
    shareHint: $('share-hint'),
    copy: $('copy-address'),
    healthError: $('health-error'),
    start: $('start'),
    stop: $('stop'),
    open: $('open'),
    quit: $('quit'),
    notice: $('notice'),
    version: $('fact-version'),
    api: $('fact-api'),
    protocol: $('fact-protocol'),
    members: $('fact-members'),
    shell: $('fact-shell'),
    launcher: $('fact-launcher'),
    sidecar: $('fact-sidecar'),
    config: $('fact-config'),
    modeLocal: $('mode-local'),
    modeLocalHint: $('mode-local-hint'),
    modeTargetLocal: $('mode-target-local'),
    modeTargetRemote: $('mode-target-remote'),
    modeTargetHint: $('mode-target-hint'),
    modeOrigin: $('mode-origin'),
    modeProblem: $('mode-problem')
  };

  var state = Host.initial(Host.formatAddress({ host: '127.0.0.1', port: 8470 }));
  var polling = false;

  /* HOST-006: silence until the shell says otherwise. `address.js` refuses to render an answer
   * it cannot place, so "nothing" is the correct initial state, not a guess. */
  var share = { silent: true, url: '', copy: '', label: '', why: '', note: '', others: 0 };

  /* HOST-004: the two choices, remembered as the payload `mode.py` documents. The store is the
   * window's own; the Rust side does not write `mode.json` yet. */
  var MODE_KEY = 'hardlylistening.desktop.mode';
  var mode = Mode.defaults();
  var modeProblem = '';

  /* HOST-003: the lifecycle's own view of a start, a stop and a close. */
  var lc = Lifecycle.initial(null);

  function dispatch(event) {
    state = Host.reduce(state, event);
    render();
  }

  function text(node, value) {
    var next = value == null || value === '' ? '—' : String(value);
    if (node.textContent !== next) { node.textContent = next; }
    node.classList.toggle('empty', next === '—');
  }

  function renderShare() {
    var shown = !share.silent && !!share.url;
    els.share.hidden = !shown;
    els.share.textContent = shown ? share.url : '';
    els.shareHint.hidden = !shown && !share.note;
    els.shareHint.textContent = shown
      ? share.label + ' · ' + share.why + (share.others ? ' (' + share.others + ' more)' : '')
      : share.note;
    els.copy.disabled = !shown;
    els.copy.title = shown ? 'Copy ' + share.url : 'No reachable address to copy';
  }

  function render() {
    var view = Host.view(state);
    els.statusText.textContent = view.status.text;
    els.statusPill.dataset.state = view.status.kind;

    els.address.textContent = view.address.url;
    els.addressHint.textContent = view.address.label + ' · listening on ' + view.address.listen;

    els.healthError.hidden = !view.healthError;
    els.healthError.textContent = view.healthError ? 'Last answer: ' + view.healthError : '';

    els.notice.hidden = !view.notice;
    els.notice.textContent = view.notice;
    els.notice.dataset.kind = view.noticeKind;

    var buttons = view.buttons;
    [['start', buttons.start], ['stop', buttons.stop], ['open', buttons.open]].forEach(function (pair) {
      var node = els[pair[0]];
      node.disabled = !pair[1].enabled;
      node.title = pair[1].hint;
    });

    text(els.version, view.version);
    var caps = view.capabilities || {};
    var server = caps.server || {};
    var subsonic = caps.subsonic || {};
    text(els.api, server.api);
    text(els.protocol, subsonic.protocol_version);
    text(els.members, server.requires_members === true ? 'required'
      : server.requires_members === false ? 'not required' : null);

    var choices = Mode.controls(mode);
    els.modeLocal.checked = choices.local.on;
    els.modeLocalHint.textContent = choices.local.hint;
    els.modeTargetLocal.checked = choices.target.value === 'local';
    els.modeTargetRemote.checked = choices.target.value === 'remote';
    els.modeTargetHint.textContent = choices.target.hint;
    if (document.activeElement !== els.modeOrigin) {
      els.modeOrigin.value = mode.remoteOrigin ? mode.remoteOrigin.url : '';
    }
    var problems = [];
    if (mode.remoteOrigin && mode.remoteOrigin.warning) { problems.push(mode.remoteOrigin.warning); }
    if (modeProblem) { problems.push(modeProblem); }
    if (Mode.combine(mode).needsAttention) { problems.push(Mode.combine(mode).summary); }
    els.modeProblem.hidden = !problems.length;
    els.modeProblem.textContent = problems.join(' ');

    renderShare();
    Lifecycle.paint(document, Lifecycle.view(lc));
  }

  function call(command, args) {
    return bridge.invoke(command, args);
  }

  function lifecycleDispatch(event) {
    lc = Lifecycle.reduce(lc, event);
    Lifecycle.paint(document, Lifecycle.view(lc));
  }

  function lifecyclePoll() {
    if (!bridge) { return; }
    call('lifecycle_step', { action: 'state' }).then(function (snapshot) {
      lifecycleDispatch({ type: 'snapshot', snapshot: snapshot || {} });
      if (lc.phase === 'starting' && Date.now() - lc.startedAt > Lifecycle.START_DEADLINE_MS) {
        lifecycleDispatch({ type: 'deadline' });
      }
    }).catch(function () {});
  }

  function poll() {
    if (!bridge || polling) { return; }
    polling = true;
    call('server_status').then(function (status) {
      status = status || {};
      /* The address is the server's answer (or the resolver's), never a second opinion. */
      if (status.address) { state.address = Host.formatAddress(status.address); }
      dispatch({
        type: 'probe',
        owned: status.owned === true,
        health: status.health || null,
        healthError: status.health_error || '',
        capabilities: status.capabilities || null
      });
      if (state.health === 'starting' && Date.now() - state.startedAt > Host.STATUS_AFTER_START_MS) {
        dispatch({ type: 'startTimeout' });
      }
      lifecyclePoll();
    }).catch(function (error) {
      dispatch({ type: 'probe', owned: false, health: null, healthError: String(error) });
    }).then(function () {
      polling = false;
    });
  }

  function explain(result, failureText) {
    dispatch({ type: 'notice', text: failureText + (result && result.detail ? ': ' + result.detail : '.'), kind: 'warn' });
  }

  function rememberMode(next) {
    mode = next;
    modeProblem = '';
    try { window.localStorage.setItem(MODE_KEY, JSON.stringify(Mode.toPayload(next))); }
    catch (error) { /* no storage: the window still works, it just forgets */ }
    render();
  }

  function storedMode() {
    try { return JSON.parse(window.localStorage.getItem(MODE_KEY) || 'null'); }
    catch (error) { return null; }
  }

  els.start.addEventListener('click', function () {
    if (!bridge) { return; }
    call('lifecycle_step', { action: 'start' }).then(function (result) {
      lifecycleDispatch({ type: 'start', result: result || {} });
      poll();
    }).catch(function (error) { explain(null, 'The local server could not be started: ' + error); });
  });

  els.stop.addEventListener('click', function () {
    if (!bridge) { return; }
    call('lifecycle_step', { action: 'stop' }).then(function (result) {
      lifecycleDispatch({ type: 'stop', result: result || {} });
      poll();
    }).catch(function (error) { explain(null, 'The local server was not stopped: ' + error); });
  });

  els.open.addEventListener('click', function () {
    if (!bridge) { return; }
    call('open_web_ui').then(function (url) {
      dispatch({ type: 'notice', text: 'Opened ' + (url || state.address.url) + ' in your browser.' });
    }).catch(function (error) { explain(null, 'The browser could not be opened: ' + error); });
  });

  els.copy.addEventListener('click', function () {
    if (!share.copy) { return; }
    var value = share.copy;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(value).then(function () {
        dispatch({ type: 'notice', text: 'Copied ' + value + '.' });
      }).catch(function (error) { explain(null, 'The address could not be copied: ' + error); });
      return;
    }
    dispatch({ type: 'notice', text: value, kind: 'warn' });
  });

  /* Two controls, two axes. Each handler passes its own axis and lets `mode.js` copy the other:
   * turning the server off cannot rewrite the target, and switching the target cannot stop a
   * server. */
  els.modeLocal.addEventListener('change', function () {
    rememberMode(Mode.setLocalServer(mode, els.modeLocal.checked));
  });

  [['local', els.modeTargetLocal], ['remote', els.modeTargetRemote]].forEach(function (pair) {
    pair[1].addEventListener('change', function () {
      if (pair[1].checked) { rememberMode(Mode.setTarget(mode, pair[0])); }
    });
  });

  /* Typing an address remembers it and changes nothing else — not the target, not the server. */
  els.modeOrigin.addEventListener('change', function () {
    if (!els.modeOrigin.value.trim()) {
      rememberMode(Mode.setTarget(mode, mode.target, null));
      return;
    }
    var parsed = Mode.parseOrigin(els.modeOrigin.value, { localHost: state.address.host });
    if (!parsed.ok) {
      modeProblem = parsed.error;
      els.modeOrigin.value = mode.remoteOrigin ? mode.remoteOrigin.url : '';
      render();
      return;
    }
    rememberMode(Mode.setTarget(mode, mode.target, parsed.origin));
  });

  els.quit.addEventListener('click', function () {
    if (!bridge) { return; }
    lifecycleDispatch({ type: 'close', decision: { action: Lifecycle.closeDecision(lc).action } });
    call('quit');
  });

  function boot() {
    if (!bridge) { return bootShell(); }
    Wizard.start({ bridge: bridge, onFinish: bootShell }).then(function (result) {
      if (result && result.show === 'shell') { bootShell(); }
    });
  }

  function bootShell() {
    var restored = Mode.fromPayload(storedMode());
    mode = restored.mode;
    modeProblem = restored.problem;

    if (!bridge) {
      render();
      dispatch({
        type: 'notice',
        text: 'This page is open outside the desktop shell, so it cannot start or stop anything. ' +
          'Run the window with `cargo tauri dev`, or open the server’s own UI in a browser.',
        kind: 'warn'
      });
      return;
    }
    call('host_env').then(function (env) {
      env = env || {};
      if (env.address) { state.address = Host.formatAddress(env.address); }
      text(els.shell, env.app_version ? 'desktop ' + env.app_version : null);
      text(els.launcher, env.launcher);
      text(els.sidecar, env.sidecar);
      text(els.config, env.config_path);
      if (env.error) {
        dispatch({ type: 'notice', text: 'The server address is unusable: ' + env.error, kind: 'warn' });
      } else if (env.config_problem) {
        dispatch({ type: 'notice', text: env.config_problem + ' — using the defaults.', kind: 'warn' });
      }
      if (env.launcher_exists === false) {
        dispatch({
          type: 'notice',
          text: 'No server launcher was found. Bundle bin/hardlylistening-server as the sidecar, ' +
            'or set HARDLYLISTENING_SERVER to a checkout.',
          kind: 'warn'
        });
      }
      render();
      /* HOST-006: the detector decides which address is reachable; this only paints its answer.
       * A detector that could not run leaves `share` silent, which is the correct output. */
      call('host_addresses').then(function (answer) {
        share = HostAddress.describe(answer, state.address && state.address.port);
        render();
      }).catch(function () {});
      poll();
      Lifecycle.attach(document, call);
      lifecyclePoll();
      window.setInterval(poll, POLL_MS);
    }).catch(function (error) {
      dispatch({ type: 'notice', text: 'The shell could not read its own environment: ' + error, kind: 'warn' });
    });
  }

  render();
  boot();
})();