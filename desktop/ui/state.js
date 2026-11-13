/* The window's decisions, without the window.
 *
 * Nothing here touches the DOM or the Tauri bridge, so `tests/test_desktop_state.cjs` can drive
 * it with plain objects. Everything the window *decides* — which buttons are live, what the
 * status line says, whether a server may be stopped — is in this file; `app.js` only paints the
 * result and `lib.rs` only spawns processes.
 *
 * The one rule worth reading twice: **ownership**. A server answering the health check that
 * this window did not spawn belongs to systemd, Docker or a terminal. Stop is refused, and the
 * refusal is explained, because silently doing nothing looks like a broken button.
 */
(function (root) {
  'use strict';

  var LOOPBACK = ['127.0.0.1', 'localhost', '::1', '[::1]'];
  var ANY = ['0.0.0.0', '', '*', '::', '[::]'];
  var STATUS_AFTER_START_MS = 20000;

  function has(list, value) {
    return list.indexOf(value) !== -1;
  }

  function stripBrackets(host) {
    return host.replace(/^\[|\]$/g, '');
  }

  /* Where a client connects, and what the window says about it. `0.0.0.0` is a listen
   * address, not a destination: the server is reachable on loopback and on the LAN, so the URL
   * points at loopback and `scope` says the difference. */
  function formatAddress(input) {
    var address = input || {};
    var host = String(address.host == null ? '127.0.0.1' : address.host).trim();
    var port = Number(address.port == null ? 8470 : address.port);
    var bare = stripBrackets(host);
    var probe = host;
    var scope = 'lan';
    var label = 'Network address';

    if (has(ANY, host)) {
      probe = host === '::' || host === '[::]' ? '::1' : '127.0.0.1';
      scope = 'any';
      label = 'All interfaces';
    } else if (has(LOOPBACK, host) || has(LOOPBACK, bare)) {
      probe = has(LOOPBACK, host) ? host : bare;
      scope = 'loopback';
      label = 'This computer';
    }

    probe = stripBrackets(probe);
    var urlHost = probe.indexOf(':') === -1 ? probe : '[' + probe + ']';
    var listenHost = bare.indexOf(':') === -1 ? bare : '[' + bare + ']';

    return {
      host: host,
      port: port,
      url: 'http://' + urlHost + ':' + port,
      listen: listenHost + ':' + port,
      scope: scope,
      label: label
    };
  }

  function api(formatted, path) {
    var suffix = String(path || '');
    return formatted.url + (suffix.charAt(0) === '/' ? suffix : '/' + suffix);
  }

  function initial(address) {
    return {
      address: address || formatAddress({}),
      health: 'unknown',      /* unknown | starting | up | down */
      ownership: 'none',      /* none | ours | external */
      version: null,
      capabilities: null,
      healthError: '',
      notice: '',
      noticeKind: 'info',
      noticeAt: 0,
      startedAt: 0,
      quitting: false
    };
  }

  function notice(state, text, kind) {
    state.notice = text || '';
    state.noticeKind = text ? (kind || 'info') : 'info';
    state.noticeAt = text ? Date.now() : 0;
    return state;
  }

  function reduce(state, event) {
    var next = Object.assign({}, state);
    var type = event && event.type;

    if (type === 'probe') {
      if (event.health && event.health.ok !== false) {
        next.health = 'up';
        next.version = event.health.version == null ? next.version : String(event.health.version);
        next.healthError = '';
        /* Something is answering. If this window is not holding it, it is not ours, whatever we
         * believed a moment ago. */
        next.ownership = event.owned ? 'ours' : 'external';
        if (event.capabilities) { next.capabilities = event.capabilities; }
        /* A server that is up is not a problem, whatever the last notice was about. */
        if (next.noticeKind !== 'warn') { notice(next, ''); }
      } else {
        var wasStarting = next.health === 'starting';
        var wasOurs = next.ownership === 'ours';
        next.health = 'down';
        next.healthError = event.healthError ? String(event.healthError) : '';
        next.capabilities = null;
        if (!event.owned) {
          /* Nothing answers and this window holds nothing: nobody owns the address any more. */
          next.ownership = 'none';
          if (wasOurs) {
            notice(next, wasStarting
              ? 'The local server exited before it answered ' + next.address.url + '.'
              : 'The local server exited. Nothing is listening at ' + next.address.url + '.',
              'warn');
          }
        }
      }
      return next;
    }

    if (type === 'starting') {
      next.health = 'starting';
      next.ownership = 'ours';
      next.startedAt = Date.now();
      notice(next, 'Starting the local server…');
      return next;
    }

    if (type === 'start') {
      var result = event.result || {};
      if (result.started) {
        next.health = 'starting';
        next.ownership = 'ours';
        next.startedAt = Date.now();
        notice(next, 'Starting the local server…');
        return next;
      }
      next.ownership = result.reason === 'already_running' ? 'external' : next.ownership;
      if (result.reason === 'already_started') {
        next.ownership = 'ours';
        next.health = 'starting';
        next.startedAt = Date.now();
        notice(next, 'The local server is already starting.');
        return next;
      }
      if (result.reason === 'already_running') {
        next.health = 'up';
        notice(next, 'A server is already running at ' + next.address.url +
          '. This window did not start it, so it will not stop it.', 'warn');
        return next;
      }
      if (result.reason === 'no_launcher') {
        next.health = next.health === 'up' ? 'up' : 'down';
        notice(next, 'The server launcher was not found' +
          (result.detail ? ': ' + result.detail : '.') +
          ' Bundle it as the sidecar, or set HARDLYLISTENING_SERVER.', 'warn');
        return next;
      }
      next.health = 'down';
      notice(next, 'The local server could not be started' +
        (result.detail ? ': ' + result.detail : '.') +
        (result.reason ? ' (' + result.reason + ')' : ''), 'warn');
      return next;
    }

    if (type === 'startTimeout') {
      if (next.health !== 'starting') { return next; }
      next.health = 'down';
      next.healthError = 'no answer within ' + Math.round(STATUS_AFTER_START_MS / 1000) + ' s';
      notice(next, 'The local server has not answered ' + next.address.url +
        ' yet. It was started by this window, so Stop is still available.', 'warn');
      return next;
    }

    if (type === 'stop') {
      var stopped = event.result || {};
      if (stopped.stopped) {
        next.ownership = 'none';
        next.health = 'down';
        next.healthError = '';
        next.capabilities = null;
        notice(next, 'Local server stopped.', 'info');
        return next;
      }
      if (stopped.reason === 'not_started_here') {
        next.ownership = next.health === 'up' ? 'external' : 'none';
        notice(next, 'That server was running before this window opened. ' +
          'This window did not start it, so it was left alone.', 'warn');
        return next;
      }
      notice(next, 'The local server was not stopped' +
        (stopped.detail ? ': ' + stopped.detail : '.') +
        (stopped.reason ? ' (' + stopped.reason + ')' : ''), 'warn');
      return next;
    }

    if (type === 'notice') {
      notice(next, event.text || '', event.kind === 'warn' ? 'warn' : 'info');
      return next;
    }

    if (type === 'quit') {
      next.quitting = true;
      return next;
    }

    return next;
  }

  /* What the window shows. Derived, never stored: a button's enabled state cannot then disagree
   * with the status line beside it. */
  function view(state) {
    var status = { text: 'Checking…', kind: 'pending' };
    if (state.health === 'unknown') {
      status = { text: 'Checking…', kind: 'pending' };
    } else if (state.health === 'starting') {
      status = { text: 'Starting…', kind: 'pending' };
    } else if (state.health === 'up') {
      status = {
        text: state.ownership === 'ours'
          ? 'Running (started by this window)'
          : 'Running (started elsewhere)',
        kind: 'up'
      };
    } else {
      status = { text: 'Stopped', kind: 'down' };
    }

    var canStart = state.health !== 'up' && state.health !== 'starting';
    var canStop = state.ownership === 'ours';
    var canOpen = state.health === 'up';

    return {
      status: status,
      address: state.address,
      version: state.version,
      notice: state.notice,
      noticeKind: state.noticeKind,
      healthError: state.healthError,
      capabilities: state.capabilities,
      buttons: {
        start: {
          enabled: canStart,
          hint: canStart ? 'Start the bundled server on ' + state.address.listen
            : (state.ownership === 'ours' ? 'This window already started a server'
              : 'A server is already running here')
        },
        stop: {
          enabled: canStop,
          hint: canStop ? 'Stop the server this window started'
            : 'Only a server this window started can be stopped from here'
        },
        open: {
          enabled: canOpen,
          hint: canOpen ? 'Open ' + state.address.url + ' in your browser'
            : 'The server is not answering yet'
        }
      }
    };
  }

  var Host = {
    initial: initial,
    reduce: reduce,
    view: view,
    formatAddress: formatAddress,
    api: api,
    STATUS_AFTER_START_MS: STATUS_AFTER_START_MS
  };

  if (typeof module === 'object' && module.exports) { module.exports = Host; }
  root.Host = Host;
})(typeof globalThis !== 'undefined' ? globalThis : this);