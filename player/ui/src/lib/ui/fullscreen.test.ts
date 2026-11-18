import { describe, expect, it } from "vitest";
import { supportsElementFullscreen } from "./fullscreen";

describe("element fullscreen support", () => {
  it("is the standard method when the engine has it", () => {
    expect(supportsElementFullscreen({ requestFullscreen() {} })).toBe(true);
  });

  it("accepts the prefixed method iPadOS has carried", () => {
    expect(supportsElementFullscreen({ webkitRequestFullscreen() {} })).toBe(true);
  });

  it("is false where the engine can only fullscreen a video", () => {
    /* iOS Safari and WKWebView: a `<video>` can go fullscreen, an element cannot. The lyrics
       overlay is an element, so the button must not be drawn — it would be a control that
       cannot move anything. */
    expect(supportsElementFullscreen({})).toBe(false);
    expect(supportsElementFullscreen(undefined)).toBe(false);
    expect(supportsElementFullscreen(null)).toBe(false);
  });

  it("does not count a property that is not callable", () => {
    expect(supportsElementFullscreen({ requestFullscreen: true })).toBe(false);
  });
});