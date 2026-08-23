# trilium-notecast-mcp

[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![TriliumNext](https://img.shields.io/badge/TriliumNext-compatible-000000?logo=trilium&logoColor=white)](https://triliumnotes.org)
[![MCP](https://img.shields.io/badge/MCP-server-7c3aed)](https://modelcontextprotocol.io)
[![Python](https://img.shields.io/badge/python-3.11%2B-3776ab?logo=python&logoColor=white)](https://www.python.org)
[![Presenter plugin](https://img.shields.io/badge/Notecast-presenter%20plugin-0a7ea4)](https://github.com/Stefan-Schmidbauer/trilium-presenter-plugin)
[![Render plugin](https://img.shields.io/badge/Notecast-render%20plugin-0a7ea4)](https://github.com/Stefan-Schmidbauer/trilium-notecast-render)

> **Status: 0.x.** The tools and the label contract are in use and tested, but
> nothing here is frozen: a tool signature or a `#notecast*` label may still
> change between releases, and there are no backports. Pin a tag if that matters
> to you. For where the HTTP mode may be run, see
> [Security model](#security-model).

MCP server for [Trilium Notes](https://github.com/TriliumNext/Trilium) — author and manage **typed notes** via the ETAPI. The authoring specialist of the **Notecast** family (see [docs/notecast-contract.md](docs/notecast-contract.md)).

A "type" — `slide`, `expenseReport`, `wikiEntry`, anything — is defined by a Trilium note labelled `#notecastType=<id>` whose content is the authoring format and whose labels carry the mechanics (target note type, mime, labels to stamp, default parent, branch prefix). Adding a type is tagging a note; this server ships **no** type definitions — it is a pure engine.

Each type's format is loaded live from its note and embedded directly into the `create_note` / `update_note` tool descriptions, so it reliably reaches the model on every client. Formats stay in a single place — editable directly in Trilium. Changes take effect on the next connection; if a type is missing, duplicated, or unreachable, the tool emits a loud "do not guess" notice and creates nothing, so a missing format can never masquerade as the real one.

## The Notecast family

This server is the **authoring** specialist of three repositories that read and
write the same Trilium notes, each doing one job:

| Repo | Role |
|---|---|
| **`trilium-notecast-mcp`** (this repo) | **authors typed notes (`#notecastType`) via the ETAPI** |
| [`trilium-presenter-plugin`](https://github.com/Stefan-Schmidbauer/trilium-presenter-plugin) | presents a subtree on screen |
| [`trilium-notecast-render`](https://github.com/Stefan-Schmidbauer/trilium-notecast-render) | renders a note to print/PDF in a chosen theme |

They never call each other. They are coupled only through Trilium, by a handful
of labels — the shared [label contract](docs/notecast-contract.md), which lives
in this repo and is binding on all three.

Each is installed separately: the two plugins as Trilium note imports, this
server alongside your AI assistant. **Types ship with the output plugin that
gives them a visible form** — the presenter owns `slide`, the renderer owns the
document types (`note`, `kbEntry`, `meetingNote`, `checklist`, `itTip`,
`letter`, `handout`). This server authors whatever the instance defines and
nothing more, so a fresh install with neither plugin has no type to write: to try the engine
first, `tools/seed-demo-type.py` tags one throwaway type and removes it again.

## Tools

| Tool | Description |
|---|---|
| `list_note_types` | List the available types (`#notecastType` notes) |
| `create_note` | Create a note of a type (format loaded live into this description); `labels` sets per-note labels |
| `get_note` | Read a note's raw content |
| `update_note` | Update a note's content and/or title |
| `attach_image` | Attach an image to a note and get back the reference to embed |
| `get_note_info` | Get note metadata (title, type, children, attributes) |
| `list_children` | List a note's direct children (tree navigation) |
| `clone_node` | Clone a note into another parent (a branch, not a copy) |
| `move_node` | Move a note to the n-th place among its siblings (0-based index) |
| `delete_note` | Delete a note |
| `search_notes` | Search notes (supports `#label` syntax) |

`create_note` reads the type definition for `note_type`, then creates the note
with the target type / mime / branch prefix it specifies and stamps its
`#notecastApplyLabels`. If `note_type` does not resolve to exactly one
definition it refuses and returns a STOP notice instead of creating anything.

The optional `labels={name: value}` sets labels on that one note, applied after
`#notecastApplyLabels` so a name given there overrides the type's default. It
exists because that default is one fixed value per type, which left a type whose
id does not distinguish its variants unreachable: `slide` has `title`, `content`
and `chapter`, the definition stamps `content`, and no deck authored here could
have a title slide. `#notecastInstance` and `#notecastType` are refused — the
first is how the renderer resolves a note's type, the second would turn the note
into a duplicate type definition and take that type offline.
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
use. With the types the two output plugins ship (eight — seven from the renderer,
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

| | [Local (stdio)](#local-stdio) | [Shared (HTTP)](#shared-http) |
|---|---|---|
| **Use when** | One machine — your laptop runs both Trilium and the MCP client | Several clients or machines share one instance over the network |
| **How it runs** | The MCP client spawns the server as a subprocess | A long-running process serves an HTTP endpoint |
| **Needs** | Python 3.11+ and a virtualenv ([why not 3.10](#why-python-311-and-not-310)) | Docker — or the same virtualenv, if you would rather not run a container |
| **Auth** | None — it is a local subprocess | Bearer token, behind a reverse proxy |

Both need a Trilium ETAPI token: **Trilium → Options → ETAPI → Create new
token**.

Everything below is a manual install, and that is the whole story — there is no
installer and nothing that has to be registered anywhere. The transport is
picked by one environment variable, so the two modes differ in how you start the
same `server.py`, not in what gets installed. `deploy.sh` and the
[Deployment](#deployment) section are a convenience for one specific case — a
container on a *remote* machine, built without a registry — and are safe to skip
entirely: nothing in this section depends on them.

### Local (stdio)

The default mode: no Docker and no network exposure. The server runs from a
virtualenv on the machine hosting the MCP client, which starts and stops it.

#### 1. Install

```bash
git clone https://github.com/Stefan-Schmidbauer/trilium-notecast-mcp.git
cd trilium-notecast-mcp
python3 -m venv venv
venv/bin/pip install --require-hashes -r requirements.txt
```

This creates the `venv/` folder the config below points at. `--require-hashes`
is what the Dockerfile and CI use as well; `requirements.txt` carries hashes, so
pip verifies them either way — passing the flag makes a lockfile that no longer
verifies fail here rather than install quietly.

> **Windows:** use the `venv\Scripts\` paths instead of `venv/bin/` throughout
> this guide — so `venv\Scripts\pip.exe` to install, and `venv\Scripts\python.exe`
> as the `command` in the config below.

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

### Shared (HTTP)

In this mode the server speaks **streamable HTTP**, so remote MCP clients reach
one shared instance instead of each spawning their own — nothing is installed on
the client machines.

> **Before you run this mode, decide where.** Reaching the endpoint means
> reaching the *whole* Trilium instance — Trilium has no user management, so the
> ETAPI token this server holds covers every note, and `MCP_AUTH_TOKEN` is the
> only check in front of it. This mode is designed for an internal network or a
> VPN (Tailscale, WireGuard or equivalent), with the port published on localhost
> and a reverse proxy in front; the bearer token is a second layer, not the
> boundary. **Do not put it on the public internet.** The server refuses to start
> in HTTP mode without `MCP_AUTH_TOKEN` for that reason.
> [Security model](#security-model) has the full picture.

There are three ways to run it, and all three are the *same* server:
`MCP_TRANSPORT=streamable-http` is what switches the transport, and the
`Dockerfile` is nothing more than a container that sets that variable and starts
`server.py`. Pick by how you already run things:

| Variant | Use when |
|---|---|
| **[A — Docker Compose](#variant-a--docker-compose)** | Trilium itself runs in a compose stack. The usual case, and the two containers then share a network |
| **[B — `docker run`](#variant-b--docker-run)** | A single container, no stack — the quickest way to try the HTTP mode |
| **[C — virtualenv, no Docker](#variant-c--virtualenv-no-docker)** | You have no Docker on that host, or manage services yourself (systemd) |

Step 1 applies to all three; step 2 has a section for each. None of them involve
`deploy.sh` — see [Deployment](#deployment) for what that script is actually for.

#### 1. Configure

The server is configured through the same `TRILIUM_*` variables as above, plus:

| Key | Description |
|---|---|
| `MCP_TRANSPORT` | `stdio` (default) or `streamable-http` — the one variable that selects the mode |
| `MCP_HOST` / `MCP_PORT` | Address the server binds to (default `127.0.0.1:8000`; the image sets `0.0.0.0` so the port can be published) |
| `MCP_PATH` | Endpoint path (default `/mcp`) |
| `MCP_AUTH_TOKEN` | Clients must send `Authorization: Bearer <token>`. **Required in HTTP mode** — without it the server refuses to start |
| `MCP_ALLOW_UNAUTHENTICATED` | `1` starts HTTP mode without any bearer check. Only for a deployment that authenticates one layer further out |
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

One consequence catches people out in every variant below: setting
`MCP_ALLOWED_HOSTS` *replaces* the SDK's localhost defaults rather than adding to
them. With only the proxy hostname listed, a `curl` against `127.0.0.1:9151` on
the host — the obvious way to check the endpoint before the proxy is in place —
answers `421` even though nothing is wrong. **Whatever host and port a client
actually connects to has to appear in the list**, which is why the examples below
carry the localhost entries alongside the proxy name; drop them once you no
longer test that way.

`MCP_PATH` is set to `/trilium-notecast-mcp` in the examples to match the path
the reverse proxy mounts the server under; the image's own default is `/mcp`.
Whichever you pick, it has to be identical in the proxy config and in the client
URL.

#### 2. Run it

Every Docker variant needs the image built first, because there is deliberately
no `build:` key anywhere — the image is built once and referenced by tag, which
is what lets the same service definition be deployed to a machine with no source
checkout. The build needs BuildKit: the `Dockerfile` uses `COPY --chmod=644`,
which the legacy builder rejects. Any current Docker with the `buildx` plugin
(`docker buildx version` answers) provides it.

```bash
docker build -t trilium-notecast-mcp:local .
```

Rebuild with the same command after pulling a new version — compose and
`docker run` both reference the `:local` tag, they never build it themselves.

##### Variant A — Docker Compose

The common case: Trilium already runs in a compose stack, and the MCP server
joins it as a second service. Both containers then sit on one network, so the
server reaches Trilium under its **service name** rather than over the host.

[`deploy/docker-compose.snippet.yml`](deploy/docker-compose.snippet.yml) is the
full service definition, with resource limits and a hardened container
(`read_only`, `cap_drop: ALL`, `no-new-privileges`). Paste it into the compose
file that runs Trilium — it is indented to drop straight under `services:` — and
adjust the four things its header comment names: the Trilium service name in
`TRILIUM_URL`, the published port, the hostnames in
`MCP_ALLOWED_HOSTS` / `MCP_ALLOWED_ORIGINS`, and the network. In outline:

```yaml
  trilium-notecast-mcp:
    image: trilium-notecast-mcp:local   # built above; no build: key on purpose
    pull_policy: never                  # local-only image; see below
    restart: always
    # no depends_on: on purpose — see below
    ports:
      - "127.0.0.1:9151:8000"           # localhost only — the proxy exposes it
    env_file:
      - ./.env.trilium-notecast-mcp     # ETAPI + bearer token, never committed
    environment:
      - TRILIUM_URL=http://triliumnext:8080   # service name, not localhost
      - MCP_TRANSPORT=streamable-http
      - MCP_HOST=0.0.0.0
      - MCP_PATH=/trilium-notecast-mcp
      - MCP_ALLOWED_HOSTS=mcp.example.net,127.0.0.1:9151,localhost:9151
    networks:
      - web                             # the network Trilium is already on
```

The secrets go into `.env.trilium-notecast-mcp` next to the compose file —
[`deploy/env.example`](deploy/env.example) is the template, and it is referenced
by `env_file:` rather than written into the compose file so the tokens stay out
of version control. Then:

```bash
docker compose up -d trilium-notecast-mcp
docker compose logs -f trilium-notecast-mcp
```

Four things to get right, all of which fail in confusing ways:

- **`pull_policy: never` is not cosmetic.** The image is built locally — by
  `docker build` here, or by [`deploy.sh`](#deployment) straight onto the server
  — and no registry ever holds it. Without that key, `docker compose pull` fails
  on this service with *"pull access denied"*, and because compose treats that as
  fatal, the whole stack's pull is aborted along with it.
- **The network must be one the stack already declares**, and the same one
  Trilium is on — otherwise compose fails with *"network web not found"*, or the
  service name in `TRILIUM_URL` does not resolve. If the stack uses only its
  implicit default network, drop the `networks:` key entirely.
- **There is deliberately no `depends_on`.** It would only order the start, not
  wait for readiness, and this server does not touch Trilium until its first
  tool call — so it buys nothing. What it costs is that `docker compose up -d
  trilium-notecast-mcp` then pulls Trilium up with it and **recreates** it if
  its configuration has drifted from what is running, which is how a deploy of
  this service alone can restart Trilium and carry it across a database
  migration. `deploy.sh` passes `--no-deps` as well, because the compose file on
  the server is a copy nothing keeps in sync with the snippet.
- **`MCP_HOST=0.0.0.0` belongs here**, unlike in variant C: the process must
  listen on the container's external interface for the published port to reach
  it. Confining the exposure is the job of the `127.0.0.1:` prefix in `ports:`.

##### Variant B — `docker run`

A single container, without a stack — the quickest way to try the HTTP mode:

```bash
docker run -d --name trilium-notecast-mcp \
  --network your-trilium-network \
  -p 127.0.0.1:9151:8000 \
  -e TRILIUM_URL=http://triliumnext:8080 \
  -e TRILIUM_API_KEY=your-etapi-token \
  -e MCP_AUTH_TOKEN=your-bearer-token \
  -e MCP_PATH=/trilium-notecast-mcp \
  -e MCP_ALLOWED_HOSTS=mcp.example.net,127.0.0.1:9151,localhost:9151 \
  trilium-notecast-mcp:local
```

**`--network` is not optional if `TRILIUM_URL` names a container.** Without it
the container lands on the default bridge, where a service name does not resolve
— and the failure is quiet: the server starts, `/healthz` answers `ok`, the
`HEALTHCHECK` reports healthy and an MCP `initialize` succeeds, because none of
those touch Trilium. Only the first real tool call fails. `docker network ls`
shows the network your Trilium stack created (typically `<stack>_web` or
`<stack>_default`). If Trilium instead runs on the host, use
`-e TRILIUM_URL=http://host.docker.internal:8080` with
`--add-host=host.docker.internal:host-gateway` and no `--network`.

##### Variant C — virtualenv, no Docker

The HTTP transport is not tied to the container: `uvicorn` is one of the three
direct dependencies, so the virtualenv from [Local (stdio)](#local-stdio) already
has everything. Install exactly as in that section, then start `server.py` with
the transport variable set instead of letting a client spawn it:

```bash
MCP_TRANSPORT=streamable-http \
MCP_HOST=127.0.0.1 MCP_PORT=9151 MCP_PATH=/trilium-notecast-mcp \
TRILIUM_URL=http://localhost:8080 \
TRILIUM_API_KEY=your-etapi-token \
MCP_AUTH_TOKEN=your-bearer-token \
  venv/bin/python server.py
```

The endpoint behaves identically to the container — same bearer check, same
`/healthz`, same DNS-rebinding rules. Two differences are worth knowing:

- **Binding.** The image sets `MCP_HOST=0.0.0.0` because a container publishes
  its port outward. Running on the host directly, keep the `127.0.0.1` default
  and let the reverse proxy do the exposing — `0.0.0.0` here puts the endpoint on
  every interface of the machine.
- **Nothing restarts it.** The container brings `restart: always` and a
  `HEALTHCHECK` that together recover a server which is up but no longer
  answering. Run it from the virtualenv and that is your job — a systemd unit
  with `Restart=always` is the usual answer, and it can probe the same
  `GET /healthz` the image uses.

#### 3. Expose it, whichever variant you picked

`GET /healthz` answers `ok` without authentication, whether or not
`MCP_AUTH_TOKEN` is set — it is handled by its own middleware layer outside both
the bearer check and the host check. That is what the image's `HEALTHCHECK` uses
(so `restart: always` also recovers a server that is up but has stopped
answering), and it is why a container can report *healthy* while every MCP
request is being rejected — a green health status says the process is alive, not
that the endpoint is reachable or that Trilium is.

The MCP endpoint has no transport-level encryption of its own, so publish the
port on localhost only and put a reverse proxy in front. With Tailscale, a path
mount keeps an existing service on `/` untouched:

```bash
tailscale serve --bg --set-path=/trilium-notecast-mcp http://127.0.0.1:9151/trilium-notecast-mcp
```

The path is the one the server serves under (`MCP_PATH`), and the hostname the
proxy answers on must appear in `MCP_ALLOWED_HOSTS` — otherwise every proxied
request comes back as `421 Invalid Host header`.

Reaching the endpoint means full access to the Trilium instance, not just to
presentations: Trilium has no user management, so an ETAPI token covers every
note, and `MCP_AUTH_TOKEN` is the only check in front of it. See
[Security model](#security-model) for what that implies for where to run it.

#### 4. Register the endpoint in your MCP client

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

Because of that weight, serving HTTP without one is refused rather than warned
about: `MCP_TRANSPORT=streamable-http` with no `MCP_AUTH_TOKEN` exits before
anything binds a port. `MCP_ALLOW_UNAUTHENTICATED=1` is the deliberate opt-out
for a deployment where something in front does the authentication — it is a
statement that the endpoint is unauthenticated on purpose, not a fallback to
reach for when the token is inconvenient. Neither setting substitutes for the
network boundary below.

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
| `test_auth.py` | the bearer check rejects a missing, wrong, prefix-matching or non-UTF-8 header; HTTP mode refuses to start without a token unless the opt-out is set; `/healthz` answers with and without a configured token |
| `test_tools.py` | the live format reaches `create_note` / `update_note`, and the middleware really is in the request chain — verified end to end through a client session |
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
| `mcp` | The SDK the whole tool surface is built on. The live-format injection is a `ServerMiddleware` on `tools/list` — supported API, but the shape of that payload is what the injection edits |
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

`mcp` is the one to watch. `tests/test_tools.py` covers the injection, including
`test_the_block_reaches_a_real_client`, which drives a real client session: a
middleware that stops being called injects nothing while every tool still lists
correctly, and that test is what turns the silent case into a failing one. Beyond
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

**Optional convenience — skip it unless this is your case.** It covers one
narrow situation: [variant A](#variant-a--docker-compose) where the stack runs on
a *different* machine than the one you develop on, and you would rather not push
the image through a registry or keep a source checkout on the server. Everything
in [Setup](#setup) works without this section; `deploy.sh` adds no capability,
only convenience.

What it automates is the tedious part of that one case: create a Docker context
over SSH, build the image on the *remote* daemon — the build context is streamed
across that connection, kept small by `.dockerignore` — then `docker compose up
-d` and poll `/healthz`. Each of those is a command you can run yourself. That
remote build is the whole point: it is what makes a source checkout on the server
unnecessary.

**Set the server side up first.** `deploy.sh` runs `docker compose up -d` against
a compose file it expects to be there already — it never creates one, and it
never deploys secrets. Both are exactly the files from
[variant A](#variant-a--docker-compose), placed on the server by hand; see
[Server-side setup](#server-side-setup) for the ownership rationale. The order
for a first deployment is:

1. Compose snippet and `.env.trilium-notecast-mcp` on the server, once, by hand
2. `deploy.env` on the dev machine (below)
3. `./deploy.sh`
4. The reverse proxy ([step 3 of the setup](#3-expose-it-whichever-variant-you-picked))

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

Nothing changes about *what* goes on the server — it is the same two files as in
[variant A](#variant-a--docker-compose), from the same templates:

| Template | Goes to | Purpose |
|---|---|---|
| `deploy/docker-compose.snippet.yml` | pasted into the compose file that runs Trilium | service definition |
| `deploy/env.example` | next to that compose file, as `.env.trilium-notecast-mcp` | ETAPI + bearer token |

What is specific to deploying remotely is that `deploy.sh` deliberately does
*not* deploy them. They are templates, placed by hand, so that the secrets never
leave the server and the compose file stays under the server admin's control —
the deploy only replaces the image and recreates the container. The practical
consequence: the admin can operate the service (`up -d`, `restart`, `logs`)
without this repository, and no secret ever sits on a dev machine. The cost is
that `deploy/` holds *copies* that nothing keeps in sync — update them when the
service definition changes.

One consequence is easy to miss when the stack is maintained by hand: the image
this deploy produces exists **only** in the server's local image store, so the
service definition has to carry `pull_policy: never` — it is in the snippet, and
it needs to survive into the compose file the snippet was pasted into. Otherwise
the admin's routine `docker compose pull` fails on this service and takes the
pull for every other service in the stack down with it. `deploy.sh` itself is
unaffected either way: it only runs `up -d`, which is content with a local
image.

The reverse proxy is set up the same way as for any other variant — see
[step 3](#3-expose-it-whichever-variant-you-picked).

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
