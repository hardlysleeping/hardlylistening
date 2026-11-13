"""A client for this server's OpenSubsonic surface, usable from outside this app.

`sdk.subsonic` drives a device's library, playlists, stars, bookmarks and byte ranges against
the endpoints `subsonic.py` implements, and says so plainly when one is not implemented.
`sdk.capabilities` reads `/api/v1/capabilities` so an optional domain can be hidden instead of
probed, and degrades to the OpenSubsonic core when the document is missing. `sdk.social` drives
the Activity feed, comments, votes, likes and boosts — the versioned reads and the unversioned
writes — and refuses by name when the document does not advertise the flag an entry point needs.
`sdk.repo` reads the Repository (`/api/v1/repo/*`: snapshot, releases, tracks, artist,
subscriptions) and refuses by name because this server's document names no `repo` domain. It is
the one module the player build must never import; `tests/test_player_store_safe.py` asserts that
over the assembled tree. Stdlib only; see `docs/client-sdk.md`.

The refusals stay in their own modules: `sdk.social` keeps `Unavailable`, `Denied`, `NotFound`,
`SignInRequired`, `RateLimited` and `ClientError`, and `sdk.repo` defines the same names for its
own surface, because `NotFound` and `ClientError` already mean a Subsonic answer in this
namespace and one word cannot carry two protocols. `sdk.repo.Track` stays module-local for the
same reason `sdk.social.Track` exists: the two rows are different shapes.
"""
from .capabilities import (Capabilities, Feature, Features, ServerInfo, SubsonicInfo, probe)
from .repo import ArtistPage, Release, ReleasePage, RepoClient, RepoError, Snapshot, Subscription
from .social import (Boost, Comment, CommentPage, DayCard, Entry, Member, Session, SocialClient,
                     SocialError, Summary, Track, Voter)
from .subsonic import (Album, Artist, Bookmark, ClientError, Credentials, Extension, GenericError,
                       IncompatibleClient, MissingParameter, NotFound, NotAuthorized, NotSupported,
                       Playlist, SearchResult, Song, SubsonicClient, SubsonicError, TrialExpired,
                       WrongCredentials, error_for)

__all__ = ['Album', 'Artist', 'ArtistPage', 'Bookmark', 'Boost', 'Capabilities', 'ClientError',
           'Comment', 'CommentPage', 'Credentials', 'DayCard', 'Entry', 'Extension', 'Feature',
           'Features', 'GenericError', 'IncompatibleClient', 'Member', 'MissingParameter',
           'NotFound', 'NotAuthorized', 'NotSupported', 'Playlist', 'Release', 'ReleasePage',
           'RepoClient', 'RepoError', 'SearchResult', 'ServerInfo', 'Session', 'Snapshot',
           'SocialClient', 'SocialError', 'Song', 'SubsonicClient', 'SubsonicError',
           'SubsonicInfo', 'Subscription', 'Summary', 'Track', 'TrialExpired', 'Voter',
           'WrongCredentials', 'error_for', 'probe']