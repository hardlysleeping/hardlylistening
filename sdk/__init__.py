"""A client for this server's OpenSubsonic surface, usable from outside this app.

`sdk.subsonic` drives a device's library, playlists, stars, bookmarks and byte ranges against
the endpoints `subsonic.py` implements, and says so plainly when one is not implemented.
`sdk.capabilities` reads `/api/v1/capabilities` so an optional domain can be hidden instead of
probed, and degrades to the OpenSubsonic core when the document is missing. `sdk.social` drives
the Activity feed, comments, votes, likes and boosts — the versioned reads and the unversioned
writes — and refuses by name when the document does not advertise the flag an entry point needs.
Stdlib only; see `docs/client-sdk.md`.

Social's refusals (`Unavailable`, `Denied`, `NotFound`, `SignInRequired`, `RateLimited`) stay in
`sdk.social`: `NotFound` and `ClientError` already mean a Subsonic answer in this namespace, and
one word cannot carry two protocols.
"""
from .capabilities import (Capabilities, Feature, Features, ServerInfo, SubsonicInfo, probe)
from .social import (Boost, Comment, CommentPage, DayCard, Entry, Member, Session, SocialClient,
                     SocialError, Summary, Track, Voter)
from .subsonic import (Album, Artist, Bookmark, ClientError, Credentials, Extension, GenericError,
                       IncompatibleClient, MissingParameter, NotFound, NotAuthorized, NotSupported,
                       Playlist, SearchResult, Song, SubsonicClient, SubsonicError, TrialExpired,
                       WrongCredentials, error_for)

__all__ = ['Album', 'Artist', 'Bookmark', 'Boost', 'Capabilities', 'ClientError', 'Comment',
           'CommentPage', 'Credentials', 'DayCard', 'Entry', 'Extension', 'Feature', 'Features',
           'GenericError', 'IncompatibleClient', 'Member', 'MissingParameter', 'NotFound',
           'NotAuthorized', 'NotSupported', 'Playlist', 'SearchResult', 'ServerInfo', 'Session',
           'SocialClient', 'SocialError', 'Song', 'SubsonicClient', 'SubsonicError', 'SubsonicInfo',
           'Summary', 'Track', 'TrialExpired', 'Voter', 'WrongCredentials', 'error_for', 'probe']