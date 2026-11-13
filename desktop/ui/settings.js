/* The settings panel: what the window can change, and what each change means.
 *
 * The rules are in `desktop/host/settings.py` — the tested copy, mirrored here because the
 * panel has to make the same decisions before it asks Rust to write anything. Rust
 * (`src-tauri/src/settings.rs`) only reads and writes; this file decides, and the DOM code at
 * the bottom only paints it.
 *
 * Two rules this file exists to keep:
 *
 *   * **No setting without a server-side meaning.** The web UI and OpenSubsonic have no switch
 *     in the server, so they are facts with a reason, never toggles. A stored flag the server
 *     ignores is a promise the next start breaks.
 *   * **Autostart is an explicit choice.** `dirty()` only sees it once `autostartTouched` is
 *     set by a click, and `save()` sends `null` for "no choice" — the entry is never installed
 *     as a side effect of changing the address.
 */
(function (root) {
  'use strict';

  var LOCAL = 'local';
  var LAN = 'lan';
  var LOCAL_HOST = '127.0.0.1';
  var ANY_HOST = '0.0.0.0';
  var DEFAULT_PORT = 8470;

  /* Short copy for the panel; the full reasoning is in settings.py and docs/desktop/settings.md. */
  var UNSUPPORTED = {
    web_ui: 'The server always serves web/: it reads no variable that turns the web UI off. ' +
      'Hiding it is a reverse-proxy rule, not a server setting.',
    subsonic: 'OpenSubsonic is always on: every /rest/ request is dispatched to the Subsonic ' +
      'adapter and /api/v1/capabilities reports it enabled as a constant. No switch exists.'
  };

  var SCOPES = {
    local: {
      host: LOCAL_HOST,
      label: 'This computer only',
      detail: 'The server binds 127.0.0.1, so only this machine can connect.'
    },
    lan: {
      host: ANY_HOST,
      label: 'Anyone on this network',
      detail: 'The server binds 0.0.0.0, so every interface answers. That is exposure, not ' +
        'authentication: set an origin and accounts before leaving it on.'
    }
  };

  function stripBrackets(host) { return String(host == null ? '' : host).replace(/^\[|\]$/g, ''); }

  function scopeOf(host) {
    var bare = stripBrackets(host).trim();
    if (bare === 'localhost' || bare === '::1' || bare === '0:0:0:0:0:0:0:1') { return LOCAL; }
    if (/^127\./.test(bare)) { return LOCAL; }
    return LAN;
  }

  function hostForScope(scope) {
    if (!SCOPES[scope]) { throw new Error('unknown exposure: ' + scope); }
    return SCOPES[scope].host;
  }

  /* `host:port` as the server prints it — the same shape as resolve.Address.listen(). */
  function listen(host, port) {
    var bare = stripBrackets(host);
    var shown = bare.indexOf(':') === -1 ? bare : '[' + bare + ']';
    return shown + ':' + port;
  }

  /* Nothing here decides the address: `host_env`/`settings_read` already resolved it, with the
   * environment winning over the file. This only shapes it for the panel. */
  function model(input) {
    var source = input || {};
    var address = source.address || {};
    var host = String(address.host == null ? LOCAL_HOST : address.host).trim();
    var port = Number(address.port == null ? DEFAULT_PORT : address.port);
    var problems = [];
    if (source.problem) { problems.push(String(source.problem)); }
    if (source.configProblem) { problems.push(String(source.configProblem)); }
    (source.problems || []).forEach(function (text) { if (text) { problems.push(String(text)); } });
    return {
      host: host,
      port: port,
      scope: scopeOf(host),
      source: address.source || 'default',
      listen: listen(host, port),
      locked: (source.locked || []).slice(),
      autostart: source.autostart === true,
      autostartSupported: source.autostartSupported !== false,
      configPath: source.configPath || '',
      autostartPath: source.autostartPath || '',
      problems: problems
    };
  }

  function rows(m) {
    return [
      {
        key: 'host',
        label: 'Who can reach it',
        value: SCOPES[m.scope].label,
        detail: SCOPES[m.scope].detail,
        editable: true,
        supported: true,
        locked: m.locked.indexOf('host') !== -1,
        options: [
          { value: LOCAL, label: SCOPES.local.label },
          { value: LAN, label: SCOPES.lan.label }
        ]
      },
      {
        key: 'port',
        label: 'Port',
        value: m.port,
        detail: 'The address this window polls and the launcher is told to bind.',
        editable: false,
        supported: true,
        locked: m.locked.indexOf('port') !== -1
      },
      {
        key: 'web_ui',
        label: 'Web UI',
        value: 'Always served',
        detail: UNSUPPORTED.web_ui,
        editable: false,
        supported: false,
        locked: true,
        options: []
      },
      {
        key: 'subsonic',
        label: 'OpenSubsonic',
        value: 'Always on',
        detail: UNSUPPORTED.subsonic,
        editable: false,
        supported: false,
        locked: true,
        options: []
      },
      {
        key: 'autostart',
        label: 'Start at sign-in',
        value: m.autostart ? 'On' : 'Off',
        detail: m.autostartSupported
          ? 'Runs the server launcher at sign-in, not this window. Logs are not captured: an ' +
            'autostarted server prints to nothing.'
          : 'There is no autostart entry on this platform in this shell; the file format it ' +
            'writes is XDG (.desktop), which is Linux.',
        editable: m.autostartSupported,
        supported: m.autostartSupported,
        locked: false,
        options: [
          { value: false, label: 'Off' },
          { value: true, label: 'On' }
        ]
      }
    ];
  }

  /* The edited copy. `autostartTouched` is the whole explicit-choice rule: without a click, the
   * draft's autostart value is not a decision and must not become one. */
  function draft(m) {
    return { scope: m.scope, port: m.port, autostart: m.autostart, autostartTouched: false };
  }

  function setScope(d, scope) {
    if (!SCOPES[scope]) { throw new Error('unknown exposure: ' + scope); }
    return Object.assign({}, d, { scope: scope });
  }

  function setAutostart(d, on) {
    return Object.assign({}, d, { autostart: on === true, autostartTouched: true });
  }

  function edited(d) {
    return Object.assign({}, d, { autostartTouched: false });
  }

  function dirty(m, d) {
    var scopeChanged = d.scope !== m.scope && m.locked.indexOf('host') === -1;
    var portChanged = Number(d.port) !== m.port && m.locked.indexOf('port') === -1;
    var autostartChanged = d.autostartTouched === true && d.autostart !== m.autostart;
    return scopeChanged || portChanged || autostartChanged;
  }

  /* The same diff settings.py's `changed()` returns, for the panel's "a restart is needed" line.
   * Every address change is a restart: the server reads `--host`/`--port` once, in `main()`. */
  function diff(m, d) {
    var changes = [];
    var host = hostForScope(d.scope);
    var port = Number(d.port);

    if (host !== m.host) {
      changes.push({ field: 'host', label: 'Who can reach it', from: m.host, to: host, restart: true });
    }
    if (port !== m.port) {
      changes.push({ field: 'port', label: 'Port', from: m.port, to: port, restart: true });
    }
    var restart = changes.filter(function (c) { return c.restart; }).map(function (c) { return c.field; });
    if (d.autostartTouched === true && d.autostart !== m.autostart) {
      changes.push({
        field: 'autostart',
        label: 'Start at sign-in',
        from: m.autostart ? 'On' : 'Off',
        to: d.autostart ? 'On' : 'Off',
        restart: false
      });
    }

    var before = listen(m.host, m.port);
    var after = listen(host, port);
    var summary;
    if (!changes.length) {
      summary = 'Nothing to apply.';
    } else if (restart.length) {
      summary = 'Saved. Restart the server to listen on ' + after + ' — it keeps answering on ' +
        before + ' until then.';
    } else {
      summary = 'Saved.';
    }
    return {
      changes: changes,
      restart: restart,
      restartRequired: restart.length > 0,
      before: before,
      after: after,
      summary: summary
    };
  }

  function decide(m, d) {
    var canSave = dirty(m, d);
    var hostLocked = m.locked.indexOf('host') !== -1;
    var hint;
    if (hostLocked) {
      hint = m.locked.indexOf('port') !== -1
        ? 'The exposure and port are fixed by the environment for this session.'
        : 'The exposure is fixed by HARDLYLISTENING_HOST for this session.';
    } else if (!canSave) {
      hint = 'Nothing has changed.';
    } else {
      var pending = diff(m, d);
      hint = pending.restartRequired
        ? 'Save, then restart the server: it keeps listening on ' + pending.before + ' until then.'
        : 'Save to write the setting.';
    }
    return {
      dirty: canSave,
      save: { enabled: canSave, hint: hint },
      scope: {
        local: { active: d.scope === LOCAL, enabled: !hostLocked },
        lan: { active: d.scope === LAN, enabled: !hostLocked }
      },
      autostart: {
        off: { active: !d.autostart, enabled: m.autostartSupported },
        on: { active: d.autostart, enabled: m.autostartSupported }
      },
      diff: diff(m, d)
    };
  }

  /* What the window says after a write. The refusal is named, because a Save that silently
   * skips a field is indistinguishable from a broken button. */
  function outcome(result) {
    var answer = result || {};
    var refused = (answer.refused || []).map(function (item) {
      return item && item.field ? item.field : String(item);
    });
    var parts = [];
    if (refused.length) {
      parts.push('Not applied: ' + refused.join(', ') + '.');
    }
    if (answer.autostart === 'written') {
      parts.push('Start at sign-in is now on — the server, not this window.');
    } else if (answer.autostart === 'removed') {
      parts.push('Start at sign-in is now off.');
    } else if (answer.autostart === 'unchanged' && !refused.length && !answer.saved) {
      parts.push('Nothing has been changed.');
    }
    if (answer.restart_required) {
      var settings = answer.settings || {};
      var before = answer.before || {};
      parts.push('Restart the server to listen on ' +
        listen(settings.host || LOCAL_HOST, settings.port || DEFAULT_PORT) +
        (before.host ? ' — it keeps answering on ' + listen(before.host, before.port) + ' until then.' : '.'));
    } else if (!parts.length && answer.saved) {
      parts.push('Saved.');
    }
    return {
      text: parts.join(' '),
      kind: refused.length ? 'warn' : 'info'
    };
  }

  /* ---- painting ------------------------------------------------------------ */

  var doc = typeof document === 'undefined' ? null : document;

  function el(name, className, text) {
    var node = doc.createElement(name);
    if (className) { node.className = className; }
    if (text != null) { node.textContent = String(text); }
    return node;
  }

  function fact(list, label, value) {
    var row = doc.createElement('div');
    row.appendChild(el('dt', null, label));
    var dd = el('dd', null, value);
    row.appendChild(dd);
    list.appendChild(row);
    return dd;
  }

  function choice(actions, label, active, enabled, onClick, hint) {
    var button = el('button', active ? 'primary' : null, label);
    button.type = 'button';
    button.setAttribute('aria-pressed', active ? 'true' : 'false');
    button.disabled = !enabled;
    if (hint) { button.title = hint; }
    button.addEventListener('click', onClick);
    actions.appendChild(button);
    return button;
  }

  function panel(shell, invoke) {
    var card = el('section', 'card settings');
    card.setAttribute('aria-labelledby', 'settings-title');
    var title = el('p', 'eyebrow', 'Settings');
    title.id = 'settings-title';
    card.appendChild(title);

    var facts = el('dl', 'facts');
    card.appendChild(facts);

    var choices = el('div', 'actions');
    card.appendChild(choices);
    var exposureHint = el('p', 'hint');
    card.appendChild(exposureHint);

    var startRow = el('div', 'actions');
    card.appendChild(startRow);
    var autostartHint = el('p', 'hint');
    card.appendChild(autostartHint);

    var foot = el('p', 'foot');
    card.appendChild(foot);
    var notice = el('p', 'notice');
    notice.setAttribute('role', 'status');
    notice.setAttribute('aria-live', 'polite');
    notice.hidden = true;
    card.appendChild(notice);

    var state = {
      model: model({}),
      draft: draft(model({})),
      canEdit: !!invoke
    };

    function show(text, kind) {
      notice.hidden = !text;
      notice.textContent = text || '';
      notice.dataset.kind = kind || 'info';
    }

    function render() {
      var m = state.model;
      var d = state.draft;
      var view = decide(m, d);
      var list = rows(m);

      facts.textContent = '';
      var hostValue = fact(facts, 'Who can reach it', list[0].value);
      hostValue.title = list[0].detail;
      fact(facts, 'Port', m.port);
      var webUi = fact(facts, 'Web UI', list[2].value);
      webUi.title = list[2].detail;
      var subsonic = fact(facts, 'OpenSubsonic', list[3].value);
      subsonic.title = list[3].detail;
      fact(facts, 'Start at sign-in', m.autostart ? 'On' : 'Off');

      choices.textContent = '';
      var hostLocked = m.locked.indexOf('host') !== -1;
      choice(choices, SCOPES.local.label, view.scope.local.active,
        state.canEdit && view.scope.local.enabled,
        function () { state.draft = setScope(state.draft, LOCAL); render(); },
        hostLocked ? 'Fixed by HARDLYLISTENING_HOST for this session' : SCOPES.local.detail);
      choice(choices, SCOPES.lan.label, view.scope.lan.active,
        state.canEdit && view.scope.lan.enabled,
        function () { state.draft = setScope(state.draft, LAN); render(); },
        hostLocked ? 'Fixed by HARDLYLISTENING_HOST for this session' : SCOPES.lan.detail);

      exposureHint.textContent = hostLocked
        ? 'The exposure is set by the environment for this session; change ' +
          'HARDLYLISTENING_HOST to move it.'
        : SCOPES[d.scope].detail;

      startRow.textContent = '';
      choice(startRow, 'Off', view.autostart.off.active,
        state.canEdit && view.autostart.off.enabled,
        function () { state.draft = setAutostart(state.draft, false); render(); });
      choice(startRow, 'On', view.autostart.on.active,
        state.canEdit && view.autostart.on.enabled,
        function () { state.draft = setAutostart(state.draft, true); render(); });
      var spacer = el('span', 'spacer');
      startRow.appendChild(spacer);
      var save = choice(startRow, 'Save', true, view.save.enabled, function () { commit(); },
        view.save.hint);
      save.disabled = !state.canEdit || !view.save.enabled;

      autostartHint.textContent = m.autostartSupported
        ? view.save.hint
        : 'Start at sign-in is only implemented for XDG (Linux) in this shell.';

      foot.textContent = 'The web UI and OpenSubsonic have no server-side switch: ' +
        'web/ is always served and /rest/ is always answered. ' +
        (m.configPath ? 'Settings file: ' + m.configPath + '. ' : '') +
        (m.autostartPath ? 'Autostart entry: ' + m.autostartPath + '.' : '');

      if (m.problems.length) { show(m.problems.join(' '), 'warn'); }
    }

    function commit() {
      if (!invoke) { return; }
      var pending = diff(state.model, state.draft);
      var patch = {};
      var host = hostForScope(state.draft.scope);
      if (host !== state.model.host) { patch.host = host; }
      if (Number(state.draft.port) !== state.model.port) { patch.port = Number(state.draft.port); }
      var autostart = state.draft.autostartTouched ? state.draft.autostart : null;
      if (!pending.changes.length) { show('Nothing has been changed.'); return; }
      invoke('settings_write', { patch: patch, autostart: autostart }).then(function (result) {
        var said = outcome(result);
        show(said.text, said.kind);
        return refresh(false).then(function () { render(); });
      }).catch(function (error) {
        show('The settings were not written: ' + error, 'warn');
      });
    }

    function apply(source) {
      state.model = model(source);
      state.draft = draft(state.model);
    }

    function refresh(clearNotice) {
      if (!invoke) { render(); return Promise.resolve(); }
      return invoke('settings_read').then(function (read) {
        read = read || {};
        apply({
          address: read.address && !read.address.error ? read.address : state.model,
          locked: read.locked,
          autostart: read.autostart,
          autostartSupported: read.autostart_supported,
          configPath: read.path,
          autostartPath: read.autostart_path,
          problem: read.problem
        });
        if (clearNotice) { show(''); }
        render();
      }).catch(function (error) {
        show('The settings could not be read: ' + error, 'warn');
      });
    }

    apply({});
    if (invoke) { notice.hidden = false; notice.textContent = 'Reading settings…'; }
    else {
      notice.hidden = false;
      notice.dataset.kind = 'warn';
      notice.textContent = 'This page is open outside the desktop shell, so the settings are ' +
        'read-only here.';
    }
    render();
    refresh(true);
    return card;
  }

  function mount(shell, invoke) {
    if (!doc || !shell) { return null; }
    var bridge = invoke;
    if (!bridge) {
      var tauri = root.__TAURI__;
      bridge = tauri && tauri.core && typeof tauri.core.invoke === 'function'
        ? function (command, args) { return tauri.core.invoke(command, args); }
        : null;
    }
    var card = panel(shell, bridge);
    var server = shell.querySelector('.card.server');
    if (server && server.parentNode === shell) {
      shell.insertBefore(card, server.nextSibling);
    } else {
      shell.appendChild(card);
    }
    return card;
  }

  function auto() {
    var shell = doc && doc.querySelector('.shell');
    if (shell) { mount(shell); }
  }

  var Settings = {
    LOCAL: LOCAL,
    LAN: LAN,
    DEFAULT_PORT: DEFAULT_PORT,
    SCOPES: SCOPES,
    UNSUPPORTED: UNSUPPORTED,
    scopeOf: scopeOf,
    hostForScope: hostForScope,
    listen: listen,
    model: model,
    rows: rows,
    draft: draft,
    setScope: setScope,
    setAutostart: setAutostart,
    edited: edited,
    dirty: dirty,
    diff: diff,
    decide: decide,
    outcome: outcome,
    mount: mount
  };

  if (typeof module === 'object' && module.exports) {
    module.exports = Settings;
  } else if (doc) {
    if (doc.readyState === 'loading') {
      doc.addEventListener('DOMContentLoaded', auto);
    } else {
      auto();
    }
  }
  root.Settings = Settings;
})(typeof globalThis !== 'undefined' ? globalThis : this);