/* A line that does not fit scrolls inside its own box, as it does on the host page.
 *
 * `web/styles/components/player.css` already carries the whole visual half of this —
 * `.rolling` on the clipping element, the inline-flex wrapper, `--mq-dist` / `--mq-dur`, and the
 * `pl-title-mq` / `pf-title-mq` keyframes that read them, plus the reduced-motion opt-out. What
 * the stylesheet cannot do is decide *whether* a line overflows, and that is all this is: the
 * host's `fitMarquee()`, with its two numbers (24 px gap and a 2600 ms floor in the docked bar,
 * 28 px and 3200 ms in the fullscreen player).
 *
 * The element is owned by this function rather than by the template, exactly as the host owns it:
 * it sets the text itself, measures, and — only when the text is wider than the box — replaces
 * the text with two copies, the second `aria-hidden` so a screen reader still hears one name.
 */

export interface MarqueeFit {
  /** `plmqt` in the docked bar, `pfmqt` in the fullscreen player. */
  wrapClass: "plmqt" | "pfmqt";
  /** The gap between the last character and the first of the second copy. */
  gap: number;
  /** The floor under the travel time, so a short overflow does not flicker past. */
  floor: number;
}

export const MARQUEE_BAR: MarqueeFit = { wrapClass: "plmqt", gap: 24, floor: 2600 };
export const MARQUEE_FULL: MarqueeFit = { wrapClass: "pfmqt", gap: 28, floor: 3200 };

/** Pixels per second the line travels; the host's number, shared by both surfaces. */
const SPEED = 42;

/** The plain line, and nothing else. Two lines that share one flex row have to be reset together
 * before either is measured: the first one measured against a row where the other is still empty
 * gets room it will not have, and a name that only just fitted is then squeezed afterwards with
 * no second look. */
export function resetMarquee(el: HTMLElement | undefined | null, text: string): void {
  if (!el) return;
  el.classList.remove("rolling");
  el.textContent = text;
}

export function fitMarquee(el: HTMLElement | undefined | null, text: string, fit: MarqueeFit): void {
  if (!el) return;
  /* Reset first: the marquee's own width is not the text's width, so every measurement below
     has to start from the plain line. */
  resetMarquee(el, text);
  if (!text) return;

  /* Nothing to measure while the surface is hidden: the docked bar is `display:none` until there
     is a track, and the fullscreen player likewise until it is opened. One retry on the next
     frame, marked with the line it was for, so a genuinely hidden surface does not spin — the
     host's `dataset.mqRetry` rule. The mark is cleared as soon as a width exists, so a later
     hide-and-show measures again. */
  if (!el.clientWidth) {
    if (el.dataset.mqRetry !== text) {
      el.dataset.mqRetry = text;
      requestAnimationFrame(() => fitMarquee(el, text, fit));
    }
    return;
  }
  delete el.dataset.mqRetry;

  /* The host honours the reader's system setting and leaves the line truncated rather than
     animating it; the stylesheet's own `prefers-reduced-motion` block would stop the animation
     anyway, and a stopped two-copy marquee is a line with its end cut off. */
  if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;

  if (el.scrollWidth <= el.clientWidth + 2) return;

  const wrap = document.createElement("span");
  wrap.className = fit.wrapClass;
  const first = document.createElement("span");
  first.textContent = text;
  const second = document.createElement("span");
  second.setAttribute("aria-hidden", "true");
  second.style.marginLeft = `${fit.gap}px`;
  second.textContent = text;
  wrap.append(first, second);
  el.replaceChildren(wrap);
  el.classList.add("rolling");

  /* The distance is the first copy plus the gap: `scrollWidth` on a laid-out inline box is
     already rounded by the browser, `getBoundingClientRect` may carry a fraction, so the larger
     of the two is taken — the host's rule, and the reason a `.5` tie does not leave the last
     letter clipped. */
  const distance = Math.max(first.scrollWidth, Math.round(first.getBoundingClientRect().width)) + fit.gap;
  const travel = Math.max(fit.floor, Math.round((distance / SPEED) * 1000));
  wrap.style.setProperty("--mq-dist", `${distance}px`);
  wrap.style.setProperty("--mq-dur", `${travel}ms`);
}