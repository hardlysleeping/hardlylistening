/* The player's glyphs, copied from `web/index.html`'s `SVG` map byte for byte.
 *
 * They are strings rather than components on purpose: the existing stylesheet sizes them by
 * their own `width`/`height` attributes and colours them with `currentColor`, so a string in
 * the same place produces the same pixels. Componentising them here would be a redesign in
 * disguise.
 */

export const SVG = {
  play: (s: number) =>
    `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5.14v13.72a1 1 0 0 0 1.53.85l10.5-6.86a1 1 0 0 0 0-1.7L9.53 4.29A1 1 0 0 0 8 5.14Z"/></svg>`,
  pause: (s: number) =>
    `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="5" width="4" height="14" rx="1.2"/><rect x="14" y="5" width="4" height="14" rx="1.2"/></svg>`,
  prev: (s: number) =>
    `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="currentColor"><rect x="5" y="5" width="2.6" height="14" rx="1"/><path d="M19 5.9v12.2a1 1 0 0 1-1.55.83l-8.4-6.1a1 1 0 0 1 0-1.66l8.4-6.1A1 1 0 0 1 19 5.9Z"/></svg>`,
  next: (s: number) =>
    `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="currentColor"><rect x="16.4" y="5" width="2.6" height="14" rx="1"/><path d="M5 5.9v12.2a1 1 0 0 0 1.55.83l8.4-6.1a1 1 0 0 0 0-1.66l-8.4-6.1A1 1 0 0 0 5 5.9Z"/></svg>`,
  shuffle: (s: number) =>
    `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M16 3h5v5"/><path d="M4 20 21 3"/><path d="M21 16v5h-5"/><path d="M15 15l6 6"/><path d="M4 4l5 5"/></svg>`,
  repeat: (s: number) =>
    `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="m17 2 4 4-4 4"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><path d="m7 22-4-4 4-4"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/></svg>`,
  repeatOne: (s: number) =>
    `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="m17 2 4 4-4 4"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><path d="m7 22-4-4 4-4"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/><path d="M12 10v4" stroke-width="2.4"/></svg>`,
  volume: (s: number) =>
    `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="currentColor"><path d="M4 9v6h3.5L13 19V5L7.5 9H4Z"/><path d="M16 8.5a5 5 0 0 1 0 7" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>`,
  volumeUp: (s: number) =>
    `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="currentColor"><path d="M4 9v6h3.5L13 19V5L7.5 9H4Z"/><path d="M15.6 9.4a4 4 0 0 1 0 5.2M18.2 7a7 7 0 0 1 0 10" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>`,
  mute: (s: number) =>
    `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="currentColor"><path d="M4 9v6h3.5L13 19V5L7.5 9H4Z"/><path d="m16 9 5 6M21 9l-5 6" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>`,
  chevronDown: (s: number) =>
    `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>`,
  plus: (s: number) =>
    `<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>`,
} as const;