/* Two independent choices, not one mode switch.
 *
 * `desktop/host/mode.py` is the model, and this is its rendering in the window. The same four
 * combinations, the same ids, the same validation — because the two files have to agree and
 * `tests/test_host_mode.cjs` is what says so.
 *
 * The rule the whole file exists to hold: **the two controls never move each other.** Turning
 * the local server off copies the target unchanged; pointing the window at a remote address
 * copies the local server choice unchanged and starts nothing. There is no code path here in
 * which one control writes the other, and no code path in which an unusable remote address
 * falls back to local — that would look like it worked.
 *
 * No DOM, no Tauri bridge, no fetch: `app.js` paints, `mode.js` decides.
 */
(function (root) {
  'use strict';

  var DEFAULT_RUN_LOCAL_SERVER = true;
  var DEFAULT_TARGET = 'local';
  var TARGETS = ['local', 'remote'];
  var SCHEMES = ['https', 'http'];
  var PREFERRED_SCHEME = 'https';
  var MODE_VERSION = 1;

  var LOOPBACK_NAMES = ['localhost', 'localhost.localdomain', 'ip6-localhost', 'ip6-loopback'];
  var ANY = ['0.0.0.0', '::', '', '*'];

  /* The four ids are the contract with mode.py and both test files. */
  var LOCAL = 'local';
  var LOCAL_BEHIND_REMOTE = 'local-behind-remote';
  var LOCAL_OFF_POINTED_LOCAL = 'local-off-pointed-local';
  var REMOTE_ONLY = 'remote-only';

  function has(list, value) {
    return list.indexOf(value) !== -1;
  }

  function trimSlash(host) {
    return String(host == null ? '' : host).trim().toLowerCase().replace(/\.+$/, '');
  }

  function isLoopback(host) {
    if (has(LOOPBACK_NAMES, host) || has(ANY, host)) { return true; }
    if (host === '::1' || host === '0:0:0:0:0:0:0:1') { return true; }
    if (host.indexOf('127.') === 0) {
      var parts = host.split('.');
      return parts.length === 4 && parts.every(function (part) { return /^\d+$/.test(part); });
    }
    return false;
  }

  function isOwnAddress(host, localHost, machineNames) {
    if (isLoopback(host)) { return true; }
    var own = trimSlash(String(localHost == null ? '' : localHost).replace(/^\[|\]$/g, ''));
    if (own && host === own) { return true; }
    var names = (machineNames || []).map(trimSlash);
    return has(names, host);
  }

  function originUrl(scheme, host, port) {
    var authority = (host.indexOf(':') === -1 ? host : '[' + host + ']') +
      (port == null ? '' : ':' + port);
    return scheme + '://' + authority + '/';
  }

  function warningFor(scheme) {
    if (scheme === PREFERRED_SCHEME) { return ''; }
    return 'This remote address is ' + scheme + ', which is not encrypted. ' +
      PREFERRED_SCHEME + ' is preferred.';
  }

  function readAuthority(authority) {
    if (!authority) { throw new Error('a remote address needs a host'); }
    var host;
    var port = '';
    if (authority.charAt(0) === '[') {
      var end = authority.indexOf(']');
      if (end === -1) {
        throw new Error('a bracketed IPv6 address is missing its closing bracket: ' + authority);
      }
      host = authority.slice(1, end);
      var tail = authority.slice(end + 1);
      if (tail && tail.charAt(0) !== ':') {
        throw new Error(authority + ' is not an address this window can read');
      }
      port = tail.slice(1);
    } else {
      var cut = authority.lastIndexOf(':');
      host = cut === -1 ? authority : authority.slice(0, cut);
      port = cut === -1 ? '' : authority.slice(cut + 1);
      if (host.indexOf(':') !== -1) {
        throw new Error('a bare IPv6 address needs brackets: [' + host + ']');
      }
    }
    host = trimSlash(host);
    if (!host) { throw new Error('a remote address needs a host'); }
    if (!port) { return { host: host, port: null }; }
    if (!/^\d+$/.test(port)) {
      throw new Error('a remote port is not a number: ' + JSON.stringify(port));
    }
    var number = parseInt(port, 10);
    if (!(number > 0 && number < 65536)) {
      throw new Error('a remote port is out of range: ' + number);
    }
    return { host: host, port: number };
  }

  /* Validate and normalise a remote origin. Returns `{ok: true, origin}` or
   * `{ok: false, error}` — no exception crosses into `app.js`, which has to render a message.
   *
   * `example.com` is refused (a scheme says whether to speak TLS); `https` is preferred and
   * `http` is allowed with a warning; one trailing slash is the canonical form; an address that
   * is this computer (loopback, listen-anywhere, the local server's host, this machine's own
   * name) is refused, because that is the local server wearing a remote address.
   */
  function parseOrigin(raw, options) {
    var opts = options || {};
    var text = String(raw == null ? '' : raw).trim();
    if (!text) { return { ok: false, error: 'a remote address is required' }; }
    var marker = text.indexOf('://');
    if (marker === -1) {
      return {
        ok: false,
        error: 'a remote address needs a scheme: write ' + PREFERRED_SCHEME + '://' + text +
          ', not the bare host'
      };
    }
    var scheme = text.slice(0, marker).trim().toLowerCase();
    if (!has(SCHEMES, scheme)) {
      return {
        ok: false,
        error: (scheme || '(none)') + ' is not a scheme this window speaks: ' +
          PREFERRED_SCHEME + ' (preferred) or http'
      };
    }
    var rest = text.slice(marker + 3);
    if (rest.indexOf('#') !== -1 || rest.indexOf('?') !== -1) {
      return { ok: false, error: 'a remote address is an origin: no query string, no #fragment' };
    }
    var cut = rest.indexOf('/');
    var authority = cut === -1 ? rest : rest.slice(0, cut);
    var suffix = cut === -1 ? '' : rest.slice(cut);
    if (authority.indexOf('@') !== -1) {
      return { ok: false, error: 'a remote address carries no credentials' };
    }
    if (suffix.replace(/\/+$/, '')) {
      return { ok: false, error: 'a remote address is an origin, not a path: ' + suffix };
    }
    var parsed;
    try {
      parsed = readAuthority(authority);
    } catch (error) {
      return { ok: false, error: String(error.message || error) };
    }
    if (isOwnAddress(parsed.host, opts.localHost, opts.machineNames)) {
      return {
        ok: false,
        error: parsed.host + ' is this computer: a remote address has to be one another ' +
          'machine reaches. A server here is the local choice.'
      };
    }
    return {
      ok: true,
      origin: {
        scheme: scheme,
        host: parsed.host,
        port: parsed.port,
        url: originUrl(scheme, parsed.host, parsed.port),
        secure: scheme === PREFERRED_SCHEME,
        warning: warningFor(scheme)
      }
    };
  }

  var COMBINATIONS = {};
  function define(combination) {
    var pair = (combination.runLocalServer ? 'on' : 'off') + '/' + combination.target;
    COMBINATIONS[combination.id] = combination;
    COMBINATIONS[pair] = combination;
  }

  define({
    id: LOCAL,
    runLocalServer: true,
    target: 'local',
    talkTo: 'local',
    start: true,
    stop: true,
    summary: 'A local server on this computer, and this window uses it.',
    unreachable: 'The local server at {address} is not answering. Start it from this window.',
    needsAttention: false
  });
  define({
    id: LOCAL_BEHIND_REMOTE,
    runLocalServer: true,
    target: 'remote',
    talkTo: 'remote',
    start: true,
    stop: true,
    summary: 'A local server runs for other devices; this window uses the remote address.',
    unreachable: 'The remote server at {address} is not answering. The local server on this ' +
      'computer keeps running; point the window at local to use it instead.',
    needsAttention: false
  });
  define({
    id: LOCAL_OFF_POINTED_LOCAL,
    runLocalServer: false,
    target: 'local',
    talkTo: 'local',
    start: false,
    stop: false,
    summary: 'This window runs no local server, and it is still pointed at the local address.',
    unreachable: 'Nothing answers {address}: this window is not running a local server. ' +
      'Switch the local server on, or point the window at a remote server.',
    needsAttention: true
  });
  define({
    id: REMOTE_ONLY,
    runLocalServer: false,
    target: 'remote',
    talkTo: 'remote',
    start: false,
    stop: false,
    summary: 'No local server. This window uses the remote address.',
    unreachable: 'The remote server at {address} is not answering. This window runs no local ' +
      'server, and it does not need one: retry when the remote is back, or switch the local ' +
      'server on.',
    needsAttention: false
  });

  function defaults() {
    return { runLocalServer: DEFAULT_RUN_LOCAL_SERVER, target: DEFAULT_TARGET, remoteOrigin: null };
  }

  function copy(current) {
    var base = current || defaults();
    return {
      runLocalServer: base.runLocalServer !== false,
      target: has(TARGETS, base.target) ? base.target : DEFAULT_TARGET,
      remoteOrigin: base.remoteOrigin || null
    };
  }

  function combinationOf(current) {
    var state = copy(current);
    var pair = (state.runLocalServer ? 'on' : 'off') + '/' + state.target;
    var found = COMBINATIONS[pair];
    if (!found) { throw new Error('no combination for ' + pair); }
    return found;
  }

  /* What one pair of answers permits. `start`/`stop` are the choice-level permissions: with the
   * local server switched off there is nothing for them to act on, and the switch is the action.
   * The runtime gate (already running, started by systemd) stays in `state.js` and is applied on
   * top — this model never overrides ownership. */
  function combine(current) {
    var state = copy(current);
    var found = combinationOf(state);
    return {
      id: found.id,
      runLocalServer: state.runLocalServer,
      target: state.target,
      talkTo: found.talkTo,
      start: { enabled: found.start, hint: found.start
        ? 'Start the local server this window runs'
        : 'This window is set to run no local server' },
      stop: { enabled: found.stop, hint: found.stop
        ? 'Stop the local server this window started'
        : 'This window is set to run no local server' },
      summary: found.summary,
      unreachable: found.unreachable,
      needsAttention: found.needsAttention,
      /* Never true, including remote-only: running no local server is a choice, not a fault. */
      degraded: false
    };
  }

  /* The URL the window talks to. `null` means "pointed at a remote with no usable address",
   * which the window says out loud rather than falling back to the local server. */
  function endpoint(current, localUrl) {
    var state = copy(current);
    if (state.target === 'remote') {
      return state.remoteOrigin && state.remoteOrigin.url ? state.remoteOrigin.url : null;
    }
    return localUrl == null ? null : String(localUrl);
  }

  function unreachableText(current, localUrl) {
    var address = endpoint(current, localUrl) || 'the remote address';
    return combinationOf(current).unreachable.replace('{address}', address);
  }

  /* Switching the local server off or on. The target and the remembered remote address are
   * copied verbatim: this function has no access to a target to rewrite. */
  function setLocalServer(current, on) {
    var state = copy(current);
    state.runLocalServer = on !== false;
    return state;
  }

  /* Pointing the window elsewhere. The local server choice is copied verbatim, and nothing is
   * started or stopped here — a value has no process to kill. */
  function setTarget(current, target, remoteOrigin) {
    if (!has(TARGETS, target)) {
      throw new Error(JSON.stringify(target) + ' is not a target this window understands');
    }
    var state = copy(current);
    state.target = target;
    if (remoteOrigin !== undefined) { state.remoteOrigin = remoteOrigin || null; }
    return state;
  }

  /* The two controls, each derived from its own axis only. `local.on` cannot read the target
   * and `target.value` cannot read the local server choice — the coupling is absent, not
   * avoided. */
  function controls(current) {
    var state = copy(current);
    var found = combinationOf(state);
    return {
      local: {
        on: state.runLocalServer === true,
        label: 'Run a local server on this computer',
        hint: state.runLocalServer
          ? 'Other devices on your network can use it while it runs.'
          : 'This window will not start a server here. The window still works: it can use a ' +
            'remote server instead.'
      },
      target: {
        value: state.target,
        options: [
          {
            value: 'local',
            label: 'This computer',
            hint: 'Use the local server address this window resolved.'
          },
          {
            value: 'remote',
            label: 'A remote server',
            hint: 'Use an address another machine answers on, such as a VPS or a tailnet name.'
          }
        ],
        hint: found.talkTo === 'remote'
          ? (state.remoteOrigin && state.remoteOrigin.url
            ? 'The window talks to ' + state.remoteOrigin.url
            : 'No usable remote address yet: the window has nowhere to talk to.')
          : 'The window talks to the local address.'
      }
    };
  }

  function toPayload(current) {
    var state = copy(current);
    return {
      version: MODE_VERSION,
      run_local_server: state.runLocalServer === true,
      target: state.target,
      remote_origin: state.remoteOrigin && state.remoteOrigin.url ? state.remoteOrigin.url : null
    };
  }

  /* The inverse of `toPayload`, with the same asymmetry as mode.py: an unusable stored address
   * leaves the target where it was and reports why. It never silently points the window at the
   * local server instead. */
  function fromPayload(payload, options) {
    var stored = payload && typeof payload === 'object' ? payload : {};
    var problems = [];
    var state = defaults();

    if (stored.version != null && stored.version !== MODE_VERSION) {
      problems.push('this file was written for version ' + stored.version + '; this window ' +
        'reads ' + MODE_VERSION + ' and is using the defaults');
      stored = {};
    }
    if (stored.run_local_server != null) {
      if (typeof stored.run_local_server !== 'boolean') {
        problems.push('run_local_server is not true or false');
      } else {
        state.runLocalServer = stored.run_local_server;
      }
    }
    if (stored.target != null) {
      if (!has(TARGETS, stored.target)) {
        problems.push(JSON.stringify(stored.target) + ' is not a target this window understands');
      } else {
        state.target = stored.target;
      }
    }
    if (stored.remote_origin != null && stored.remote_origin !== '') {
      var parsed = parseOrigin(stored.remote_origin, options);
      if (parsed.ok) { state.remoteOrigin = parsed.origin; }
      else { problems.push('the remote address is unusable: ' + parsed.error); }
    }
    if (state.target === 'remote' && !state.remoteOrigin) {
      problems.push('this window is pointed at a remote server but has no usable address');
    }
    return { mode: state, problem: problems.join('; ') };
  }

  var Mode = {
    defaults: defaults,
    parseOrigin: parseOrigin,
    combine: combine,
    combinationOf: combinationOf,
    endpoint: endpoint,
    unreachableText: unreachableText,
    setLocalServer: setLocalServer,
    setTarget: setTarget,
    controls: controls,
    toPayload: toPayload,
    fromPayload: fromPayload,
    TARGETS: TARGETS,
    IDs: {
      LOCAL: LOCAL,
      LOCAL_BEHIND_REMOTE: LOCAL_BEHIND_REMOTE,
      LOCAL_OFF_POINTED_LOCAL: LOCAL_OFF_POINTED_LOCAL,
      REMOTE_ONLY: REMOTE_ONLY
    },
    MODE_VERSION: MODE_VERSION
  };

  if (typeof module === 'object' && module.exports) { module.exports = Mode; }
  root.Mode = Mode;
})(typeof globalThis !== 'undefined' ? globalThis : this);