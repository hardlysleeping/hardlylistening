# Subsonic compatibility, measured against another implementation (HL-231)

Every Subsonic test before this card had our server answering our client's questions. That
proves the surface is self-consistent, not compatible: a third-party server arranges the same
protocol differently, and a strict third-party client refuses to guess. This card is the
outside opinion.

Two directions, both pinned to recorded bytes:

* **our client against a third-party server** — `sdk/subsonic.py` parses real Navidrome
  responses with no network and no server of ours in the loop.
* **our server against the recorded contract** — our live answers, cell by cell, where the
  recording says what a client is allowed to assume.

`tests/test_subsonic_fidelity.py` is the proof; this file is the matrix. One row, one cell, one
test. Nothing here is claimed without a recorded response behind it.

## Where the recordings come from

| | |
| --- | --- |
| server | Navidrome `0.64.2 (10114574)`, a public demo instance |
| base URL | `https://demo.navidrome.org` (`demo`/`demo`) |
| protocol | Subsonic `1.16.1`, `f=json` |
| recorded | see `tests/fixtures/subsonic/navidrome-0.64.2/manifest.json` |
| how | `python3 tools/record-subsonic-fixtures.py navidrome-0.64.2 https://demo.navidrome.org demo demo` |

Thirteen responses, each filed with its endpoint, parameters and SHA-256 of the bytes the server
sent. The recorder only ever issues `GET`: nothing on the recorded server is starred, scanned or
written. It names its own `User-Agent` because the demo answers `403` to `Python-urllib`'s
default one — recorded here so a refresh does not look like a protocol failure.

Ids chain from the listing to the detail endpoints (`getArtists` → `getArtist` → `getAlbum` →
`getSong`), so the fixture set is internally consistent rather than invented.

## The matrix

### Both directions

| # | behaviour | Navidrome 0.64.2, recorded | HardlyListening, live | test |
| --- | --- | --- | --- | --- |
| 1 | envelope names status, version, type, serverVersion | all four present | all four present, version ≥ 1.16.1 | `OurServerHoldsTheSameContract.test_cell_1_the_envelope_names_the_server_and_the_protocol` |
| 2 | `openSubsonic: true` and an extension list with integer versions | 7 extensions, including `songLyrics`, `formPost`, `transcoding` | 3 extensions, same shape | `...test_cell_2_open_subsonic_is_declared_with_versions` |
| — | the client reads a third-party extension list | `songLyrics` and `formPost` parsed as `Extension` values | — | `RecordedThirdParty.test_cell_2_open_subsonic_extensions_are_read_from_a_third_party_server` |
| 5 | a refusal is a `200` envelope with `status=failed` and a numeric `code` | `70` for an artist id that does not exist | `40` for wrong credentials | `...test_cell_5_an_error_is_a_200_envelope_with_a_numeric_code` |
| 6 | an unknown object is error `70`, not an empty result | `getCoverArt` → `70` "Artwork not found" | `getCoverArt` → `70` | `...test_cell_6_an_unknown_cover_art_id_is_error_70` |
| 7 | `size`/`offset` are honoured | `getAlbumList2` honours `size=5` | page equals the same slice of the full list; past the end is `[]` | `...test_cell_7_pagination_parameters_are_honoured_not_ignored` |
| 8 | a song row carries what a player needs to play and cache it | `id`, `title`, `album`, `duration`, `size`, `isDir`, `created`, `contentType`, `suffix`, `parent` | the same ten | `...test_cell_8_our_child_objects_carry_the_same_fields` |
| 9 | lists use the singular key in array form (`playlists.playlist`) | array | array | `...test_cell_9_lists_use_the_singular_key_in_array_form` |
| 10 | an empty answer is empty, not a failure | `getLyricsBySongId` → `status=ok`, `lyricsList {}` | `search3` → `song/album/artist` all `[]` | `...test_cell_10_an_empty_search_is_an_empty_array` |
| 11 | a declared extension can actually be asked | `songLyrics` declared, `getLyricsBySongId` answered | same | `...test_cell_11_a_declared_lyrics_extension_answers_even_with_no_lyrics` |

### Our server only (no third-party recording exists for these)

| # | behaviour | Navidrome 0.64.2, recorded | HardlyListening, live | test |
| --- | --- | --- | --- | --- |
| 3 | JSON and XML are the same answer | recorded as JSON only | `f=xml` returns the same status/version and the same artist count | `...test_cell_3_json_and_xml_are_the_same_answer` |
| 4 | `formPost` is real because it is declared | extension declared | credentials in a form body authenticate | `...test_cell_4_form_post_is_real_because_it_is_declared` |
| 12 | an unimplemented endpoint is error `30`, never a fake empty answer | — | `getSonicSimilarTracks`, `findSonicPath`, `getVideos` → `30` | `...test_cell_12_an_unimplemented_endpoint_is_error_30_not_a_fake_empty_answer` |

### The client, against a server that is not ours

| # | behaviour | test |
| --- | --- | --- |
| 8 | a third-party song parses into the same value type we use for ours | `RecordedThirdParty.test_cell_8_child_objects_carry_what_a_player_needs` |
| 9 | `artists.index[].artist` flattens into one list | `...test_cell_9_nested_lists_keep_their_singular_key` |
| 10 | an empty lyrics answer is not an error | `...test_cell_10_an_empty_answer_is_an_empty_list_not_an_error` |
| 5 | a missing parameter is a protocol error, not an HTTP failure | `...test_cell_5_a_missing_parameter_is_a_protocol_error_not_an_http_failure` |
| 6 | an unknown id raises `NotFound` carrying `70` | `...test_cell_6_an_unknown_object_is_a_protocol_error` |
| 11 | a declared extension is asked for, and answered | `...test_cell_11_a_declared_extension_is_answered_by_a_third_party_server` |

## What the recordings corrected

* **A missing `id` is `70`, not `10`.** The card assumed `getArtist` without an id would be a
  missing-parameter error. Navidrome answers `70` "Artist not found", and so do we — our `find`
  helper treats an empty id as an id that is not in the catalogue. The cell is written from the
  recording, so it asserts `70`.
* **An empty `lyricsList` may omit `structuredLyrics`.** Navidrome sends `{"lyricsList": {}}`
  for a song with no lyrics; we send `{"structuredLyrics": []}`. Both are "no lines", so the
  cell accepts both spellings instead of pinning ours and calling the other broken.
* **XML is namespaced.** Our XML declares `xmlns="http://subsonic.org/restapi"` like the
  protocol says, so the JSON/XML cell compares local names rather than raw tags.

## Limits of this claim

* One third-party server, one version, one set of responses. Navidrome is not the protocol, and
  a second recording (Gonic, Airsonic, Ampache) would be a second opinion, not this card.
* The recordings cover `getArtists`, `getArtist`, `getAlbum`, `getSong`, `getPlaylists`,
  `getPlaylist`, `search3`, `getAlbumList2`, `getOpenSubsonicExtensions`, `ping` and
  `getLyricsBySongId`. Endpoints we answer but the recording does not touch — shares, bookmarks,
  jukebox, transcoding — are still proven only by our own suites.
* No third-party *client* was driven against our server: the client direction is our SDK, whose
  parser is the thing being tested. A real player (Feishin, Symfonium, substreamer) is a manual
  check and is not part of this matrix.
* `hls` and the sonic endpoints answer `30` here; a client that requires them will not play.
  That is a stated limitation, not a compatibility claim.