/* Element fullscreen, where the engine has it.
 *
 * The lyrics reading draws a Fullscreen button, and on a desktop WebView it does something: the
 * overlay takes the window. Not every engine can do that. iOS puts a `<video>` fullscreen and
 * nothing else, and WKWebView follows Safari there, so the button on a phone was a control that
 * could not move anything — the worst kind, because it looks like the one way to make the words
 * bigger. The prefixed spelling predates the standard and is what iPadOS carried from 12
 * onwards, so it counts as support when it is present.
 *
 * The answer is taken from a prototype rather than from the live document so it can be tested:
 * it decides whether a control is drawn at all, and "the button is missing" is not something a
 * screenshot of a working engine can prove.
 */

interface FullscreenCapable {
  requestFullscreen?: unknown;
  webkitRequestFullscreen?: unknown;
}

/** True when this engine can put an element on the whole screen, prefixed or not. */
export function supportsElementFullscreen(proto: FullscreenCapable | null | undefined): boolean {
  if (!proto) return false;
  return (
    typeof proto.requestFullscreen === "function" ||
    typeof proto.webkitRequestFullscreen === "function"
  );
}