//! The order a native player plays in, and the messages it sends back.
//!
//! This file is why the mobile engine can be reviewed at all on a machine that cannot build a
//! phone app. The platform player (AVQueuePlayer, Media3) owns the bytes and the lock screen, but
//! the *rules* — what plays after the last song, what a skip does at either end, what "repeat
//! one" means to a manual press — are not the platform's to invent. They live here, as plain
//! data, and they are the same rules the browser engine gets from `PlayerStore`.
//!
//! The other half is the shape of the events. Rust and the page share one channel
//! (`hl:native-audio`), and the page branches on `kind`; a payload this file can produce is a
//! payload `native-engine.ts` already understands, and
//! `player/ui/src/lib/player/native-engine.test.ts` pins the same shape from the other side.
//!
//! Nothing here talks to a socket, a player or Tauri, so `cargo test` on the module alone is a
//! real test of it.

use serde::Serialize;

/// What to do at the end of the order.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Repeat {
    Off,
    All,
    One,
}

impl Repeat {
    pub fn parse(raw: &str) -> Result<Repeat, String> {
        match raw {
            "off" => Ok(Repeat::Off),
            "all" => Ok(Repeat::All),
            "one" => Ok(Repeat::One),
            other => Err(format!("\"{other}\" is not a repeat mode")),
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Repeat::Off => "off",
            Repeat::All => "all",
            Repeat::One => "one",
        }
    }
}

/// What the player should do next. `Repeat` is not "play something else" — it means the current
/// track starts again, which on a real player is a seek and a continue.
#[derive(Clone, PartialEq, Eq, Debug)]
pub enum Step {
    Next(usize),
    Repeat(usize),
    Stop,
}

/// The songs, in the order they will play, and where the playhead is.
#[derive(Clone, Debug)]
pub struct Queue {
    ids: Vec<String>,
    repeat: Repeat,
    at: Option<usize>,
}

impl Queue {
    pub fn new(ids: Vec<String>, repeat: Repeat) -> Queue {
        Queue {
            ids,
            repeat,
            at: None,
        }
    }

    pub fn len(&self) -> usize {
        self.ids.len()
    }

    pub fn is_empty(&self) -> bool {
        self.ids.is_empty()
    }

    pub fn at(&self) -> Option<usize> {
        self.at
    }

    pub fn repeat(&self) -> Repeat {
        self.repeat
    }

    pub fn set_repeat(&mut self, repeat: Repeat) {
        self.repeat = repeat;
    }

    /// Start the item at `index`. Out of range is refused rather than clamped: a caller asking
    /// for the ninth song of eight has a bug, and silently playing the last one hides it.
    pub fn play(&mut self, index: usize) -> Option<&str> {
        let id = self.ids.get(index)?;
        self.at = Some(index);
        Some(id)
    }

    /// The track ended. This is what a real player calls when it reaches the end of a file.
    pub fn advance(&mut self) -> Step {
        let at = match self.at {
            Some(at) => at,
            None => return Step::Stop,
        };
        match self.repeat {
            Repeat::One => Step::Repeat(at),
            Repeat::All => {
                let next = (at + 1) % self.ids.len();
                self.at = Some(next);
                Step::Next(next)
            }
            Repeat::Off => {
                if at + 1 < self.ids.len() {
                    self.at = Some(at + 1);
                    Step::Next(at + 1)
                } else {
                    // The order ran out. Repeat is off, so the queue stops where it is.
                    Step::Stop
                }
            }
        }
    }

    /// A person pressed next or previous.
    ///
    /// Deliberately not `advance()` with a sign: a manual press moves on even when repeat is
    /// "one" (pressing next on repeat-one has to go somewhere), and pressing back from the first
    /// song restarts it rather than jumping to the end.
    pub fn skip(&mut self, forward: bool) -> Step {
        let at = match self.at {
            Some(at) => at,
            None => return Step::Stop,
        };
        if forward {
            if at + 1 < self.ids.len() {
                self.at = Some(at + 1);
                Step::Next(at + 1)
            } else if self.repeat == Repeat::All && !self.ids.is_empty() {
                self.at = Some(0);
                Step::Next(0)
            } else {
                Step::Stop
            }
        } else if at > 0 {
            self.at = Some(at - 1);
            Step::Next(at - 1)
        } else {
            // The first song has nowhere back to go, so it starts again.
            Step::Repeat(0)
        }
    }

    /// Every item's address, in order.
    ///
    /// A queue is built before it plays, so one song the server will not name is a reason to say
    /// so by id and play nothing, rather than to start a queue that stalls at track four.
    pub fn addresses<F>(&self, resolve: F) -> Result<Vec<String>, String>
    where
        F: Fn(&str) -> Result<String, String>,
    {
        let mut out = Vec::with_capacity(self.ids.len());
        for id in &self.ids {
            match resolve(id) {
                Ok(url) => out.push(url),
                Err(why) => return Err(format!("{id}: {why}")),
            }
        }
        Ok(out)
    }
}

