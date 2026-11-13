/* The one address worth copying, and only when there is one.
 *
 * `desktop/host/addresses.py` decides *which* address is usable — the precedence, the bind
 * check and the tailnet read all live there, and are tested in `tests/test_host_addresses.py`.
 * This file decides what a person sees: the URL to copy, which kind of address it is, and the
 * sentence explaining that choice. Splitting it that way keeps one precedence, not two.
 *
 * The renderer is deliberately suspicious of its input. Anything it cannot place — no primary,
 * an unknown kind, a loopback address wearing a LAN label — renders as *nothing*, because a
 * window that invents a plausible address is worse than a window that says nothing. The shell
 * may hand this file a stale or half-filled answer (the host command failed, the server just
 * restarted); silence is the correct output for all of those.
 */
(function (root) {
  'use strict';

  var DEFAULT_PORT = 8470;

  /* Every kind `addresses.py` can produce, with the label and the sentence used when the host
   * did not supply a reason of its own. The wording is the window's, not the detector's: the
   * detector says *what* it found, this says what it means to the person reading it. */
  var KINDS = {
    bind: {
      label: 'As configured',
      why: 'The server was told to listen on this address.'
    },
    magicdns: {
      label: 'Tailscale name',
      why: 'Tailscale is running, so this name works from any device signed in to the tailnet.'
    },
    tailscale: {
      label: 'Tailscale address',
      why: 'Tailscale is running, so this address works from any device signed in to the tailnet.'
    },
    lan: {
      label: 'Local network',
      why: 'Another device on the same network can reach this address.'
    }
  };

  var LOOPBACK = ['127.0.0.1', 'localhost', '::1', '0:0:0:0:0:0:0:1'];
  var ANY = ['0.0.0.0', '', '*', '::'];

  function has(list, value) {
    return list.indexOf(value) !== -1;
  }

  function stripBrackets(host) {
    return String(host).replace(/^\[|\]$/g, '');
  }

  /* A host as it goes into a URL: brackets for IPv6, zone id dropped (it is meaningless on the
   * other end of a network), and nothing else touched. */
  function urlHost(host) {
    var bare = stripBrackets(host).split('%')[0];
    return bare.indexOf(':') === -1 ? bare : '[' + bare + ']';
  }

  function usableHost(host) {
    var bare = stripBrackets(host).trim();
    if (!bare || /[\s/]/.test(bare)) { return ''; }
    if (has(LOOPBACK, bare.toLowerCase()) || has(ANY, bare)) { return ''; }
    return bare;
  }

  function url(host, port) {
    var bare = stripBrackets(String(host == null ? '' : host).trim()).split('%')[0];
    var number = Number(port == null ? DEFAULT_PORT : port);
    if (!bare) { return ''; }
    if (!isFinite(number) || number <= 0 || number > 65535) { number = DEFAULT_PORT; }
    return 'http://' + urlHost(bare) + ':' + number;
  }

  function label(kind) {
    return Object.prototype.hasOwnProperty.call(KINDS, kind) ? KINDS[kind].label : '';
  }

  function silent(note) {
    return {
      silent: true,
      url: '',
      copy: '',
      host: '',
      port: DEFAULT_PORT,
      kind: '',
      label: '',
      why: '',
      note: note ? String(note) : '',
      others: 0
    };
  }

  /* `answer` is the dictionary from `addresses.py`; `port` comes from the address the shell
   * resolved, because the detector answers about hosts and the server owns its port. */
  function describe(answer, port) {
    if (!answer || typeof answer !== 'object') { return silent(''); }
    var primary = answer.primary;
    if (!primary || typeof primary !== 'object') {
      return silent(answer.note === undefined ? '' : answer.note);
    }
    var kind = typeof primary.kind === 'string' ? primary.kind : '';
    if (!Object.prototype.hasOwnProperty.call(KINDS, kind)) { return silent(''); }

    var host = usableHost(primary.host);
    if (!host) { return silent(''); }

    var number = Number(port == null ? DEFAULT_PORT : port);
    if (!isFinite(number) || number <= 0 || number > 65535) { number = DEFAULT_PORT; }
    var shown = url(host, number);

    var others = 0;
    var seen = [stripBrackets(host).toLowerCase()];
    var list = answer.others && answer.others.length ? answer.others : [];
    for (var index = 0; index < list.length; index++) {
      var other = list[index] || {};
      var alternate = usableHost(other.host);
      if (!alternate || !Object.prototype.hasOwnProperty.call(KINDS, other.kind)) { continue; }
      var key = alternate.toLowerCase();
      if (has(seen, key)) { continue; }
      seen.push(key);
      others++;
    }

    var why = typeof primary.why === 'string' ? primary.why.trim() : '';
    return {
      silent: false,
      url: shown,
      copy: shown,             /* the copy button copies exactly what is shown */
      host: host,
      port: number,
      kind: kind,
      label: KINDS[kind].label,
      why: why || KINDS[kind].why,
      note: typeof answer.note === 'string' ? answer.note : '',
      others: others
    };
  }

  var HostAddress = {
    describe: describe,
    url: url,
    label: label,
    usableHost: usableHost,
    KINDS: KINDS,
    DEFAULT_PORT: DEFAULT_PORT
  };

  if (typeof module === 'object' && module.exports) { module.exports = HostAddress; }
  root.HostAddress = HostAddress;
})(typeof globalThis !== 'undefined' ? globalThis : this);