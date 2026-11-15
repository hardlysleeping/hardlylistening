/* Which engine this window uses.
 *
 * A phone gets the platform's player, because a WebView's `<audio>` cannot promise to keep
 * playing with the screen off. A desktop gets the browser engine, because there is no such
 * promise to keep and no native player to reach.
 *
 * The choice is a question, not an assumption: the shell answers `native_audio_available`, and
 * this file acts on the answer. A build without the command — an older shell, or the desktop
 * build this one is — keeps the browser engine, which is the difference between "the phone plays
 * in the background" and "every play button on the desktop silently does nothing".
 */

import type { InvokeLike } from "../subsonic";
import { NativeAudioEngine, type ListenLike } from "./native-engine";
import type { PlayerStore } from "./store.svelte";

/** The command that says whether this build has a platform player behind it. */
export const NATIVE_AVAILABLE_COMMAND = "native_audio_available";

/**
 * Give the player the native engine when the shell has one.
 *
 * Returns whether it did, so a caller (or a test) can tell "swapped" from "there was nothing to
 * swap to" without reading the player back.
 */
export async function useNativeAudioIfPresent(
  player: PlayerStore,
  invoke: InvokeLike | null,
  listen: ListenLike | null,
): Promise<boolean> {
  if (!invoke) return false;

  let available: unknown = false;
  try {
    available = await invoke(NATIVE_AVAILABLE_COMMAND);
  } catch {
    // No command is an older build, and an older build plays in the WebView as it always did.
    return false;
  }
  if (available !== true) return false;

  // The engine reports into the store's own event object, so a native `track`, `position` or
  // `ended` lands in the same place a web one would.
  player.useEngine(
    new NativeAudioEngine({ invoke, listen, events: player.engineEvents }),
  );
  return true;
}