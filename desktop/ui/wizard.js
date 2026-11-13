/* The first-run wizard's decisions, then its four steps.
 *
 * HOST-002. Same split as `state.js`: everything that is a *rule* is in this file, with no DOM
 * in sight, so `tests/test_host_wizard.cjs` can drive it with plain objects. The DOM half is at
 * the bottom and only paints what `view()` decided and sends what a button means.
 *
 * Four answers, and one of them is never stored: the administrator's password goes straight to
 * `setup_admin`, which hands it to the server's own account code on a pipe. It is not in `payload()`,
 * not in the state file and not in this object after the step is done.
 *
 * The numbers below are `desktop/host/setup.py`'s, and the tests compare the two copies: Python is
 * where the server-facing rules are proven, this is where the window repeats them so a typo is
 * answered before a round trip.
 */
(function (root) {
  'use strict';

  var RULES = {
    version: 1,
    steps: ['music', 'data', 'name', 'admin'],
    fields: ['music_dir', 'data_dir', 'server_name', 'admin_email'],
    password_min: 10,
    password_max: 1024,
    email_max: 254,
    name_max: 64,
    default_server_name: 'HardlyListening',
    music_suffix: 'Music/HardlyListening',
    xdg_data_suffix: '.local/share',
    data_leaf: 'hardlylistening'
  };

  var STEP_META = {
    music: {
      title: 'Where is your music?',
      hint: 'The folder the server plays from. It is only read: nothing in it is renamed or removed.',
      label: 'Music folder',
      kind: 'path'
    },
    data: {
      title: 'Where should the server keep its own files?',
      hint: 'The database, artwork and caches. Created if it is not there yet; your music folder is untouched.',
      label: 'Data folder',
      kind: 'path'
    },
    name: {
      title: 'What is this server called?',
      hint: 'A label for this window, so two installs on two machines are told apart. It is not a hostname.',
      label: 'Server name',
      kind: 'text'
    },
    admin: {
      title: 'Your administrator account',
      hint: 'The first account, created by the server itself. It can add members and change every setting.',
      label: 'Admin email',
      kind: 'account'
    }
  };

  function text(value) {
    return String(value == null ? '' : value).trim();
  }

  function fail(field, detail) {
    return { ok: false, field: field, value: '', detail: detail };
  }

  function pass(field, value) {
    return { ok: true, field: field, value: value, detail: '' };
  }

  /* `~/Music/HardlyListening` and `~/.local/share/hardlylistening`, joined here because the window
   * knows the home directory and nothing else about the machine. */
  function defaults(home) {
    var base = text(home).replace(/[\\/]+$/, '');
    return {
      music_dir: base + '/' + RULES.music_suffix,
      data_dir: base + '/' + RULES.xdg_data_suffix + '/' + RULES.data_leaf
    };
  }

  function checkEmail(value) {
    var email = text(value).toLowerCase();
    var parts = email.split('@');
    if (email.length < 5 || email.length > RULES.email_max || parts.length !== 2 || /\s/.test(email)) {
      return fail('admin_email', 'Enter an email address');
    }
    var domain = parts[1];
    if (!parts[0] || domain.indexOf('.') === -1 ||
        domain.charAt(0) === '.' || domain.charAt(domain.length - 1) === '.') {
      return fail('admin_email', 'Enter an email address');
    }
    return pass('admin_email', email);
  }

  /* One field, one sentence. The filesystem half of a path ("does it exist") belongs to the
   * shell's Rust, which asks the operating system; this only refuses the empty answer. */
  function checkField(field, value) {
    if (field === 'music_dir') {
      var music = text(value);
      if (!music) { return fail('music_dir', 'Choose a folder for your music'); }
      return pass('music_dir', music);
    }
    if (field === 'data_dir') {
      var data = text(value);
      if (!data) { return fail('data_dir', 'Choose a folder for the server data'); }
      return pass('data_dir', data);
    }
    if (field === 'server_name') {
      var name = text(value);
      if (name.length < 1 || name.length > RULES.name_max) {
        return fail('server_name', 'A server name is 1-' + RULES.name_max + ' characters');
      }
      if (/[\u0000-\u001f\u007f]/.test(name)) {
        return fail('server_name', 'A server name cannot contain control characters');
      }
      return pass('server_name', name);
    }
    if (field === 'admin_email') { return checkEmail(value); }
    if (field === 'password') {
      if (typeof value !== 'string' || value.length < RULES.password_min) {
        return fail('password', 'Password must be at least ' + RULES.password_min + ' characters');
      }
      if (value.length > RULES.password_max) {
        return fail('password', 'Password is too long');
      }
      return pass('password', value);
    }
    return fail(field, 'unknown field ' + field);
  }

  function checkStep(state) {
    var step = state.step;
    if (step === 'admin') {
      var email = checkEmail(state.values.admin_email);
      if (!email.ok) { return email; }
      /* A resumed run that already created the account has no password to re-enter: the step is
       * a confirmation, and its only job is to save the finished state. */
      if (state.account) { return pass('admin_email', email.value); }
      return checkField('password', state.password);
    }
    if (step === 'name') { return checkField('server_name', state.values.server_name); }
    if (step === 'music') { return checkField('music_dir', state.values.music_dir); }
    return checkField('data_dir', state.values.data_dir);
  }

  function cleanCompleted(list) {
    var done = [];
    (Array.isArray(list) ? list : []).forEach(function (step) {
      if (RULES.steps.indexOf(step) !== -1 && done.indexOf(step) === -1) { done.push(step); }
    });
    return RULES.steps.filter(function (step) { return done.indexOf(step) !== -1; });
  }

  function isComplete(state) {
    if (!state || state.version !== RULES.version) { return false; }
    var done = cleanCompleted(state.completed);
    if (done.length !== RULES.steps.length) { return false; }
    return RULES.fields.every(function (field) { return text(state[field]) !== ''; });
  }

  function firstUnfinished(state) {
    var done = cleanCompleted(state.completed);
    for (var i = 0; i < RULES.steps.length; i += 1) {
      var step = RULES.steps[i];
      var field = RULES.fields[i];
      if (done.indexOf(step) === -1 || text(state[field]) === '') { return step; }
    }
    return RULES.steps[0];
  }

  function initial(saved, options) {
    options = options || {};
    saved = saved && typeof saved === 'object' ? saved : {};
    var fallback = options.defaults && typeof options.defaults === 'object'
      ? options.defaults : defaults(options.home || '');
    var values = {
      music_dir: text(saved.music_dir) || text(fallback.music_dir),
      data_dir: text(saved.data_dir) || text(fallback.data_dir),
      server_name: text(saved.server_name) || RULES.default_server_name,
      admin_email: text(saved.admin_email)
    };
    var state = {
      version: RULES.version,
      show: isComplete(saved) ? 'shell' : 'wizard',
      step: firstUnfinished(saved),
      values: values,
      completed: cleanCompleted(saved.completed),
      account: saved.admin_created === true,
      password: '',
      error: '',
      field: '',
      notice: text(options.problem),
      problem: text(options.problem),
      saving: false
    };
    return state;
  }

  function clone(state) {
    return {
      version: state.version,
      show: state.show,
      step: state.step,
      values: {
        music_dir: state.values.music_dir,
        data_dir: state.values.data_dir,
        server_name: state.values.server_name,
        admin_email: state.values.admin_email
      },
      completed: state.completed.slice(),
      account: state.account,
      password: state.password,
      error: state.error,
      field: state.field,
      notice: state.notice,
      problem: state.problem,
      saving: state.saving
    };
  }

  function indexOfStep(step) {
    return RULES.steps.indexOf(step);
  }

  function reduce(state, event) {
    var next = clone(state);
    var type = event && event.type;

    if (type === 'edit') {
      var field = String(event.field || '');
      if (field === 'password') {
        next.password = String(event.value == null ? '' : event.value);
      } else if (Object.prototype.hasOwnProperty.call(next.values, field)) {
        next.values[field] = String(event.value == null ? '' : event.value);
      } else {
        return next;
      }
      if (next.field === field) { next.error = ''; next.field = ''; }
      return next;
    }

    if (type === 'picked') {
      var picked = String(event.path || '');
      if (picked) {
        var target = next.step === 'music' ? 'music_dir' : 'data_dir';
        next.values[target] = picked;
        next.error = '';
        next.field = '';
      } else if (event.detail) {
        next.error = String(event.detail);
        next.field = '';
      }
      return next;
    }

    if (type === 'next') {
      if (next.saving) { return next; }
      var check = checkStep(next);
      if (!check.ok) {
        next.error = check.detail;
        next.field = check.field;
        return next;
      }
      next.saving = true;
      next.error = '';
      next.field = '';
      next.notice = '';
      return next;
    }

    if (type === 'created') {
      next.account = true;
      next.values.admin_email = text(event.email) || next.values.admin_email;
      next.password = '';
      return next;
    }

    if (type === 'rejected') {
      next.saving = false;
      next.field = String(event.field || '');
      next.error = text(event.detail) || 'That could not be saved.';
      return next;
    }

    if (type === 'saved') {
      var saved = event.state && typeof event.state === 'object' ? event.state : {};
      next.saving = false;
      next.error = '';
      next.field = '';
      next.completed = cleanCompleted(saved.completed);
      RULES.fields.forEach(function (key) {
        if (text(saved[key])) { next.values[key] = text(saved[key]); }
      });
      if (saved.admin_created === true) { next.account = true; }
      next.password = '';
      next.problem = '';
      next.notice = text(event.detail);
      if (next.completed.length === RULES.steps.length) {
        next.show = 'shell';
      } else {
        next.step = firstUnfinished({
          completed: next.completed,
          music_dir: next.values.music_dir,
          data_dir: next.values.data_dir,
          server_name: next.values.server_name,
          admin_email: next.values.admin_email
        });
      }
      return next;
    }

    if (type === 'back') {
      var index = indexOfStep(next.step);
      next.error = '';
      next.field = '';
      next.password = '';
      next.notice = '';
      if (index > 0) { next.step = RULES.steps[index - 1]; }
      return next;
    }

    return next;
  }

  /* What is saved on disk. Deliberately an allow-list: a password that is not named here cannot
   * reach `setup_save`, whatever the rest of this file does. The step being saved counts as done
   * in the payload, so the file written on "Continue" already records that step; a refusal comes
   * back as `rejected` and the in-memory `completed` list was never touched. */
  function payload(state) {
    var done = cleanCompleted(state.completed);
    if (state.saving && done.indexOf(state.step) === -1) { done.push(state.step); }
    return {
      version: RULES.version,
      music_dir: text(state.values.music_dir),
      data_dir: text(state.values.data_dir),
      server_name: text(state.values.server_name),
      admin_email: text(state.values.admin_email),
      admin_created: state.account === true,
      completed: cleanCompleted(done)
    };
  }

  function view(state) {
    var meta = STEP_META[state.step];
    var index = indexOfStep(state.step);
    var isLast = index === RULES.steps.length - 1;
    var fields = [];

    if (state.step === 'admin') {
      fields.push({
        name: 'admin_email',
        label: 'Email',
        kind: 'email',
        value: state.values.admin_email,
        autocomplete: 'username'
      });
      if (!state.account) {
        fields.push({
          name: 'password',
          label: 'Password',
          kind: 'password',
          value: '',
          autocomplete: 'new-password',
          hint: 'At least ' + RULES.password_min + ' characters.'
        });
      }
    } else if (meta.kind === 'path') {
      fields.push({
        name: state.step === 'music' ? 'music_dir' : 'data_dir',
        label: meta.label,
        kind: 'path',
        value: state.values[state.step === 'music' ? 'music_dir' : 'data_dir'],
        autocomplete: 'off'
      });
    } else {
      fields.push({
        name: 'server_name',
        label: meta.label,
        kind: 'text',
        value: state.values.server_name,
        autocomplete: 'off'
      });
    }

    var label = isLast ? (state.account ? 'Finish' : 'Create the account') : 'Continue';
    var summary = [];
    if (state.step === 'admin') {
      summary = [
        { label: 'Music', value: state.values.music_dir || '—' },
        { label: 'Data', value: state.values.data_dir || '—' },
        { label: 'Name', value: state.values.server_name || '—' }
      ];
    }

    return {
      show: state.show,
      step: state.step,
      index: index + 1,
      total: RULES.steps.length,
      title: meta.title,
      hint: meta.hint,
      fields: fields,
      summary: summary,
      error: state.error,
      field: state.field,
      notice: state.notice,
      noticeKind: state.problem && state.notice === state.problem ? 'warn' : 'info',
      saving: state.saving,
      account: state.account,
      steps: RULES.steps.map(function (step, position) {
        return {
          step: step,
          title: STEP_META[step].title,
          state: state.completed.indexOf(step) !== -1 ? 'done'
            : (position === index ? 'current' : 'todo')
        };
      }),
      buttons: {
        back: {
          enabled: index > 0 && !state.saving,
          hint: index > 0 ? 'Back to the previous step' : 'This is the first step'
        },
        pick: {
          enabled: meta.kind === 'path' && !state.saving,
          hint: meta.kind === 'path'
            ? 'Choose this folder in a file dialog'
            : 'Type the answer; there is no folder to pick'
        },
        next: {
          enabled: !state.saving,
          label: label,
          hint: state.saving ? 'Saving…' : meta.hint
        }
      }
    };
  }

  /* ---- the DOM half: paint `view()`, send what a click means ---------------- */
  function mount(state, bridge, doc, options) {
    /* The wizard takes the shell's place rather than floating over it: the window's layout
     * (`main.shell`, its column, its width) is the visual language, and nothing here adds a
     * stylesheet — the CSP is `style-src 'self'`, so inline styles are not an option. */
    var shell = doc.querySelector('main.shell');
    var hidden = [];
    if (shell) {
      Array.prototype.slice.call(shell.children).forEach(function (child) {
        if (!child.hidden) { hidden.push(child); child.hidden = true; }
      });
    }

    var container = doc.createElement('section');
    container.className = 'card wizard';
    container.setAttribute('aria-label', 'First-run setup');

    var eyebrow = doc.createElement('p');
    eyebrow.className = 'eyebrow';
    var title = doc.createElement('h2');
    var hint = doc.createElement('p');
    hint.className = 'hint';
    var notice = doc.createElement('p');
    notice.className = 'notice';
    notice.setAttribute('role', 'status');
    notice.setAttribute('aria-live', 'polite');
    var body = doc.createElement('div');
    body.className = 'wizard-fields';
    var summary = doc.createElement('dl');
    summary.className = 'facts';
    var error = doc.createElement('p');
    error.className = 'reason';
    error.setAttribute('role', 'alert');
    var actions = doc.createElement('div');
    actions.className = 'actions';
    var back = doc.createElement('button');
    back.type = 'button';
    back.className = 'quiet';
    back.textContent = 'Back';
    var pick = doc.createElement('button');
    pick.type = 'button';
    pick.textContent = 'Choose folder…';
    var spacer = doc.createElement('span');
    spacer.className = 'spacer';
    var next = doc.createElement('button');
    next.type = 'button';
    next.className = 'primary';
    var foot = doc.createElement('p');
    foot.className = 'foot';
    foot.textContent = 'Every answer is written after its step, so closing this window loses at most '
      + 'the step you are on. Nothing is written to your music folder.';

    actions.appendChild(back);
    actions.appendChild(pick);
    actions.appendChild(spacer);
    actions.appendChild(next);
    container.appendChild(eyebrow);
    container.appendChild(title);
    container.appendChild(hint);
    container.appendChild(notice);
    container.appendChild(body);
    container.appendChild(summary);
    container.appendChild(error);
    container.appendChild(actions);
    container.appendChild(foot);
    (shell || doc.body).appendChild(container);

    var inputs = {};
    var renderedStep = null;

    function field(name) { return inputs[name] || null; }

    function buildFields(stepView) {
      body.textContent = '';
      inputs = {};
      stepView.fields.forEach(function (spec) {
        var label = doc.createElement('label');
        label.className = 'wizard-label';
        label.textContent = spec.label;
        var input = doc.createElement('input');
        input.type = spec.kind === 'password' ? 'password' : (spec.kind === 'email' ? 'email' : 'text');
        input.className = 'wizard-input';
        input.value = spec.value || '';
        input.autocomplete = spec.autocomplete || 'off';
        input.spellcheck = false;
        input.id = 'wizard-' + spec.name;
        label.setAttribute('for', input.id);
        input.addEventListener('input', function () {
          dispatch({ type: 'edit', field: spec.name, value: input.value });
        });
        input.addEventListener('keydown', function (event) {
          if (event.key === 'Enter') { event.preventDefault(); submit(); }
        });
        body.appendChild(label);
        body.appendChild(input);
        if (spec.hint) {
          var note = doc.createElement('p');
          note.className = 'hint';
          note.textContent = spec.hint;
          body.appendChild(note);
        }
        inputs[spec.name] = input;
      });
      var first = stepView.fields.length ? inputs[stepView.fields[0].name] : null;
      if (first && doc.activeElement === doc.body) { first.focus(); }
    }

    function render() {
      var current = view(state);
      eyebrow.textContent = 'First run · step ' + current.index + ' of ' + current.total;
      title.textContent = current.title;
      hint.textContent = current.hint;
      notice.hidden = !current.notice;
      notice.textContent = current.notice || '';
      notice.dataset.kind = current.noticeKind;
      error.hidden = !current.error;
      error.textContent = current.error || '';

      if (renderedStep !== current.step) {
        buildFields(current);
        renderedStep = current.step;
      } else {
        current.fields.forEach(function (spec) {
          var input = field(spec.name);
          if (input && doc.activeElement !== input) { input.value = spec.value || ''; }
        });
      }

      summary.textContent = '';
      summary.hidden = current.summary.length === 0;
      current.summary.forEach(function (row) {
        var wrap = doc.createElement('div');
        var term = doc.createElement('dt');
        term.textContent = row.label;
        var value = doc.createElement('dd');
        value.textContent = row.value;
        wrap.appendChild(term);
        wrap.appendChild(value);
        summary.appendChild(wrap);
      });

      back.disabled = !current.buttons.back.enabled;
      back.title = current.buttons.back.hint;
      pick.hidden = current.step !== 'music' && current.step !== 'data';
      pick.disabled = !current.buttons.pick.enabled;
      pick.title = current.buttons.pick.hint;
      next.disabled = !current.buttons.next.enabled;
      next.title = current.buttons.next.hint;
      next.textContent = current.saving ? 'Saving…' : current.buttons.next.label;
    }

    function dispatch(event) {
      state = reduce(state, event);
      render();
    }

    function done() {
      hidden.forEach(function (child) { child.hidden = false; });
      container.remove();
      if (options && typeof options.onFinish === 'function') { options.onFinish(); }
    }

    function save() {
      var snapshot = payload(state);
      Promise.resolve(bridge.invoke('setup_save', { state: snapshot })).then(function (result) {
        result = result || {};
        if (result.saved !== true) {
          dispatch({
            type: 'rejected',
            field: result.field || '',
            detail: result.detail || 'That could not be saved.'
          });
          return;
        }
        dispatch({ type: 'saved', state: snapshot, detail: result.detail || '' });
        if (state.show === 'shell') { done(); }
      }).catch(function (failure) {
        dispatch({ type: 'rejected', detail: String(failure) });
      });
    }

    function submit() {
      dispatch({ type: 'next' });
      if (!state.saving) { return; }
      if (state.step !== 'admin' || state.account) { save(); return; }
      Promise.resolve(bridge.invoke('setup_admin', {
        email: state.values.admin_email,
        password: state.password,
        data_dir: state.values.data_dir
      })).then(function (result) {
        result = result || {};
        if (result.created !== true) {
          dispatch({
            type: 'rejected',
            field: 'password',
            detail: result.detail || 'The account could not be created.'
          });
          return;
        }
        dispatch({ type: 'created', email: result.email });
        save();
      }).catch(function (failure) {
        dispatch({ type: 'rejected', detail: String(failure) });
      });
    }

    back.addEventListener('click', function () { dispatch({ type: 'back' }); });
    next.addEventListener('click', submit);
    pick.addEventListener('click', function () {
      bridge.invoke('setup_pick_folder', { kind: state.step }).then(function (result) {
        result = result || {};
        dispatch({ type: 'picked', path: result.path || '', detail: result.detail || '' });
      }).catch(function (failure) {
        dispatch({ type: 'picked', path: '', detail: String(failure) });
      });
    });

    render();
  }

  var Wizard = {
    RULES: RULES,
    initial: initial,
    reduce: reduce,
    view: view,
    payload: payload,
    defaults: defaults,
    checkField: checkField,
    checkStep: checkStep,
    isComplete: isComplete,
    firstUnfinished: firstUnfinished,
    /* Resolves to `{show: 'wizard'|'shell'}`. Without the bridge there is nothing to read, save or
     * create, so the shell's own page is left alone (it already says it is outside the shell). */
    start: function (options) {
      options = options || {};
      var bridge = options.bridge || null;
      var doc = options.doc || (typeof document !== 'undefined' ? document : null);
      if (!bridge || !doc) { return Promise.resolve({ show: 'shell', reason: 'no bridge' }); }
      return Promise.resolve(bridge.invoke('setup_state')).then(function (env) {
        env = env || {};
        var state = initial(env.state, {
          defaults: env.defaults,
          home: env.home,
          problem: env.problem
        });
        if (state.show === 'shell') { return { show: 'shell' }; }
        mount(state, bridge, doc, options);
        return { show: 'wizard' };
      });
    }
  };

  if (typeof module === 'object' && module.exports) { module.exports = Wizard; }
  root.Wizard = Wizard;
})(typeof globalThis !== 'undefined' ? globalThis : this);