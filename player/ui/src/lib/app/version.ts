/**
 * The build a person is looking at.
 *
 * It exists because a stale install and a fresh one used to be indistinguishable: the app said
 * "could not reach" and there was no way to tell whether the phone held the build with the
 * certificate fix or the one before it. `tests/test_player_shell.py` keeps this in step with
 * `src-tauri/tauri.conf.json` and `ui/package.json`, which is what the bundle is stamped with.
 */
export const APP_VERSION = "0.1.1";