/// One message to the page. The JSON shape is the contract: `native-engine.ts` switches on `kind`.
#[derive(Serialize, PartialEq, Debug)]
#[serde(tag = "kind", rename_all = "lowercase")]
pub enum Event {
    /// The player moved to this position in the order, by itself or because it was asked to.
    Track {
        index: usize,
    },
    Position {
        position: f64,
        duration: f64,
    },
    Playing {
        playing: bool,
    },
    /// The order ran out. Not "one track ended" — the player advances itself.
    Ended,
    Error {
        message: String,
    },
}

/* ---- the argument contract the commands apply ----------------------------------- */

/// A queue as handed over by the page: ids in playing order, and what to do at the end.
pub fn checked_queue(ids: Vec<String>, repeat: &str) -> Result<Queue, String> {
    if ids.is_empty() {
        return Err("A queue with no songs has nothing to play".to_string());
    }
    if ids.iter().any(|id| id.trim().is_empty()) {
        return Err("A queue cannot hold an empty song id".to_string());
    }
    let mode = Repeat::parse(repeat)?;
    Ok(Queue::new(ids, mode))
}

/// An index into the order that was handed over. Out of range is a caller bug, not a clamp.
pub fn checked_index(queue: &Queue, index: usize) -> Result<usize, String> {
    if index >= queue.len() {
        return Err(format!(
            "There is no song {index} in a queue of {}",
            queue.len()
        ));
    }
    Ok(index)
}

/// A position on the track: finite and not negative.
pub fn checked_seconds(seconds: f64) -> Result<f64, String> {
    if !seconds.is_finite() || seconds < 0.0 {
        return Err("\"seconds\" must be a position on the track".to_string());
    }
    Ok(seconds)
}

/// A fader position, clamped to the range a fader has.
pub fn checked_volume(volume: f64) -> Result<f64, String> {
    if !volume.is_finite() {
        return Err("\"volume\" must be a number".to_string());
    }
    Ok(volume.max(0.0).min(1.0))
}

/// `1` is forward, `-1` is back. Anything else is a caller that has not read the contract.
pub fn checked_direction(direction: i32) -> Result<bool, String> {
    match direction {
        1 => Ok(true),
        -1 => Ok(false),
        other => Err(format!("{other} is not a direction; use 1 or -1")),
    }
}

