# GalaxyOS Desktop (Stage 1)

> **Status**: stage 1 — desktop shell scaffolds, OpenClaw decoupled, sidecar
> bridgeable to `XiaoYiClawLLM` over pyzmq. No MeMo / ACRouter yet.

## What this is

A standalone desktop application wrapper around the GalaxyOS engine,
decoupled from the OpenClaw plugin runtime. Stage 1 is the "smoke test":
can we run the engine outside OpenClaw and stream a chat response through
a TokUI-style bubble UI?

## Layout

```
desktop-shell/
├── package.json         # Electron + pyzmq + esbuild
├── tsconfig.json        # TS for main + preload
├── esbuild.config.mjs   # bundles main + preload
├── src/
│   ├── main.ts          # Electron main: spawns sidecar, pyzmq client
│   └── preload.ts       # contextBridge → window.galaxy.*
├── renderer/
│   ├── index.html       # TokUI-style chat
│   ├── style.css        # dark theme
│   └── renderer.js      # chat loop
├── python/
│   ├── path_resolver_desktop.py  # shim that replaces path_resolver
│   └── galaxyos_sidecar.py       # pyzmq REP server
├── scripts/
│   ├── dev.mjs          # one-shot dev launcher
│   └── build-python.sh  # PyInstaller bundle
└── README.md            # you are here
```

## How it works (one paragraph)

`main.ts` spawns `galaxyos_sidecar.py` as a child process. The sidecar
imports `path_resolver_desktop` *first* — this installs itself as
`sys.modules['path_resolver']`, replacing the OpenClaw-coupled upstream
default with a desktop-friendly one (`~/.galaxyos/` or
`$GALAXYOS_HOME`). Once the shim is in place, the sidecar lazily
imports `XiaoYiClawLLM` and binds a zmq REP socket on
`tcp://127.0.0.1:5757`. The Electron main process opens a zmq REQ
socket to the same port and forwards renderer `window.galaxy.ask(...)`
calls to the sidecar. The sidecar returns the answer, the renderer
puts it in a bubble.

## Run

Prereqs: Python 3.11+, Node.js 20+.

```bash
cd desktop-shell
npm install
npm run build:main          # esbuild → dist/main.cjs
python -m venv .venv
.venv/Scripts/python -m pip install -r ../requirements-core.txt
.venv/Scripts/python -m pip install pyzmq
npm run dev                 # bundles + launches Electron
```

The first dev launch will take a few minutes to install deps. Subsequent
launches are < 5s.

## Verifying the sidecar standalone

You can run the sidecar without Electron and poke at it from a REPL:

```bash
.venv/Scripts/python desktop-shell/python/galaxyos_sidecar.py
# In another shell:
python -c "
import zmq, json
s = zmq.Context().socket(zmq.REQ)
s.connect('tcp://127.0.0.1:5757')
s.send_json({'id': 1, 'method': 'health', 'params': {}})
print(json.loads(s.recv().decode()))
"
```

Expected:

```json
{"id": 1, "result": {"status": "ok", "version": "0.1.0-stage1",
                      "rccam_enabled": true, "memo_enabled": false,
                      "router_enabled": false}}
```

## Stage 1 acceptance

- [x] `python -m pip install -r requirements-core.txt` imports `galaxyos`
- [x] Sidecar boots without OpenClaw
- [x] `health()` returns 200
- [x] `ask(question)` returns an answer (uses the liquid layer only —
      MeMo / RAG swap comes in Stage 2)
- [x] `remember(content)` writes to local SQLite
- [x] `recall(query)` returns the remembered items
- [ ] Electron window opens and renders bubbles — needs `npm install`
      (deferred to first user run)

## What's NOT in Stage 1

- **MeMo** (Stage 2): frozen knowledge model + Grounding→Entity→Answer
- **Agent-as-a-Router C-A-F loop** (Stage 3): Orchestrator + Verifier + Memory
- **Streaming responses** (Stage 2): the renderer currently waits for
  the full answer; Stage 2 will stream via server-sent events or
  chunked zmq
- **TokUI integration** (Stage 1.5): the renderer is a stand-in HTML
  bubble UI; we'll switch to `@jboltai/tokui` once the package is
  installed and the bubble/stream-event API is wired
- **Build/packaging** (Stage 1.5): `electron-builder` + PyInstaller
  produces a single distributable

## OpenClaw interop

If `OPENCLAW_HOME` is set and points to a real OpenClaw install (i.e.
`extensions/` or `openclaw.json` exists), the shim honours it. This
lets you run the desktop app as a *front-end* to your existing
OpenClaw data without migration. Otherwise it defaults to
`~/.galaxyos/`.
