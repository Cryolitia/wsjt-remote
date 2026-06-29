# WSJT-X Remote

Lightweight browser remote control for WSJT-X using the documented UDP `NetworkMessage` protocol.

## Features

- Shows WSJT-X status and Band Activity in the browser.
- Sends CQ using `FreeText`: `CQ {DE call} {grid4}`.
- Replies to a selected decode with the WSJT-X `Reply` message.
- Sends Halt TX, Replay, Clear, and arbitrary free text commands.
- Provides a `/debug` page with raw UDP hex, parsed messages, parse errors, and manual command sending.
- Keeps a frontend-only Watch List: double-click an All Activity row or press Watch to add it.

Frequency switching is intentionally not implemented yet. The current WSJT-X UDP protocol does not expose a direct “set dial frequency” command.

## Layout

```text
backend/
  start.py
  requirements.txt
  wsjtx_remote/
    protocol.py
    state.py
    udp.py
    web.py
    app.py

frontend/
  package.json
  tsconfig.json
  index.html
  debug.html
  src/
  dist/
```

## Build With Nix

The frontend is built by Nix using `tsc` and `esbuild`. It has no npm install step.

Build the package:

```bash
nix build .#wsjt-remote
```

`nix build .` builds the same default package.

Build only the backend package:

```bash
nix build .#backend
```

## Install Backend

```bash
cd backend
python -m pip install -r requirements.txt
```

## Run

From the repository root:

```bash
python backend/start.py
```

With Nix:

```bash
nix run .#wsjt-remote
```

`nix run .` runs the same default package.

Run only the backend:

```bash
nix run .#backend
```

This starts one web service that serves both the frontend and backend API:

```text
http://127.0.0.1:8080/
```

Pass backend arguments after `--`:

```bash
nix run .#wsjt-remote -- --web-host 0.0.0.0
```

Load a read-only ADIF log for worked-before lookup:

```bash
nix run .#wsjt-remote -- --adif ./wsjtx_log.adi
```

Logs are written to stderr. Use `--log-level` to change verbosity:

```bash
nix run .#wsjt-remote -- --log-level DEBUG
```

Open:

```text
http://127.0.0.1:8080/
http://127.0.0.1:8080/debug
```

To access from another device on your LAN:

```bash
python backend/start.py --web-host 0.0.0.0
```

Do not expose this service to the public internet. It has no authentication and can transmit through WSJT-X.

## WSJT-X Settings

In WSJT-X:

```text
Settings -> Reporting
UDP Server: backend host IP
UDP Server port number: 2237
Accept UDP requests: enabled
```

If WSJT-X and this service run on the same machine, use:

```text
UDP Server: 127.0.0.1
UDP Server port number: 2237
```

## Frontend Backend URL

If the frontend is served by the backend, no configuration is needed.

If the frontend is hosted elsewhere, set the backend URL at the top of the page, for example:

```text
http://192.168.1.20:8080
```

The value is stored in browser `localStorage`.

## CQ And Enable Tx

The `CQ` button first sends WSJT-X a UDP `FreeText` command for `CQ {DE call} {grid4}`. If WSJT-X is idle, it then uses Niri IPC to find a window whose title/app-id contains `WSJT` or `JTDX`, focuses it, and sends `Alt+N` with `wtype`.

## Development

Backend syntax check:

```bash
cd backend
python -m compileall wsjtx_remote
```
