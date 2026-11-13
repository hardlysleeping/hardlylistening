/* The window: paint what `state.js` decided, send what the buttons mean.
 *
 * There is no logic here worth testing — the reducer and the address formatter are in `state.js`
 * precisely so that they can be. If you find yourself adding a rule to this file, it belongs in
 * the reducer instead, and in `tests/test_desktop_state.cjs`.
 */
(function () {
  'use strict';

  var Host = window.Host;
  var POLL_MS = 2000;
  var bridge = (window.__TAURI__ && window.__TAURI__.core) || null;

  function $(id) { return document.getElementById(id); }

  var els = {
    statusPill: $('status-pill'),
    statusText: $('status-text'),
    address: $('address'),
    addressHint: $('address-hint'),
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
    config: $('fact-config')
  };

  var state = Host.initial(Host.formatAddress({ host: '127.0.0.1', port: 8470 }));
  var polling = false;

  function dispatch(event) {
    state = Host.reduce(state, event);
    render();
  }

  function text(node, value) {
    var next = value == null || value === '' ? '—' : String(value);
    if (node.textContent !== next) { node.textContent = next; }
    node.classList.toggle('empty', next === '—');
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
  }

  function call(command) {
    return bridge.invoke(command);
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
    }).catch(function (error) {
      dispatch({ type: 'probe', owned: false, health: null, healthError: String(error) });
    }).then(function () {
      polling = false;
    });
  }

  function explain(result, failureText) {
    dispatch({ type: 'notice', text: failureText + (result && result.detail ? ': ' + result.detail : '.'), kind: 'warn' });
  }

  els.start.addEventListener('click', function () {
    if (!bridge) { return; }
    call('server_start').then(function (result) {
      dispatch({ type: 'start', result: result || {} });
      poll();
    }).catch(function (error) { explain(null, 'The local server could not be started: ' + error); });
  });

  els.stop.addEventListener('click', function () {
    if (!bridge) { return; }
    call('server_stop').then(function (result) {
      dispatch({ type: 'stop', result: result || {} });
      poll();
    }).catch(function (error) { explain(null, 'The local server was not stopped: ' + error); });
  });

  els.open.addEventListener('click', function () {
    if (!bridge) { return; }
    call('open_web_ui').then(function (url) {
      dispatch({ type: 'notice', text: 'Opened ' + (url || state.address.url) + ' in your browser.' });
    }).catch(function (error) { explain(null, 'The browser could not be opened: ' + error); });
  });

  els.quit.addEventListener('click', function () {
    if (!bridge) { return; }
    dispatch({ type: 'quit' });
    call('quit');
  });

  function boot() {
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
      poll();
      window.setInterval(poll, POLL_MS);
    }).catch(function (error) {
      dispatch({ type: 'notice', text: 'The shell could not read its own environment: ' + error, kind: 'warn' });
    });
  }

  render();
  boot();
})();