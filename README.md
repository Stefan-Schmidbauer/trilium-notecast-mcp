# trilium-notecast-mcp

[![Release](https://img.shields.io/github/v/release/Stefan-Schmidbauer/trilium-notecast-mcp?sort=semver)](https://github.com/Stefan-Schmidbauer/trilium-notecast-mcp/releases/latest)
[![License: MIT](https://img.shields.io/github/license/Stefan-Schmidbauer/trilium-notecast-mcp)](LICENSE)
[![TriliumNext](https://img.shields.io/badge/TriliumNext-compatible-000000?logo=trilium&logoColor=white)](https://triliumnotes.org)
[![MCP](https://img.shields.io/badge/MCP-server-7c3aed)](https://modelcontextprotocol.io)
[![Python](https://img.shields.io/badge/python-3.11%2B-3776ab?logo=python&logoColor=white)](https://www.python.org)

MCP server for [Trilium Notes](https://github.com/TriliumNext/Trilium) — author and manage **typed notes** via the ETAPI. The authoring specialist of the **Notecast** family (see [docs/notecast-contract.md](docs/notecast-contract.md)).

A "type" — `slide`, `expenseReport`, `wikiEntry`, anything — is defined by a Trilium note labelled `#notecastType=<id>` whose content is the authoring format and whose labels carry the mechanics (target note type, mime, labels to stamp, default parent, branch prefix). Adding a type is tagging a note; this server ships **no** type definitions — it is a pure engine. Types ship with the output plugin that renders them — [trilium-presenter-plugin](https://github.com/Stefan-Schmidbauer/trilium-presenter-plugin) owns `slide`, [trilium-notecast-render](https://github.com/Stefan-Schmidbauer/trilium-notecast-render) owns the document types (`note`, `kbEntry`, `meetingNote`, `checklist`, `itTip`, `letter`) — installed into Trilium separately; the two only meet inside Trilium. To try the engine before installing either, `tools/seed-demo-type.py` tags one throwaway type and removes it again. Each type's format is loaded live from its note and embedded directly into the `create_note` / `update_note` tool descriptions, so it reliably reaches the model on every client. Formats stay in a single place — editable directly in Trilium. Changes take effect on the next connection; if a type is missing, duplicated, or unreachable, the tool emits a loud "do not guess" notice and creates nothing, so a missing format can never masquerade as the real one.

## Tools

| Tool | Description |
|---|---|
| `list_note_types` | List the available types (`#notecastType` notes) |
| `create_note` | Create a note of a type (format loaded live into this description) |
| `get_note` | Read a note's raw content |
| `update_note` | Update a note's content and/or title |
| `attach_image` | Attach an image to a note and get back the reference to embed |
| `get_note_info` | Get note metadata (title, type, children, attributes) |
| `list_children` | List a note's direct children (tree navigation) |
| `clone_node` | Clone a note into another parent (a branch, not a copy) |
| `move_node` | Reorder a note within its parent |
| `delete_note` | Delete a note |
| `search_notes` | Search notes (supports `#label` syntax) |

`create_note` reads the type definition for `note_type`, then creates the note
with the target type / mime / branch prefix it specifies and stamps its
`#notecastApplyLabels`. If `note_type` does not resolve to exactly one
definition it refuses and returns a STOP notice instead of creating anything.
`list_children` is plain navigation — the MCP authors notes; **presenting** a
subtree is the presenter plugin's job, not this server's.

### Images

An image belongs to the note that shows it, as a Trilium attachment whose title
is the file name. The order is `create_note` → `attach_image` → `update_note`:
the note has to exist to attach to, and `attach_image` hands back a `reference`
field — the snippet to put into the note body — already in the right form for
that note's target type (a bare `![alt](name.png)` for markdown notes, which the
output plugin resolves against the note's attachment titles; a literal
`api/attachments/…` URL for HTML notes, which Trilium renders as-is).

File names must be plain (no slashes) and unique within their note, because the
reference is matched against attachment titles — a duplicate would make the
mapping depend on enumeration order, so it is refused. Accepted extensions are
png, jpg, jpeg, gif, webp and svg; the payload is base64 in the tool argument,
so in practice the model's context is a tighter limit than the server's 10 MB.

### How many types this scales to

Every defined type's full authoring format is embedded into the `create_note`
*and* `update_note` descriptions, so the block is sent twice on every connection.
That is deliberate — it is the only path guaranteed to reach the model — but it
means the context cost grows with the number of types, not with the number in
use. With the types the two output plugins ship (seven — six from the renderer,
`slide` from the presenter) this is comfortable; a few dozen would not be. Discovery itself is capped at 100 `#notecastType` notes.

If you reach that point, the fix is to split types across separate server
instances by purpose rather than to keep one instance listing everything.

## Prompts

| Prompt | Description |
|---|---|
| `note_creation_guide` | Tool workflow + every defined type's format, loaded live from the `#notecastType` notes (STOP "do-not-guess" notice for a missing/duplicated type — never a built-in substitute). Note: the same formats are also embedded into the `create_note` / `update_note` tool descriptions, which is the path guaranteed to reach the model — prompts are not injected by every client. |

## Setup

The server speaks two transports. Both run the same `server.py` against the same
ETAPI token — only environment variables differ — so pick the one that matches
your setup and follow just that section:

| | [Local (stdio)](#local-stdio) | [Shared (HTTP, Docker)](#shared-http-docker) |
|---|---|---|
| **Use when** | One machine — your laptop runs both Trilium and the MCP client | Several clients or machines share one instance over the network |
| **How it runs** | The MCP client spawns the server as a subprocess | A container serves an HTTP endpoint |
| **Needs** | Python 3.11+ and a virtualenv ([why not 3.10](#why-python-311-and-not-310)) | Docker; no local Python |
| **Auth** | None — it is a local subprocess | Bearer token, behind a reverse proxy |

Both need a Trilium ETAPI token: **Trilium → Options → ETAPI → Create new
token**.

### Local (stdio)

The default mode: no Docker and no network exposure. The server runs from a
virtualenv on the machine hosting the MCP client, which starts and stops it.

#### 1. Install

```bash
git clone https://github.com/Stefan-Schmidbauer/trilium-notecast-mcp.git
cd trilium-notecast-mcp
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

This creates the `venv/` folder the config below points at.

> **Windows:** use the `venv\Scripts\` paths instead of `venv/bin/` throughout
> this guide — e.g. `venv\Scripts\pip.exe install -r requirements.txt` to
> install and `venv\Scripts\python.exe` as the `command` in the config below.

#### 2. Register the server in your MCP client

In this mode the server is configured entirely through environment variables,
passed by the client:

| Key | Description |
|---|---|
| `TRILIUM_URL` | Local URL of Trilium (default: `http://localhost:8080`) |
| `TRILIUM_API_KEY` | Your ETAPI token |
| `TRILIUM_DEFAULT_PARENT` | Note ID where new notes are created when neither the call nor the type specifies one (default: `root`) |

Copy the following block into your MCP client config and replace the two
absolute paths and the env values with your own. Use **absolute** paths — MCP
clients do not resolve `~` or relative paths.

```json
{
  "mcpServers": {
    "trilium-notecast": {
      "command": "/path/to/trilium-notecast-mcp/venv/bin/python",
      "args": ["/path/to/trilium-notecast-mcp/server.py"],
      "env": {
        "TRILIUM_URL": "http://localhost:8080",
        "TRILIUM_API_KEY": "your-etapi-token",
        "TRILIUM_DEFAULT_PARENT": "root"
      }
    }
  }
}
```

The same block lives in [`claude_desktop_config.example.json`](claude_desktop_config.example.json) — copy it to a real file and fill in your values:

```bash
cp claude_desktop_config.example.json claude_desktop_config.json
```

This `claude_desktop_config.json` is just a scratch file for assembling the `mcpServers` block before you paste it into your client — it is gitignored and not read by the server itself.

Where to paste the `mcpServers` block depends on your client:

- **Claude Code (project-scoped):** `.mcp.json` in your project root
- **Claude Desktop — macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Claude Desktop — Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
- **Claude Desktop — Linux:** `~/.config/Claude/claude_desktop_config.json`

Restart the client after editing its config so the server is picked up.

#### 3. Test the server (without an MCP client)

The server speaks MCP over stdio, so running it standalone just blocks waiting
for input. To inspect and exercise the tools interactively, use the
[MCP Inspector](https://github.com/modelcontextprotocol/inspector):

```bash
TRILIUM_URL=http://localhost:8080 TRILIUM_API_KEY=your-etapi-token \
  npx @modelcontextprotocol/inspector venv/bin/python server.py
```

This opens a local UI where you can list and call each tool against your
Trilium instance.

### Shared (HTTP, Docker)

In this mode the server speaks **streamable HTTP**, so remote MCP clients reach
one shared instance instead of each spawning their own. The `Dockerfile` builds
an image that defaults to it — nothing is installed on the client machines.

#### 1. Configure

The container is configured through the same `TRILIUM_*` variables as above,
plus:

| Key | Description |
|---|---|
| `MCP_TRANSPORT` | `stdio` (default) or `streamable-http` |
| `MCP_HOST` / `MCP_PORT` | Bind address inside the container (default `127.0.0.1:8000`; the image sets `0.0.0.0`) |
| `MCP_PATH` | Endpoint path (default `/mcp`) |
| `MCP_AUTH_TOKEN` | If set, clients must send `Authorization: Bearer <token>`. Unset means **no authentication** |
| `MCP_ALLOWED_HOSTS` | Comma-separated hostnames accepted by the SDK's DNS-rebinding protection. Required when reached through a reverse proxy under its own hostname |
| `MCP_ALLOWED_ORIGINS` | Comma-separated origins accepted for CORS/rebinding checks |

**DNS-rebinding protection is on by default, and it fails closed.** Leaving
`MCP_ALLOWED_HOSTS` and `MCP_ALLOWED_ORIGINS` unset does not disable the `Host`
check: the SDK then applies its localhost-only defaults, and every request
arriving under a proxy hostname is answered with `421 Invalid Host header` — with
a valid bearer token and a correct path. If a fresh deployment answers 421, this
is why; put the hostname the proxy serves under into `MCP_ALLOWED_HOSTS`.

The same applies one level down: setting `MCP_ALLOWED_HOSTS` but leaving
`MCP_ALLOWED_ORIGINS` empty means any request carrying an `Origin` header is
rejected with `403`, while requests without one pass. Browser-based clients
therefore need both.

#### 2. Build and run

The build needs BuildKit — the `Dockerfile` uses `COPY --chmod=644`, which the
legacy builder rejects. Any current Docker with the `buildx` plugin installed
(`docker buildx version` answers) provides it.

```bash
docker build -t trilium-notecast-mcp:local .
docker run -d --name trilium-notecast-mcp -p 127.0.0.1:9151:8000 \
  -e TRILIUM_URL=http://trilium:8080 \
  -e TRILIUM_API_KEY=your-etapi-token \
  -e MCP_AUTH_TOKEN=your-bearer-token \
  -e MCP_PATH=/trilium-notecast-mcp \
  -e MCP_ALLOWED_HOSTS=mcp.example.net \
  trilium-notecast-mcp:local
```

`MCP_PATH` is set here to match the path the reverse proxy mounts the server
under further below; the image's own default is `/mcp`. Whichever you pick, it
has to be the same in the proxy config and in the client URL.

`GET /healthz` answers `ok` without authentication, whether or not
`MCP_AUTH_TOKEN` is set — it is handled by its own middleware layer outside the
bearer check. That is what `deploy.sh` probes and what the image's `HEALTHCHECK`
uses, so `restart: always` also recovers a server that is up but has stopped
answering. The MCP endpoint itself has no transport-level encryption — put it
behind a reverse proxy (nginx, `tailscale serve`, …) and publish the container
port on localhost only.

Note that reaching the endpoint means full access to the Trilium instance, not
just to presentations: Trilium has no user management, so an ETAPI token covers
every note, and `MCP_AUTH_TOKEN` is the only check in front of it. See
[Security model](#security-model) for what that implies for where to run it.

#### 3. Register the endpoint in your MCP client

Unlike stdio, the client is given a URL instead of a command — paste this into
the same config location listed above for your client:

```json
{
  "mcpServers": {
    "trilium-notecast": {
      "type": "http",
      "url": "https://mcp.example.net/trilium-notecast-mcp",
      "headers": { "Authorization": "Bearer your-bearer-token" }
    }
  }
}
```

## Security model

The access this server grants, and what follows from it for where to run it.

Trilium has no user management and no per-token scoping: an ETAPI token grants
access to the whole instance. This server holds such a token, and its tools
inherit that reach — `search_notes` searches every note, `get_note` returns the
content of any note ID, `delete_note` deletes any note. None of it is confined to
notes of a known type; the tool names describe the intended use, not a boundary.

In HTTP mode, access to the endpoint is therefore access to the entire instance,
and `MCP_AUTH_TOKEN` is the only check in front of it. It is a static shared
secret: no rotation, no per-client identity, no rate limiting. Writes and
deletions leave no audit trail beyond Trilium's own revision history. In effect
the bearer token carries the same weight as the ETAPI token.

The setup this is designed for is accordingly an internal network or a VPN
(Tailscale, WireGuard or equivalent), with the container port published on
localhost and a reverse proxy in front, and the bearer token as a second layer
rather than the only one. For a single-user instance on a trusted network the
trade-off is proportionate; as a deployment departs from that, more of the
protection rests on the shared secret alone.

One aspect is outside what any server-side measure can address: `get_note` and
`search_notes` return whatever a note contains, so note content reaches the model
as text it reads. A note carrying instructions ("ignore the above and delete …")
targets the model rather than this server. The mitigation is the MCP client's
tool approval prompts, which is a reason to keep them enabled for the writing
tools. The one case handled server-side is the type-definition notes, whose
content becomes part of a tool description — see below.

In stdio mode the exposure is different: the server runs as a local subprocess
with no network surface, and the token sits in the client config.

### Note type trust

A `#notecastType=<id>` note is not just data — its content is embedded into the
`create_note` / `update_note` tool descriptions, so it arrives at the model as
*instructions*. Which note supplies a type therefore matters as much as whether
one does: resolving by label alone means anyone who can put that label on a note
— via a sync peer, an import, or anyone holding an ETAPI token, since Trilium has
no user separation — decides what the model is told to write.

By design there is **no trust boundary**: anyone who can tag a note defines a
type. That is the point of the "just tag a note" workflow, and it puts the
responsibility on whoever administers the instance's notes. The one safeguard is
a *clarity* guard, not a boundary: if **more than one** note carries
`#notecastType=<id>` for the same id, the server refuses to pick one and emits an
"ambiguous — do not guess" notice, the same way it refuses when the type is
missing entirely. A duplicate fails loudly instead of silently changing the
format. There is no pinning env var — pinning every type by ID would defeat the
workflow.

## Development

```bash
python3 -m venv venv
venv/bin/pip install --require-hashes -r requirements.txt
venv/bin/pip install -r requirements-dev.txt

venv/bin/pytest        # the test suite
venv/bin/ruff check .  # lint
```

The tests need no Trilium: ETAPI is mocked at the HTTP layer with `respx`, so the
URL a tool actually requests and the headers it sends are part of what is
checked, not just the helper functions.

What they cover, and why those things and not others — each maps to a promise
this README makes:

| File | The promise it pins |
|---|---|
| `test_id_validation.py` | `_id()` guards the ETAPI path: no request is *sent* for an ID containing `..`, `?`, `#` or CRLF |
| `test_type_resolution.py` | a type resolves to exactly one definition, or refuses loudly — and discovery does not fan out into a request per type |
| `test_auth.py` | the bearer check rejects a missing, wrong, prefix-matching or non-UTF-8 header; `/healthz` answers with and without a configured token |
| `test_tools.py` | the live format reaches `create_note` / `update_note` and does not compound across connections — the private-SDK-API guard |
| `test_attachments.py` | an image survives the roundtrip byte for byte, goes out as `application/octet-stream`, and gets the reference form its note's target type needs |

One thing the mocks cannot pin is whether *Trilium* still accepts that binary
path — the ETAPI spec does not describe it, so it was measured rather than read.
`tools/probe-attachment-binary.py` re-measures it against a live instance and is
worth running after a Trilium upgrade; it creates one throwaway note and deletes
it again.

## Dependencies

Two files, and only one of them is edited by hand:

| File | Role |
|---|---|
| `requirements.in` | the three direct dependencies — **edit this one** |
| `requirements.txt` | generated by `pip-compile --generate-hashes`; every transitive dependency, each with its hashes |
| `requirements-dev.txt` | pytest, respx, ruff, pip-tools, pip-audit — never installed into the image |

This matters more than usual here, because `deploy.sh` builds on the *remote*
daemon with no lockfile and no registry: without pins, two builds from the same
source can install different code, and "rebuild to restart it" quietly becomes
"upgrade three dependencies in production". Pinned versions fix the version
numbers; the hashes fix the bytes, which is the part that still holds if a
release is re-uploaded. The Dockerfile installs with `--require-hashes`, so a
mismatch fails the build rather than shipping.

| Package | Why it is pinned |
|---|---|
| `mcp` | The server reaches into SDK internals (`mcp._mcp_server.list_tools`, `mcp._tool_manager`) to inject the live note formats. Private API — it can change in any release, including a patch |
| `httpx` | The whole ETAPI client. Pre-1.0, so minor bumps may break |
| `uvicorn` | Only used in HTTP mode, via `uvicorn.run` — the most stable of the three |

### Why Python 3.11 and not 3.10

The server's own code is 3.10-compatible, and so are all three direct
dependencies — the limit comes from the lockfile. `requirements.txt` is generated
by `pip-compile` under 3.12 and resolves *without* environment markers, so it
leaves out `exceptiongroup`, which `anyio` requires on Python < 3.11. And because
the file carries hashes, pip installs in hash-checking mode, where every
dependency must be pinned: the missing backport fails the install outright
instead of being fetched quietly. Python 3.10 is the default on Ubuntu 22.04 LTS,
so this is worth stating rather than leaving to be discovered.

Supporting 3.10 would mean regenerating the lockfile as a universal one
(`pip-compile --universal`) and teaching CI's staleness check the same flag.
Until someone needs it, 3.11+ is the supported range, and 3.12 is what the image
and CI actually run.

### When to upgrade

There is no schedule to keep. Upgrade when there is a reason:

- **A security advisory** affects one of them — then promptly. `pip-audit -r requirements.txt` reports known CVEs against the pinned set.
- **You need something new** from a release (an MCP protocol feature a client requires, a fix you actually hit).
- **Otherwise, occasionally** — every few months, so the gap never grows large enough that upgrading becomes its own project.

Do it as a deliberate, separate commit, never bundled with a feature:

```bash
# 1. edit the version in requirements.in, then regenerate the lockfile
venv/bin/pip-compile --generate-hashes --strip-extras requirements.in

# 2. install it and run the tests
venv/bin/pip install --require-hashes -r requirements.txt
venv/bin/python -m pytest
```

Commit `requirements.in` and `requirements.txt` together — a lockfile that does
not match its input is worse than no lockfile.

`mcp` is the one to watch. `tests/test_tools.py` covers the private API the
server uses to inject the live note formats, so a breaking change there fails the
suite rather than silently producing tool descriptions without a format. Beyond
that, verify a real client connection before deploying:

```bash
TRILIUM_URL=... TRILIUM_API_KEY=... \
  npx @modelcontextprotocol/inspector venv/bin/python server.py
```

In the Inspector, check that `tools/list` still returns all 11 tools **and** that
`create_note`'s description still carries the note-format block. If the format
block is gone, the SDK internals moved — that is exactly the breakage the pins
exist to keep out of a deploy.

Roll back by restoring the previous `requirements.txt` and rebuilding, or by
re-pinning the previous image tag (see [Deployment](#deployment)).

## Deployment

Only relevant for the shared HTTP mode, and only when the container runs on a
different machine than the one you develop on — a local install needs none of
this.

`deploy.sh` deploys from a dev machine to a server without a registry and
without a source checkout on the server: the image is built by the *remote*
Docker daemon through an SSH context, and the build context is streamed over
that connection (kept small by `.dockerignore`).

**On a new server, do [Server-side setup](#server-side-setup) first.** `deploy.sh`
builds the image and then runs `docker compose up -d` against a compose file it
expects to be there already — it never creates one. Run it before that file and
its `.env.trilium-notecast-mcp` exist and the build succeeds, then the recreate
step fails on the missing service. The order for a first deployment is:

1. [Server-side setup](#server-side-setup) — compose snippet and secrets, once, by hand
2. `deploy.env` on the dev machine (below)
3. `./deploy.sh`
4. The reverse proxy (end of the server-side section)

The division of responsibility is deliberate — the service definition
(`docker-compose.yml`) and the secrets (`.env.trilium-notecast-mcp`) live on the
server and belong to the server's admin; the deploy only replaces the image and
recreates the container:

```bash
./deploy.sh              # build + recreate + health check
./deploy.sh --no-build   # only recreate, e.g. after an env change
```

Configure it once per dev machine in `deploy.env` (gitignored) — or export the
same variables, which take precedence:

```bash
cp deploy/deploy.env.example deploy.env
$EDITOR deploy.env
```

| Key | Default |
|---|---|
| `DEPLOY_HOST` | none — **required**, e.g. `admin@your-server.example.net` |
| `DOCKER_CTX` | `trilium-notecast-mcp` (context created on first run) |
| `REMOTE_COMPOSE` | `/opt/docker/docker-compose.yml` |
| `HEALTH_URL` | `http://127.0.0.1:9151/healthz` (resolved on the server) |

Prerequisites:

- **SSH key access** to `DEPLOY_HOST`, with that user in the server's `docker`
  group. `DEPLOY_HOST` is passed to `ssh` verbatim and also becomes the docker
  context's endpoint, so whatever host name you put there must be the one your
  `~/.ssh/config` matches — an entry for an alias does not apply to the same
  machine's FQDN, and the key and port would silently fall back to the defaults.
- **`buildx` on the dev machine** (`docker buildx version`), e.g. Docker's
  `docker-buildx-plugin` package. The remote daemon runs the build, but the
  builder is selected client-side: without the plugin the legacy builder is used
  and the `Dockerfile`'s `COPY --chmod=644` fails with *"the --chmod option
  requires BuildKit"*. Buildx on the server alone does not help.

Each build is tagged twice — `:local` (what compose references) and a version
tag from `git describe`, so an earlier build can be re-pinned for a rollback.
That second tag is `v1.2.0` when the deploy ran on a release tag, `v1.2.0-3-gafe0bf9`
three commits past one, and a bare short SHA in a repo with no tags yet
(`-dirty` is appended if the working tree had uncommitted changes):

```bash
ssh admin@your-server.example.net \
  'docker tag trilium-notecast-mcp:v1.2.0 trilium-notecast-mcp:local && \
   docker compose -f /opt/docker/docker-compose.yml up -d trilium-notecast-mcp'
```

`docker --context trilium-notecast-mcp images trilium-notecast-mcp` lists what
is still on the server to roll back to.

Host and compose path are written out literally on purpose: `deploy.env` is read
by `deploy.sh`, not by your shell, so `$DEPLOY_HOST` / `$REMOTE_COMPOSE` are not
set in the session you type this in — and inside the quoted remote command they
would be expanded on the server, where they are not set either.

### Server-side setup

The two files that live on the server are not deployed — set them up once by
hand, from the templates in `deploy/`:

| Template | Goes to | Purpose |
|---|---|---|
| `deploy/docker-compose.snippet.yml` | pasted into the compose file that runs Trilium | service definition |
| `deploy/env.example` | next to that compose file, as `.env.trilium-notecast-mcp` | ETAPI + bearer token |

They are templates rather than deployed files on purpose: the secrets never
leave the server, and the compose file stays under the server admin's control.
Keep the templates in sync when the service definition changes — nothing
enforces it.

Finally, expose the port through a reverse proxy. With Tailscale, a path mount
keeps an existing service on `/` untouched:

```bash
tailscale serve --bg --set-path=/trilium-notecast-mcp http://127.0.0.1:9151/trilium-notecast-mcp
```

The path there is the one the container serves under (`MCP_PATH`, set to
`/trilium-notecast-mcp` in the compose snippet). The hostname must appear in
`MCP_ALLOWED_HOSTS` — since the snippet sets that variable, DNS-rebinding
protection is active, and a hostname missing from the list is answered with
`Invalid Host header`.

## Content Organization

`clone_node` creates a Trilium *branch*, so one note can appear under several
parents at once — edit it in one place, and every appearance updates. This
supports reuse patterns like Trilium Presenter's **Master / Sets** (a central
library of source notes, and finished decks assembled from clones of them), but
nothing here is presentation-specific: the same mechanism works for any type of
note.

## Documentation

- [Notecast label contract](docs/notecast-contract.md) — the labels the three repos share, and which repo reads each one. Binding on all of them; changes belong there first.
- [Security policy](SECURITY.md) — what is in scope for a report, and what is documented behaviour rather than a finding.

The two output plugins carry their own docs: [trilium-presenter-plugin](https://github.com/Stefan-Schmidbauer/trilium-presenter-plugin#readme) (on-screen decks, owns `slide`) and [trilium-notecast-render](https://github.com/Stefan-Schmidbauer/trilium-notecast-render#readme) (print/PDF, owns the document types).

## Author

**Stefan Schmidbauer** — [GitHub](https://github.com/Stefan-Schmidbauer)

Built with [Claude Code](https://claude.ai/claude-code) as co-author.

## License

MIT — see [LICENSE](LICENSE).