/// The JSON the page receives for one event. One writer for the channel, so the payload a
/// platform bridge emits is the payload `native-engine.ts` already switches on.
pub fn event_payload(event: &Event) -> Result<serde_json::Value, String> {
    serde_json::to_value(event).map_err(|error| format!("Could not describe the event — {error}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ids(values: &[&str]) -> Vec<String> {
        values.iter().map(|value| value.to_string()).collect()
    }

    fn queue(ids: &[&str], repeat: Repeat) -> Queue {
        Queue::new(ids.iter().map(|id| id.to_string()).collect(), repeat)
    }

    #[test]
    fn an_empty_order_never_plays() {
        let mut empty = queue(&[], Repeat::All);
        assert_eq!(empty.play(0), None);
        assert_eq!(empty.at(), None);
        assert_eq!(empty.advance(), Step::Stop);
        assert_eq!(empty.skip(true), Step::Stop);
        assert!(empty.is_empty());
    }

    #[test]
    fn a_song_is_played_by_its_position_and_nothing_is_invented() {
        let mut list = queue(&["a", "b", "c"], Repeat::Off);
        assert_eq!(list.play(1), Some("b"));
        assert_eq!(list.at(), Some(1));
        // Out of range is refused and the playhead does not move.
        assert_eq!(list.play(3), None);
        assert_eq!(list.at(), Some(1));
    }

    #[test]
    fn advancing_walks_the_order_and_stops_at_the_end() {
        let mut list = queue(&["a", "b"], Repeat::Off);
        list.play(0);

        assert_eq!(list.advance(), Step::Next(1));
        assert_eq!(list.advance(), Step::Stop);
        // Stopped where it was, not wrapped and not cleared.
        assert_eq!(list.at(), Some(1));
    }

    #[test]
    fn repeat_all_wraps_from_the_last_song_to_the_first() {
        let mut list = queue(&["a", "b"], Repeat::All);
        list.play(1);

        assert_eq!(list.advance(), Step::Next(0));
        assert_eq!(list.at(), Some(0));
    }

    #[test]
    fn repeat_one_asks_for_the_same_song_again_rather_than_moving() {
        let mut list = queue(&["a", "b"], Repeat::One);
        list.play(1);

        assert_eq!(list.advance(), Step::Repeat(1));
        assert_eq!(list.at(), Some(1));
    }

    #[test]
    fn a_manual_skip_moves_on_even_when_repeat_is_one() {
        let mut list = queue(&["a", "b"], Repeat::One);
        list.play(0);

        // The person pressed next. Repeat-one is about the end of a track, not about a button.
        assert_eq!(list.skip(true), Step::Next(1));
        assert_eq!(list.at(), Some(1));
    }

    #[test]
    fn skipping_back_from_the_first_song_restarts_it() {
        let mut list = queue(&["a", "b"], Repeat::Off);
        list.play(0);

        assert_eq!(list.skip(false), Step::Repeat(0));
        assert_eq!(list.at(), Some(0));

        list.play(1);
        assert_eq!(list.skip(false), Step::Next(0));
    }

    #[test]
    fn a_skip_past_the_end_stops_unless_repeat_all_is_on() {
        let mut off = queue(&["a", "b"], Repeat::Off);
        off.play(1);
        assert_eq!(off.skip(true), Step::Stop);

        let mut all = queue(&["a", "b"], Repeat::All);
        all.play(1);
        assert_eq!(all.skip(true), Step::Next(0));
    }

    #[test]
    fn the_repeat_mode_changes_without_touching_the_playhead() {
        let mut list = queue(&["a", "b", "c"], Repeat::Off);
        list.play(2);

        list.set_repeat(Repeat::All);

        assert_eq!(list.repeat(), Repeat::All);
        assert_eq!(list.at(), Some(2));
    }

    #[test]
    fn every_address_is_resolved_in_order_and_a_failure_names_the_song() {
        let list = queue(&["a", "b"], Repeat::Off);

        let urls = list
            .addresses(|id| {
                Ok(format!(
                    "https://music.example.com/rest/stream.view?id={id}"
                ))
            })
            .expect("every id resolves");
        assert_eq!(
            urls,
            vec![
                "https://music.example.com/rest/stream.view?id=a",
                "https://music.example.com/rest/stream.view?id=b",
            ]
        );

        // One song the server will not name stops the queue from being built, and says which.
        let error = list
            .addresses(|id| {
                if id == "b" {
                    Err("the keychain has no password".to_string())
                } else {
                    Ok("x".to_string())
                }
            })
            .unwrap_err();
        assert!(error.starts_with("b: "), "{error}");
    }

    #[test]
    fn a_queue_needs_songs_and_a_repeat_mode_it_understands() {
        assert!(checked_queue(ids(&["a", "b"]), "all").is_ok());
        assert_eq!(
            checked_queue(ids(&["a"]), "off").unwrap().len(),
            1,
            "the order is kept as given"
        );

        assert!(checked_queue(ids(&[]), "off").is_err());
        assert!(checked_queue(ids(&["a", ""]), "off").is_err());
        assert!(checked_queue(ids(&["a", "  "]), "off").is_err());
        let error = checked_queue(ids(&["a"]), "forever").unwrap_err();
        assert!(error.contains("forever"), "{error}");
    }

    #[test]
    fn a_play_index_is_checked_against_the_order_that_was_handed_over() {
        let list = checked_queue(ids(&["a", "b"]), "off").unwrap();

        assert_eq!(checked_index(&list, 0), Ok(0));
        assert_eq!(checked_index(&list, 1), Ok(1));
        // The page has no business starting a song it never sent.
        let error = checked_index(&list, 2).unwrap_err();
        assert!(error.contains("queue of 2"), "{error}");
    }

    #[test]
    fn a_seek_is_a_finite_position_and_a_volume_is_clamped() {
        assert_eq!(checked_seconds(0.0), Ok(0.0));
        assert_eq!(checked_seconds(12.5), Ok(12.5));
        assert!(checked_seconds(-1.0).is_err());
        assert!(checked_seconds(f64::NAN).is_err());
        assert!(checked_seconds(f64::INFINITY).is_err());

        assert_eq!(checked_volume(0.5), Ok(0.5));
        assert_eq!(checked_volume(-2.0), Ok(0.0));
        assert_eq!(checked_volume(9.0), Ok(1.0));
        assert!(checked_volume(f64::NAN).is_err());
    }

    #[test]
    fn a_skip_direction_is_one_of_two_values() {
        assert_eq!(checked_direction(1), Ok(true));
        assert_eq!(checked_direction(-1), Ok(false));
        for wrong in [0, 2, -7] {
            assert!(checked_direction(wrong).is_err(), "{wrong}");
        }
    }

    #[test]
    fn the_events_are_the_shape_the_page_listens_for() {
        let value = |event: Event| serde_json::to_value(event).expect("an event serialises");

        // Internal tag: the page switches on `kind` and nothing else.
        assert_eq!(
            value(Event::Track { index: 3 }),
            serde_json::json!({ "kind": "track", "index": 3 })
        );
        assert_eq!(
            value(Event::Position {
                position: 12.5,
                duration: 200.0
            }),
            serde_json::json!({ "kind": "position", "position": 12.5, "duration": 200.0 })
        );
        assert_eq!(
            value(Event::Playing { playing: true }),
            serde_json::json!({ "kind": "playing", "playing": true })
        );
        assert_eq!(value(Event::Ended), serde_json::json!({ "kind": "ended" }));
        assert_eq!(
            value(Event::Error {
                message: "the file is gone".to_string()
            }),
            serde_json::json!({ "kind": "error", "message": "the file is gone" })
        );
    }
}
