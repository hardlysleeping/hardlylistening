/* HOST-003: what the window says about the server's lifecycle, and which button is live.
 *
 * `state.js` answers "is something answering, and is it mine". This file answers the questions
 * after that: has a start failed and why, what is an external server refusing, is a server this
 * window started going to survive a close. It is separate because the two questions have
 * different owners: `state.js` is HOST-001's ownership rule, this is HOST-003's transitions.
 *
 * Nothing here spawns, signals or reads a file. `desktop/host/lifecycle.py` decides; this
 * paints the decision and, on close, repeats it in one sentence so a person is not surprised by
 * a server that is still running.
 *
 * The one rule worth reading twice: **closing hands off by default**. A server this window
 * started keeps running unless `stop_on_close` was chosen, and an external server is never
 * stopped or claimed by either setting.
 */
(function (root) {
  'use strict';

  /* The same deadline as `START_DEADLINE` in lifecycle.py and `STATUS_AFTER_START_MS` in
   * state.js. Three copies looks like two too many; they are one fact the window shows and the
   * host enforces, and `tests/test_host_lifecycle.py` compares the Python and Rust ones. */
  var START_DEADLINE_MS = 20000;
  var STOP_GRACE_MS = 3000;

  function initial(address) {
    return {
      phase: 'idle',          /* idle | starting | up | failed | stopping */
      ownership: 'none',      /* none | ours | external */
      pid: null,
      reason: '',             /* a lifecycle reason, not prose */
      detail: '',
      healthError: '',
      address: address || null,
      notice: '',
      noticeKind: 'info',
      noticeAt: 0,
      startedAt: 0,
      handoff: null,
      stopOnClose: false
    };
  }

  function notice(state, text, kind) {
    state.notice = text || '';
    state.noticeKind = text ? (kind || 'info') : 'info';
    state.noticeAt = text ? Date.now() : 0;
    return state;
  }

  function merge(state, fields) {
    Object.keys(fields).forEach(function (key) {
      if (fields[key] !== undefined) { state[key] = fields[key]; }
    });
    return state;
  }

  /* A reason turned into a sentence. The detail from the host is used when it has one, because
   * it names the address and the exit status, and that is what a person can act on. */
  function reasonText(state) {
    var detail = state.detail ? ' ' + state.detail : '';
    var url = state.address && state.address.url ? state.address.url : 'the address';
    switch (state.reason) {
      case 'timeout':
        return 'The server did not answer ' + url + ' within ' +
          Math.round(START_DEADLINE_MS / 1000) + ' s. It was started by this window, so Stop is ' +
          'still available.';
      case 'exited':
        return 'The server exited.' + detail;
      case 'already_running':
        return 'A server is already running at ' + url + '. This window did not start it, so ' +
          'it will not stop it.';
      case 'port_in_use':
        return detail || 'That port is already in use by another program.';
      case 'no_launcher':
        return 'The server launcher was not found.' + detail +
          ' Bundle it as the sidecar, or set HARDLYLISTENING_SERVER.';
      case 'spawn_failed':
        return 'The server could not be started.' + detail;
      case 'bad_address':
        return 'The server address is unusable.' + detail;
      case 'not_started_here':
        /* The host's detail carries the refusal; when it is a short answer (a window that
         * stopped nothing at all), the rule itself is what a person needs to read. */
        return /left alone/.test(detail)
          ? detail.trim()
          : 'A server this window did not start was left alone.' + detail;
      case 'already_started':
        return 'This window already started a server.';
      case 'state_unavailable':
        return 'The window lost its own process slot. Restart the window.';
      default:
        return detail ? detail.trim() : '';
    }
  }

  function reduce(state, event) {
    var next = merge({}, state);
    var type = event && event.type;

    if (type === 'snapshot') {
      var snapshot = event.snapshot || {};
      var before = next.phase + '/' + next.reason;
      if (snapshot.phase) { next.phase = snapshot.phase; }
      if (snapshot.ownership) { next.ownership = snapshot.ownership; }
      if (snapshot.pid !== undefined) { next.pid = snapshot.pid == null ? null : snapshot.pid; }
      next.reason = snapshot.reason ? String(snapshot.reason) : '';
      next.detail = snapshot.detail ? String(snapshot.detail) : '';
      next.healthError = snapshot.health_error || snapshot.healthError || '';
      next.handoff = snapshot.handoff || null;
      next.stopOnClose = snapshot.stop_on_close === true;
      if (before !== next.phase + '/' + next.reason) {
        /* Explain a transition once, not on every poll: a warning that re-appears every two
         * seconds is noise, and noise is how a real problem gets ignored. */
        if (next.phase === 'up' && next.ownership === 'ours') {
          notice(next, 'The local server this window started is running.');
        } else if (next.phase === 'up') {
          notice(next, 'A server this window did not start is answering. It will be left alone.');
        } else if (next.phase === 'failed' || next.reason === 'not_started_here') {
          notice(next, reasonText(next), 'warn');
        }
      }
      return next;
    }

    if (type === 'start') {
      var result = event.result || {};
      if (result.phase) { next.phase = result.phase; }
      if (result.ownership) { next.ownership = result.ownership; }
      if (result.pid !== undefined) { next.pid = result.pid; }
      next.reason = result.reason ? String(result.reason) : '';
      next.detail = result.detail ? String(result.detail) : '';
      if (result.started && result.reason === 'already_started') {
        notice(next, 'This window is already running the server.');
        return next;
      }
      if (result.started) {
        next.phase = 'starting';
        next.ownership = 'ours';
        next.startedAt = Date.now();
        notice(next, 'Starting the local server…');
        return next;
      }
      notice(next, reasonText(next), 'warn');
      return next;
    }

    if (type === 'deadline') {
      /* The window's own guard, for a host that went quiet: the start is declared failed by
       * the same deadline, not by a spinner that never ends. */
      if (next.phase !== 'starting') { return next; }
      next.phase = 'failed';
      next.reason = 'timeout';
      next.detail = '';
      notice(next, reasonText(next), 'warn');
      return next;
    }

    if (type === 'stop') {
      var stopped = event.result || {};
      if (stopped.phase) { next.phase = stopped.phase; }
      if (stopped.ownership) { next.ownership = stopped.ownership; }
      if (stopped.stopped) {
        /* Whatever the host's answer carries, a stopped server is nobody's: ownership cannot
         * survive the process it described. */
        next.phase = 'idle';
        next.ownership = 'none';
        next.pid = null;
        next.handoff = null;
        next.reason = '';
        next.detail = stopped.detail ? String(stopped.detail) : '';
        next.healthError = '';
        notice(next, stopped.forced
          ? 'Local server stopped: it did not exit within ' + Math.round(STOP_GRACE_MS / 1000) +
            ' s, so it was killed.'
          : 'Local server stopped.');
        return next;
      }
      next.reason = stopped.reason ? String(stopped.reason) : '';
      next.detail = stopped.detail ? String(stopped.detail) : '';
      notice(next, reasonText(next), 'warn');
      return next;
    }

    if (type === 'close') {
      var decision = event.decision || {};
      if (decision.action === 'handoff') {
        next.handoff = decision.handoff || null;
        next.phase = 'idle';
        next.ownership = 'none';
        next.pid = null;
        notice(next, 'Left running: ' + (decision.detail || 'the server this window started') +
          '. The next window will find it and leave it alone.', 'info');
        return next;
      }
      if (decision.action === 'stop') {
        next.phase = 'idle';
        next.ownership = 'none';
        next.pid = null;
        next.handoff = null;
        notice(next, 'Stopped on close: ' + (decision.detail || 'the server this window started') +
          '.');
        return next;
      }
      notice(next, decision.detail || 'Nothing this window started is running.');
      return next;
    }

    if (type === 'stopOnClose') {
      next.stopOnClose = event.value === true;
      notice(next, next.stopOnClose
        ? 'Closing the window will stop the server it started.'
        : 'Closing the window will leave the server it started running.');
      return next;
    }

    if (type === 'notice') {
      notice(next, event.text || '', event.kind === 'warn' ? 'warn' : 'info');
      return next;
    }

    return next;
  }

  function statusOf(state) {
    if (state.phase === 'starting') { return { text: 'Starting…', kind: 'pending' }; }
    if (state.phase === 'stopping') { return { text: 'Stopping…', kind: 'pending' }; }
    if (state.phase === 'up') {
      return {
        text: state.ownership === 'ours'
          ? 'Running (started by this window)'
          : 'Running (started elsewhere)',
        kind: 'up'
      };
    }
    if (state.phase === 'failed') {
      return {
        text: state.ownership === 'ours' ? 'Start failed (still running)' : 'Start failed',
        kind: 'down'
      };
    }
    return { text: 'Stopped', kind: 'down' };
  }

  /* What closing the window does, decided from state alone. An external server is neither
   * stopped nor described as ours, whatever the preference says. */
  function closeDecision(state) {
    var ours = state.ownership === 'ours' && (state.phase === 'up' ||
      state.phase === 'starting' || state.phase === 'failed');
    if (!ours) {
      return {
        action: 'nothing',
        text: state.ownership === 'external'
          ? 'A server this window did not start keeps running.'
          : 'No server was started by this window.'
      };
    }
    if (state.stopOnClose === true) {
      return {
        action: 'stop',
        text: 'Closing will stop the server this window started.'
      };
    }
    return {
      action: 'handoff',
      text: 'Closing will leave the server this window started running' +
        (state.pid ? ' (pid ' + state.pid + ')' : '') + '.'
    };
  }

  function view(state) {
    var status = statusOf(state);
    var ours = state.ownership === 'ours';
    /* Start is offered when nothing is in the way: idle, or a failed start that left nothing
     * behind. A failed start that is still ours needs Stop first, and a port held by another
     * program will refuse the next start anyway — offering it is a lie. */
    var canStart = state.phase === 'idle' ||
      (state.phase === 'failed' && state.ownership === 'none');
    var canStop = ours && state.phase !== 'stopping';
    var decision = closeDecision(state);

    return {
      phase: state.phase,
      ownership: state.ownership,
      pid: state.pid,
      status: status,
      reason: state.reason ? reasonText(state) : '',
      notice: state.notice,
      noticeKind: state.noticeKind,
      healthError: state.healthError,
      stopOnClose: state.stopOnClose === true,
      close: decision,
      handoff: state.handoff && state.handoff.pid
        ? { active: true, pid: state.handoff.pid,
            text: 'Left running by an earlier window: pid ' + state.handoff.pid +
              (state.handoff.listen ? ' on ' + state.handoff.listen : '') +
              '. This window did not start it, so it will not stop it.' }
        : { active: false, text: '' },
      buttons: {
        start: {
          enabled: canStart,
          hint: canStart ? 'Start the bundled server'
            : (ours ? 'A server this window started is already running; stop it first'
              : 'A server is already running here, or the port is in use')
        },
        stop: {
          enabled: canStop,
          hint: canStop ? 'Stop the server this window started (SIGTERM, then kill after ' +
            Math.round(STOP_GRACE_MS / 1000) + ' s)'
            : 'Only a server this window started can be stopped from here'
        },
        close: {
          enabled: true,
          hint: decision.text
        }
      }
    };
  }

  /* Painting is deliberately confined to ids this card owns (`lifecycle-*` and
   * `stop-on-close`): `app.js` already paints the HOST-001 status pill from `state.js`, and two
   * owners for one element is how a status line starts flickering. */
  function paint(doc, painted) {
    if (!doc || !doc.getElementById) { return []; }
    var touched = [];
    function node(id) { return doc.getElementById(id); }
    function text(id, value) {
      var element = node(id);
      if (!element) { return; }
      element.textContent = value;
      touched.push(id);
    }

    text('lifecycle-status', painted.status.text);
    text('lifecycle-reason', painted.reason);
    text('lifecycle-handoff', painted.handoff.text);
    text('lifecycle-close', painted.close.text);

    var pill = node('lifecycle-status');
    if (pill && pill.dataset) {
      pill.dataset.state = painted.status.kind;
      if (!pill.classList && typeof pill.setAttribute === 'function') {
        pill.setAttribute('data-state', painted.status.kind);
      }
    }
    var reason = node('lifecycle-reason');
    if (reason) { reason.hidden = !painted.reason; }
    var handoff = node('lifecycle-handoff');
    if (handoff) { handoff.hidden = !painted.handoff.active; }

    var checkbox = node('stop-on-close');
    if (checkbox) {
      checkbox.checked = painted.stopOnClose === true;
      touched.push('stop-on-close');
    }
    return touched;
  }

  /* The thin bridge, for when `index.html` grows the two nodes this card adds. Outside the Tauri
   * window there is no bridge and nothing to do, so the page keeps working as a static file. */
  function attach(doc, invoke) {
    var checkbox = doc && doc.getElementById ? doc.getElementById('stop-on-close') : null;
    if (!checkbox || typeof invoke !== 'function') { return false; }
    checkbox.addEventListener('change', function () {
      invoke('lifecycle_step', { action: 'close_behaviour', value: checkbox.checked === true });
    });
    return true;
  }

  var Lifecycle = {
    initial: initial,
    reduce: reduce,
    view: view,
    reasonText: reasonText,
    closeDecision: closeDecision,
    paint: paint,
    attach: attach,
    START_DEADLINE_MS: START_DEADLINE_MS,
    STOP_GRACE_MS: STOP_GRACE_MS
  };

  if (typeof module === 'object' && module.exports) { module.exports = Lifecycle; }
  root.Lifecycle = Lifecycle;
})(typeof globalThis !== 'undefined' ? globalThis : this);