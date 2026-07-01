# open-webui-honcho

Give **Open WebUI** long-term memory backed by [**Honcho**](https://honcho.dev) — transparent
recall before each turn, passive save after, an explicit model-invokable tool surface, and a
memory-usage policy. Because the memory lives in a Honcho workspace (not in Open WebUI), it is
**shared** with any other Honcho client the user runs (e.g. the Claude Code / Cursor / OpenCode
memory plugin): what Open WebUI learns is available there, and vice-versa.

**Source:** [github.com/GeorgelPreput/open-webui-honcho](https://github.com/GeorgelPreput/open-webui-honcho)
· **Image:** [`georgelpreput/open-webui-honcho`](https://hub.docker.com/r/georgelpreput/open-webui-honcho) on Docker Hub

## How it works

Two components share one Honcho client:

| Component | What it is |
|---|---|
| **`honcho-tools` sidecar** (`src/open_webui_honcho/`) | A small FastAPI service. Owns **all** Honcho SDK calls. Serves 7 model-invokable **OpenAPI tools** and internal `/recall` + `/save` endpoints for the Filter. |
| **`Honcho Memory` Filter** (`functions/honcho_memory_filter.py`) | A single-file Open WebUI **Filter Function** with **no third-party dependencies**. `inlet` recalls relevant memory + injects the policy; `outlet` saves the turn. It only talks to the sidecar over HTTP. |
| **Memory policy** (`prompts/system_prompt.md`) | Injected by the Filter (once per chat). Synced into the Filter by `scripts/build_filter.py`. |

```
Open WebUI (stock image)
  Filter  ── inlet → /internal/recall, outlet → /internal/save ─┐   (whole handler fails open)
  model tools ── honcho_search / honcho_chat / … ───────────────┤   (global OpenAPI tool server)
                                                                 ▼
  honcho-tools sidecar → honcho_service.py (transport-agnostic) → honcho_client.py (Honcho SDK)
                                                                 │
                                                                 ▼
                                       Honcho  (a shared workspace == shared memory)
```

Splitting the work this way keeps the Filter dependency-free (it ships as one `.py` uploaded to
Open WebUI) and puts every SDK call in one container. `honcho_service.py` is deliberately
transport-agnostic, so the same logic could later be exposed over MCP without touching the core.

## The memory contract

The behaviour depends on Honcho's **observation mode**, which is configurable (`unified` by
default):

- **`unified` (default) — self-scoped.** Conclusions live on `peer(subject).conclusions_of(subject)`
  (observer == observed == the subject peer); `chat` / `context` / `representation` run on
  `peer(subject)` with **no target**. This is what makes memory *shared*: every client that reads
  and writes the same subject peer in the same workspace sees the same conclusions. (Using a
  different observer, a name prefix, or a directional scope silently forks the memory.)
- **`directional`.** Conclusions are scoped `(observer=speaker, observed=subject)` and reads pass
  `target=subject`; the speaker must observe the subject in the session. Use this only if you
  want per-observer views rather than one shared representation.

**Recall** is a search-scoped `peer.context(...)` returning a `representation` + `peer_card`,
formatted into a compact `Known about the user` block prepended as a system message.

**Save is messages-only:** the user's message is authored by the **subject** peer (this feeds
recall); the assistant's message is authored by the **speaker** peer (`open_webui`) for
provenance — it is intentionally *not* recalled. Honcho's deriver turns the messages into
conclusions. Peers are registered on the session (subject with `observe_me=true`) **before**
messages are added, so the deriver schedules reasoning.

## Identity & configuration

The sidecar resolves `(workspace, subject_peer, speaker_peer, observation_mode)` per request.
The **subject peer** is chosen by: explicit `X-Honcho-Peer` (or the Filter's `UserValves.peer`)
→ derived from the login email → `HONCHO_DEFAULT_PEER` → `user`. Derivation mode is
`HONCHO_PEER_DERIVATION`:

- `localpart` (default) — the part before `@` (so `alice@example.com` → peer `alice`). Best when
  each person should map to a stable peer that also unifies with their other Honcho clients.
- `email` — the full address, collision-safe across domains for multi-tenant deployments.
- `userid` — the Open WebUI user id.

Open WebUI passes identity to both paths via templated **custom headers**
(`X-Honcho-User-Email: {{USER_EMAIL}}`, `X-Honcho-Chat-Id: {{CHAT_ID}}`).

| Env var | Default | Purpose |
|---|---|---|
| `HONCHO_BASE_URL` | `https://api.honcho.dev` | Honcho instance (cloud or self-hosted) |
| `HONCHO_API_KEY` | — (required) | Honcho API key / workspace-scoped token |
| `HONCHO_DEFAULT_WORKSPACE` | `default` | Workspace the memory lives in |
| `HONCHO_DEFAULT_PEER` | `user` | Fallback subject peer (no name hardcoded) |
| `HONCHO_SPEAKER_PEER` | `open_webui` | Peer that authors assistant messages (provenance) |
| `HONCHO_OBSERVATION_MODE` | `unified` | `unified` or `directional` |
| `HONCHO_PEER_DERIVATION` | `localpart` | `localpart` \| `email` \| `userid` |
| `TOOL_SERVER_API_KEY` | — (blank = no auth) | Bearer the tool server requires |
| `TOOL_SERVER_HOST` / `TOOL_SERVER_PORT` | `0.0.0.0` / `8000` | Bind address |
| `HONCHO_TIMEOUT_SECONDS` | `8.0` | Fail-open timeout for Honcho calls |

The Filter has matching **Valves** (sidecar URL + bearer, defaults, timeouts, policy text) and
**UserValves** (per-user `peer` / `workspace` / `enabled`).

## Quickstart (local)

```bash
uv sync --extra dev
uv run python scripts/build_filter.py --check    # policy is embedded in the Filter
HONCHO_API_KEY=... uv run owui-honcho-tools       # serves on :8000; GET /health
```

## Quality & security

A `Makefile` mirrors CI:

```bash
make lint         # ruff
make format       # ruff format (format-check to verify only)
make typecheck    # mypy
make test         # pytest + coverage
make codeql       # CodeQL database build + analysis (SARIF)
make opengrep     # OpenGrep static analysis (JSON)
make scan         # codeql + opengrep
make all          # everything
```

Lint, type, and test failures fail the build; the security scans are informational (reports land
in `reports/`). A `.pre-commit-config.yaml` runs ruff, ruff-format, mypy, and pip-audit:

```bash
uv run pre-commit install
uv run pre-commit run --all-files
```

## Docker image

The published image is on Docker Hub — **[`georgelpreput/open-webui-honcho`](https://hub.docker.com/r/georgelpreput/open-webui-honcho)**. Pull and run it:

```bash
docker pull georgelpreput/open-webui-honcho
docker run --rm -p 8000:8000 -e HONCHO_API_KEY=... georgelpreput/open-webui-honcho
```

Or build it yourself (`linux/amd64`, pure-Python):

```bash
docker build -f docker/Dockerfile -t open-webui-honcho .
```

**CI:** `.github/workflows/docker.yml` builds, smoke-tests `/health`, and pushes to Docker Hub on
`main` / `v*` tags — via repo secrets `DOCKERHUB_USERNAME` / `DOCKERHUB_TOKEN` and the optional
`IMAGE_NAME` variable (to publish under your own namespace).

## Deploy to Open WebUI

1. **Run the sidecar** — the [`georgelpreput/open-webui-honcho`](https://hub.docker.com/r/georgelpreput/open-webui-honcho)
   image — where Open WebUI's backend can reach it (the simplest option is the same Docker
   network, e.g. reachable as `http://honcho-tools:8000`). Provide its environment (at least
   `HONCHO_API_KEY`, and `HONCHO_BASE_URL` if self-hosting; set `TOOL_SERVER_API_KEY` to require
   a bearer).
2. **Register the tool server** — *Admin Settings → Tools* → add the sidecar URL, type OpenAPI,
   Bearer = `TOOL_SERVER_API_KEY`, and Custom Headers
   `{"X-Honcho-User-Email":"{{USER_EMAIL}}","X-Honcho-Chat-Id":"{{CHAT_ID}}"}`. Registering it
   globally makes requests server-side, so an internal hostname works and CORS does not apply.
3. **Install the Filter** — *Admin Settings → Functions* → upload
   `functions/honcho_memory_filter.py`, set its Valves (`sidecar_base_url`, `sidecar_api_key`),
   and attach it to the model(s) you want memory on.

## Project layout

```
src/open_webui_honcho/
  config.py          Env-driven settings + identity defaults
  identity.py        resolve() + sanitize_peer() + email derivation
  honcho_client.py   All Honcho SDK calls (unified/directional scope)
  honcho_service.py  Transport-agnostic ops (recall/save/tool_*)
  tool_server.py     FastAPI: 7 honcho_* tools + /internal/{recall,save} + /health
functions/honcho_memory_filter.py   Open WebUI Filter (no deps; calls the sidecar)
prompts/system_prompt.md            Memory policy (source of truth)
scripts/build_filter.py             Embeds the policy into the Filter
docker/Dockerfile                   Sidecar image
.github/workflows/docker.yml        Build + smoke + push
tests/                              Unit tests
```

## License

MIT. Honcho is a project of [Plastic Labs](https://plasticlabs.ai).
