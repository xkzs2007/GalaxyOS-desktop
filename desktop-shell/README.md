# GalaxyOS Desktop (Stage 1.5 — TokUI)

> **Status**: stage 1.5 — desktop shell + OpenClaw decoupled + **TokUI**
> streaming UI + ZCode/Codex-style 3-column layout. No MeMo / ACRouter
> yet (those are stages 2 and 3).

## What this is

A standalone desktop application wrapper around the GalaxyOS engine,
decoupled from the OpenClaw plugin runtime. Looks and behaves like
**ZCode / Codex**: a 3-column layout (left sidebar with sessions and
skills, center chat with streaming AI bubbles, right details panel for
R-CCAM trace).

The center column uses **[TokUI](https://tokui.jboltai.com/)** (jboltai
team) as the UI library. The sidecar streams TokUI DSL fragments over
HTTP SSE, and the renderer feeds them into the `TokUI` client, which
parses incrementally and renders as the bytes arrive.

## Layout

```
desktop-shell/
├── package.json                  # Electron + @jboltai/tokui + zeromq + esbuild
├── tsconfig.json                 # TS for main + preload
├── esbuild.config.mjs            # bundles main + preload → dist/
├── src/
│   ├── main.ts                   # Electron main: spawns sidecar, zmq client,
│   │                             #   IPC handlers, **injects TokUI UMD** into renderer
│   └── preload.ts                # contextBridge → window.galaxy.*
├── renderer/
│   ├── index.html                # 3-column ZCode/Codex layout
│   ├── style.css                 # dark theme
│   └── renderer.js               # SSE consumer, TokUI client, mode switcher
├── python/
│   ├── path_resolver_desktop.py  # shim that replaces path_resolver
│   ├── tokui_dsl.py              # GalaxyOS process() → TokUI DSL mapping
│   └── galaxyos_sidecar.py       # pyzmq REP + HTTP SSE (dual transport)
├── scripts/
│   ├── dev.mjs                   # one-shot dev launcher
│   └── build-python.sh           # PyInstaller bundle
├── README.md                     # you are here
└── .gitignore
```

## How it works (one paragraph)

`main.ts` spawns `galaxyos_sidecar.py` as a child process. The sidecar
imports `path_resolver_desktop` *first* — this installs itself as
`sys.modules['path_resolver']`, replacing the OpenClaw-coupled upstream
default with a desktop-friendly one (`~/.galaxyos/` or
`$GALAXYOS_HOME`). Once the shim is in place, the sidecar lazily
imports `XiaoYiClawLLM` and binds two transports:

1. **pyzmq REP** at `tcp://127.0.0.1:5757` for structured RPCs
   (`ask/remember/recall/process/health/quit`)
2. **HTTP SSE** at `http://127.0.0.1:5758/sse/{ask,process,health}` for
   streaming TokUI DSL — each `data: {"tokui": "..."}` is a complete
   fragment the renderer feeds into the TokUI client

When the page loads, `main.ts` reads `@jboltai/tokui/dist/tokui.umd.js`
and injects it via `webContents.executeJavaScript`, exposing
`window.TokUI` to `renderer.js`. The renderer creates a `new TokUI(...)`
instance on the center `#tokui-container` and connects its
`streamAsk`/`streamProcess` helpers to the sidecar's SSE endpoints.
Fragments arrive in 60fps-coalesced batches, fed into
`ui.feed(...)`/`ui.endStream()`, and rendered as proper TokUI bubbles,
think-chains, tool-calls, markdown bodies, and action chips.

## TokUI DSL mapping (Stage 1.5)

`tokui_dsl.process_result_to_fragments()` converts a `XiaoYiClawLLM.process()`
result into a sequence of TokUI fragments:

| GalaxyOS result              | TokUI DSL fragment                                  |
|------------------------------|-----------------------------------------------------|
| (whole response)             | `[bubble role:ai model:Qwen-2.5 time:HH:MM]`       |
| phase retrieval              | `[think-step status:done tt:检索 dur:120ms]`        |
| phase cognition              | `[think-step status:done tt:认知 dur:350ms]`        |
| ... (5 phases total)         | `[/think-chain]`                                   |
| thinking skill               | `[tool-call name:recall status:done duration:—]`    |
| answer text                  | `[md]\n**bold** etc.\n[/md]`                        |
| confidence                   | `[p v:muted]置信度 82%[/p]`                         |
| (close)                      | `[msg-actions copy regenerate like dislike visible]` + `[/bubble]` |
| `[DONE]`                     | (SSE terminator)                                   |

Stage 2 will map MeMo 3-stage progress to `[upd id:step status:running]`
events; Stage 3 will add ACRouter C-A-F phases as `[plan tt:路由决策]`
+ `[plan-step]`.

## Run

Prereqs: Python 3.11+, Node.js 20+.

