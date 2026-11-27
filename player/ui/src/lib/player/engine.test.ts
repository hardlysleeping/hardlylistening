/* Cueing a song is a swap, not a request.
 *
 * The defect this pins: the element was only given an address at the moment of the press, so the
 * silence between the tap and the sound was the network round trip plus WebKit's own start-up
 * (measured: ~120–150 ms to first byte through the tunnel for a request the server answers in
 * 2 ms, then the media pipeline's start on top). A second element warmed ahead of the press turns
 * that into handing over a buffer.
 *
 * Every test here drives two fake elements, because the property under test — "the warmed element
 * becomes the one playing, and its bytes were not asked for again" — only exists if there are two.
 */

import { describe, expect, it, vi } from "vitest";
import { WebAudioEngine, type EngineEvents } from "./engine";

class FakeAudio {
  src = "";
  preload = "";
  volume = 1;
  muted = false;
  currentTime = 0;
  duration = Number.NaN;
  readyState = 0;
  #listeners = new Map<string, Set<() => void>>();

  load = vi.fn(() => {});
  play = vi.fn(async () => {
    this.fire("play");
    this.fire("playing");
  });
  pause = vi.fn(() => this.fire("pause"));
  removeAttribute = vi.fn((name: string) => {
    if (name === "src") this.src = "";
  });

  addEventListener(name: string, handler: () => void): void {
    const set = this.#listeners.get(name) ?? new Set();
    set.add(handler);
    this.#listeners.set(name, set);
  }

  fire(name: string): void {
    for (const handler of this.#listeners.get(name) ?? []) handler();
  }
}

function harness() {
  const elements: FakeAudio[] = [];
  const events = {
    time: vi.fn(),
    ended: vi.fn(),
    playing: vi.fn(),
    loading: vi.fn(),
    error: vi.fn(),
  };
  const engine = new WebAudioEngine(
    events as unknown as EngineEvents,
    () => {
      const element = new FakeAudio();
      elements.push(element);
      return element as unknown as HTMLAudioElement;
    },
  );
  return { engine, events, elements };
}

describe("a warmed source", () => {
  it("plays from the second buffer instead of asking the network again", () => {
    const { engine, elements } = harness();
    const [first, second] = elements as [FakeAudio, FakeAudio];

    engine.setSource("https://music.example.com/rest/stream?id=one");
    first.readyState = 4;
    engine.preload("https://music.example.com/rest/stream?id=two");
    second.readyState = 3;
    const loadsBefore = second.load.mock.calls.length;

    engine.setSource("https://music.example.com/rest/stream?id=two");

    expect(engine.element).toBe(second as unknown as HTMLAudioElement);
    // The warmed element kept its buffer: nothing new was loaded on it.
    expect(second.load.mock.calls.length).toBe(loadsBefore);
    // And the one it replaced gave its buffer back.
    expect(first.pause).toHaveBeenCalled();
    expect(first.removeAttribute).toHaveBeenCalledWith("src");
  });

  it("does not take the play button through a pause while swapping", () => {
    const { engine, events, elements } = harness();
    const [first, second] = elements as [FakeAudio, FakeAudio];
    engine.setSource("https://music.example.com/rest/stream?id=one");
    engine.preload("https://music.example.com/rest/stream?id=two");
    second.readyState = 3;
    events.playing.mockClear();

    engine.setSource("https://music.example.com/rest/stream?id=two");
    engine.play();
    first.fire("pause"); // the dropped element says it stopped; that is not about this song

    expect(events.playing.mock.calls.map(([state]) => state)).not.toContain(false);
  });

  it("is ignored while it is still fetching, so a press never lands on an empty buffer", () => {
    const { engine, elements } = harness();
    const [first, second] = elements as [FakeAudio, FakeAudio];
    engine.setSource("https://music.example.com/rest/stream?id=one");
    engine.preload("https://music.example.com/rest/stream?id=two");
    second.readyState = 1;

    engine.setSource("https://music.example.com/rest/stream?id=two");

    expect(engine.element).toBe(first as unknown as HTMLAudioElement);
    expect(first.src).toBe("https://music.example.com/rest/stream?id=two");
    expect(first.load).toHaveBeenCalled();
  });

  it("asks for its bytes ahead of time, and only there", () => {
    const { engine, elements } = harness();
    const [first, second] = elements as [FakeAudio, FakeAudio];

    engine.preload("https://music.example.com/rest/stream?id=two");

    expect(second.preload).toBe("auto");
    expect(second.src).toBe("https://music.example.com/rest/stream?id=two");
    expect(second.load).toHaveBeenCalled();
    // The element the person hears keeps fetching metadata only; it is not the one pre-fetching.
    expect(first.preload).toBe("metadata");
    expect(first.src).toBe("");
  });

  it("is dropped when nothing follows", () => {
    const { engine, elements } = harness();
    const [, second] = elements as [FakeAudio, FakeAudio];
    engine.preload("https://music.example.com/rest/stream?id=two");

    engine.preload(null);

    expect(second.removeAttribute).toHaveBeenCalledWith("src");
  });
});

describe("the element that is playing", () => {
  it("is the only one whose events reach the store", () => {
    const { engine, events, elements } = harness();
    const [first, second] = elements as [FakeAudio, FakeAudio];
    engine.setSource("https://music.example.com/rest/stream?id=one");
    engine.preload("https://music.example.com/rest/stream?id=two");
    events.time.mockClear();
    events.ended.mockClear();
    events.error.mockClear();

    second.currentTime = 12;
    second.fire("timeupdate");
    second.fire("ended");
    second.fire("error");

    expect(events.time).not.toHaveBeenCalled();
    expect(events.ended).not.toHaveBeenCalled();
    expect(events.error).not.toHaveBeenCalled();

    first.currentTime = 7;
    first.fire("timeupdate");
    expect(events.time).toHaveBeenCalledWith(7, 0);
  });

  it("keeps the level across a swap", () => {
    const { engine, elements } = harness();
    const [first, second] = elements as [FakeAudio, FakeAudio];
    engine.setSource("https://music.example.com/rest/stream?id=one");
    engine.setVolume(0.4);
    engine.setMuted(true);
    engine.preload("https://music.example.com/rest/stream?id=two");
    second.readyState = 3;

    engine.setSource("https://music.example.com/rest/stream?id=two");

    expect(engine.element).toBe(second as unknown as HTMLAudioElement);
    expect(second.volume).toBeCloseTo(0.4);
    expect(second.muted).toBe(true);
    expect(first.volume).toBeCloseTo(0.4);
  });
});