"""A client for this server's OpenSubsonic surface, usable from outside this app.

`sdk.subsonic` drives a device's library, playlists, stars, bookmarks and byte ranges against
the endpoints `subsonic.py` implements, and says so plainly when one is not implemented.
`sdk.capabilities` reads `/api/v1/capabilities` so an optional domain can be hidden instead of
probed, and degrades to the OpenSubsonic core when the document is missing. Stdlib only; see
`docs/client-sdk.md`.
"""
from .capabilities import (Capabilities, Feature, Features, ServerInfo, SubsonicInfo, probe)
from .subsonic import (Album, Artist, Bookmark, ClientError, Credentials, Extension, GenericError,
                       IncompatibleClient, MissingParameter, NotFound, NotAuthorized, NotSupported,
                       Playlist, SearchResult, Song, SubsonicClient, SubsonicError, TrialExpired,
                       WrongCredentials, error_for)

__all__ = ['Album', 'Artist', 'Bookmark', 'Capabilities', 'ClientError', 'Credentials',
           'Extension', 'Feature', 'Features', 'GenericError', 'IncompatibleClient',
           'MissingParameter', 'NotFound', 'NotAuthorized', 'NotSupported', 'Playlist',
           'SearchResult', 'ServerInfo', 'Song', 'SubsonicClient', 'SubsonicError',
           'SubsonicInfo', 'TrialExpired', 'WrongCredentials', 'error_for', 'probe']