```bash
cd desktop-shell
npm install
npm run build:main             # esbuild → dist/main.cjs
python -m venv .venv
.venv/Scripts/python -m pip install -r ../requirements-core.txt
.venv/Scripts/python -m pip install pyzmq
npm run dev                    # bundles + launches Electron
```

The first dev launch takes a few minutes for pip + npm. Subsequent
launches are < 5s.

## Verifying the sidecar standalone

```bash
.venv/Scripts/python desktop-shell/python/galaxyos_sidecar.py

# In another shell, hit the SSE endpoint with a raw socket:
python -c "
import socket
s = socket.create_connection(('127.0.0.1', 5758), timeout=8)
s.sendall(b'POST /sse/health HTTP/1.1\r\nHost: x\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
print(b''.join(iter(lambda: s.recv(4096), b'')).decode())
"

# Or /sse/ask with a prompt:
python -c "
import socket
body = b'prompt=hello&session_id=demo'
s = socket.create_connection(('127.0.0.1', 5758), timeout=8)
req = (f'POST /sse/ask HTTP/1.1\r\nHost: x\r\nContent-Type: application/x-www-form-urlencoded\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n').encode() + body
s.sendall(req)
print(b''.join(iter(lambda: s.recv(4096), b'')).decode())
"
```

Expected SSE output (excerpt):

```
HTTP/1.1 200 OK
Content-Type: text/event-stream
...

event: tokui
data: {"tokui": "[bubble role:ai model:Qwen-2.5 time:13:05]"}

event: tokui
data: {"tokui": "[think-chain tt:推理过程]"}

event: tokui
data: {"tokui": "[think-step status:done tt:检索 dur:120ms][p 召回 8 条候选][/think-step]"}

...

event: end
data: [DONE]
```

## Stage 1.5 acceptance

- [x] `python -m pip install -r requirements-core.txt` imports `galaxyos`
- [x] Sidecar boots without OpenClaw
- [x] HTTP `/sse/health` returns JSON
- [x] HTTP `/sse/ask` and `/sse/process` stream TokUI DSL over SSE
- [x] `tokui_dsl.py` has 14/12 unit tests passing
- [x] `path_resolver_desktop.py` has 7/7 unit tests passing
- [x] Renderer mounts TokUI dynamically via `executeJavaScript` injection
- [x] 3-column ZCode/Codex-style layout (sidebar / center / details)
- [ ] Electron end-to-end smoke (needs `npm install` + first launch)
- [ ] Visual verification of the streaming bubble UI (needs first launch)

## What's NOT in Stage 1.5

- **MeMo** (Stage 2): frozen knowledge model + Grounding→Entity→Answer
  as `[think-step status:running]` + `[upd id:step status:done]`
- **Agent-as-a-Router C-A-F loop** (Stage 3): Orchestrator + Verifier + Memory
  as `[plan tt:路由决策]` + `[plan-step status:done]`
- **TokUI handler registration** (Stage 2): the `clk:onClick` / `sub:event`
  patterns work in TokUI but the renderer doesn't yet call
  `TokUI.registerHandler(...)` for `copy / regenerate / like / dislike`
- **Build/packaging**: `electron-builder` + PyInstaller — stub is there
  but not validated

## OpenClaw interop

If `OPENCLAW_HOME` is set and points to a real OpenClaw install
(`extensions/` or `openclaw.json` exists), the shim honours it. The
desktop app then becomes a *front-end* to your existing OpenClaw data
without migration. Otherwise it defaults to `~/.galaxyos/`.

## Architecture (Stage 1.5)

```
┌────────────────────────────────────────────────────────────────────┐
│ GalaxyOS Desktop App (Electron 32)                                 │
│ ┌─────────┐ ┌──────────────────────┐ ┌─────────────────┐            │
│ │Sidebar  │ │ Center (TokUI mount) │ │ Right details   │            │
│ │sessions │ │ <div id=tokui>       │ │ R-CCAM trace    │            │
│ │skills   │ │  + dynamic inject    │ │ MeMo (stage 2)  │            │
│ │health   │ │    of @jboltai/tokui │ │ C-A-F (stage 3) │            │
│ └─────────┘ └──────────────────────┘ └─────────────────┘            │
│              ▲                                                     │
│              │ SSE (text/event-stream)                              │
│              │ http://127.0.0.1:5758/sse/{ask,process}              │
│              ▼                                                     │
│ ┌────────────────────────────────────────────────────────────┐    │
│ │ Python sidecar (galaxyos-sidecar)                          │    │
│ │ - asyncio HTTP server (stdlib; no aiohttp)                 │    │
│ │ - pyzmq REP server (stdlib)                                │    │
│ │ - path_resolver_desktop shim in sys.modules                │    │
│ │ - tokui_dsl.process_result_to_fragments()                  │    │
│ │ - XiaoYiClawLLM (graceful degradation for missing deps)   │    │
│ └────────────────────────────────────────────────────────────┘    │
└────────────────────────────────────────────────────────────────────┘
```
