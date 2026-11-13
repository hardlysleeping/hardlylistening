"""The desktop shell's non-Rust logic.

`resolve` answers where the server is and which address to talk to; `tests/test_desktop_host.py`
is its test. Nothing here imports the server: the shell is a client of the HTTP API, and the
package must be deletable without the headless install noticing.
"""