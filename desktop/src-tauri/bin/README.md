# Staged sidecar

Empty in a checkout, on purpose. `tauri.conf.json` declares

```json
"bundle": { "externalBin": ["bin/hardlylistening-server"] }
```

and Tauri resolves that to **`bin/hardlylistening-server-<target-triple>`**, where the triple is
the build host's (`rustc -vV`, line `host:`). `beforeBuildCommand` runs

```sh
python3 ../tools/stage_sidecar.py
```

which copies the canonical launcher here under that name and refuses to finish if the copy is not
byte-identical. The suffix is not cosmetic: without it the bundle builds and then ships without a
server, and the failure only shows up when somebody presses Start.

Verify a staged copy at any time:

```sh
python3 ../tools/stage_sidecar.py --check
```

Do not edit anything in this directory. If the sidecar needs to behave differently, the change
belongs in `bin/hardlylistening-server` or `cli.py`, so systemd, Docker and the desktop all get
it.