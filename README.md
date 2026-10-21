# Trilium Notecast MCP

[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-3776ab?logo=python&logoColor=white)](https://www.python.org)
[![MCP](https://img.shields.io/badge/MCP-server-7c3aed)](https://modelcontextprotocol.io)
[![TriliumNext](https://img.shields.io/badge/TriliumNext-compatible-000000?logo=trilium&logoColor=white)](https://triliumnotes.org)
[![Presenter plugin](https://img.shields.io/badge/Notecast-presenter%20plugin-0a7ea4)](https://github.com/Stefan-Schmidbauer/trilium-presenter-plugin)
[![Render plugin](https://img.shields.io/badge/Notecast-render%20plugin-0a7ea4)](https://github.com/Stefan-Schmidbauer/trilium-notecast-render)

> **Status: 0.x.** The tools and the label contract are in use and tested, but
> nothing here is frozen: a tool signature or a `#notecast*` label may still
> change between releases, and there are no backports. Pin a tag if that matters
> to you.

An MCP server that lets an AI assistant author and manage **typed notes** in
[Trilium Notes](https://github.com/TriliumNext/Trilium) through the ETAPI.

A "type" — `slide`, `expenseReport`, `wikiEntry`, anything — is defined by a
Trilium note labelled `#notecastType=<id>` whose content is the authoring format
and whose labels carry the mechanics (target note type, mime, labels to stamp,
default parent, branch prefix). Adding a type is tagging a note; this server
ships **no** type definitions — it is a pure engine.

Each type's format is loaded live from its note and embedded directly into the
`create_note` / `update_note` tool descriptions, so it reliably reaches the
model on every client. Formats stay in a single place — editable directly in
Trilium. Changes take effect on the next connection; if a type is missing,
duplicated, or unreachable, the tool emits a loud "do not guess" notice and
creates nothing, so a missing format can never masquerade as the real one.

## Concepts

### The Notecast family

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

**Types ship with the output plugin that gives them a visible form** — the
presenter owns `slide`, the renderer owns the document types (`note`, `kbEntry`,
`meetingNote`, `checklist`, `itTip`, `letter`, `handout`). This server authors
whatever the instance defines and nothing more, so a fresh install with neither
plugin has no type to write: to try the engine first,
`tools/seed-demo-type.py` tags one throwaway type and removes it again.

### How many types this scales to

Every defined type's full authoring format is embedded into the `create_note`
*and* `update_note` descriptions, so the block is sent twice on every
connection. That is deliberate — it is the only path guaranteed to reach the
model — but the context cost grows with the number of types, not with the
number in use. With the types the two output plugins ship (eight) this is
comfortable; a few dozen would not be. Discovery itself is capped at 100
`#notecastType` notes. If you reach that point, split types across separate
server instances by purpose.

## Tools

| Tool | Description |
|---|---|
| `list_note_types` | List the available types (`#notecastType` notes) |
| `create_note` | Create a note of a type (format loaded live into this description); `labels` sets per-note labels |
| `get_note` | Read a note's raw content |
| `update_note` | Update a note's content and/or title |
| `attach_image` | Attach an image to a note and get back the reference to embed |
| `get_note_info` | Get note metadata (title, type, children, attributes) |
| `list_children` | List a note's direct children with the branch each sits on (tree navigation) |
| `clone_node` | Clone a note into another parent (a branch, not a copy) |
| `move_node` | Move a note to the n-th place among its siblings (0-based index) |
| `move_to_parent` | Move one placement of a note to a different parent |
| `unlink_branch` | Remove ONE placement of a note, keeping the note and its others |
| `set_labels` | Add or update labels on an existing note |
| `remove_label` | Remove a label from a note |
| `delete_note` | Delete a note everywhere |
| `search_notes` | Search notes (supports `#label` syntax); reports type and `dateModified` |

The prompt `note_creation_guide` carries the tool workflow and every type's
format as well — but prompts are not injected by every client, which is why the
formats are embedded into the tool descriptions too.

`create_note` reads the type definition for `note_type`, then creates the note
with the target type / mime / branch prefix it specifies and stamps its
`#notecastApplyLabels`. If `note_type` does not resolve to exactly one
definition it refuses and returns a STOP notice instead of creating anything.

The optional `labels={name: value}` sets labels on that one note, applied after
`#notecastApplyLabels` so a name given there overrides the type's default — that
is how a deck gets a title slide when the `slide` type stamps `content`.
`#notecastInstance` and `#notecastType` are refused — the first is how the
renderer resolves a note's type, the second would turn the note into a duplicate
type definition and take that type offline.

`list_children` reports a `branchId` per child: the placement of that child
*under this parent*, which is what `move_node` and `unlink_branch` address and
what tells two clones of one note apart.

### Editing the tree

Placement in Trilium lives on a *branch*, not on the note, and three of these
tools are about branches rather than notes. One of them is destructive and two
are not:

| I want to… | Tool |
|---|---|
| put a note in a second place | `clone_node` |
| change where a note sits | `move_to_parent` |
| take a note out of one place, keep it elsewhere | `unlink_branch` |
| reorder a note among its siblings | `move_node` |
| remove a note from everywhere, for good | `delete_note` |

`clone_node` makes one note appear under several parents at once — edit it in
one place, and every appearance updates. That supports reuse patterns like
Trilium Presenter's **Master / Sets** (a central library of source notes, and
finished decks assembled from clones of them), but works for any type of note.

`move_to_parent` needs `source_parent_id` as well as the target: a cloned note
sits under several parents, and nothing on the note itself says which of those
placements was meant. It writes the new placement before removing the old one,
so a failure in between leaves the note in two places rather than in none. It
refuses a source parent the note is not under, a source and target that are the
same, and a target inside the note's own subtree.

`unlink_branch` refuses to remove a note's **last** placement, because Trilium
deletes a note that has none left. Deleting a note everywhere stays an explicit,
separate decision (`delete_note`). Both behaviours are measured rather than read
out of the ETAPI spec — `tools/probe-branch-deletion.py` re-measures them after
a Trilium upgrade.

### Labels on existing notes

`set_labels` and `remove_label` keep a growing library queryable:
`#material=handout` or `#reviewStatus=outdated` can be searched for, a title
convention cannot. `set_labels` updates a label of the same name rather than
adding a second one, and refuses the same reserved names as `create_note`.

Both touch only a note's **own** labels. A note reports what it inherits
alongside what it owns, so matching on name alone would reach past the note:
patching an inherited label rewrites it on the note it comes from, and deleting
one strips it from every note inheriting it.

### Images

An image belongs to the note that shows it, as a Trilium attachment whose title
is the file name. The order is `create_note` → `attach_image` → `update_note`:
`attach_image` hands back a `reference` — the snippet to put into the note body —
already in the right form for that note's target type (a bare
`![alt](name.png)` for markdown notes, which the output plugin resolves against
the note's attachment titles; a literal `api/attachments/…` URL for HTML notes,
which Trilium renders as-is).

File names must be plain (no slashes) and unique within their note. Accepted
extensions are png, jpg, jpeg, gif, webp and svg; the payload is base64 in the
tool argument, so in practice the model's context is a tighter limit than the
server's 10 MB.

## Configuration

Everything is configured through environment variables. Account settings and
secrets go into the env file on the server,
[`deploy/env.example`](deploy/env.example) is the template; the deployment
wiring is set in [`deploy/docker-compose.snippet.yml`](deploy/docker-compose.snippet.yml).

| Variable | |
|---|---|
| `TRILIUM_API_KEY` | **Required.** ETAPI token: Trilium → Options → ETAPI → Create new token |
| `TRILIUM_URL` | Where the server reaches Trilium. Set in the snippet to Trilium's service name, `http://triliumnext:8080`; default `http://localhost:8080` |
| `TRILIUM_DEFAULT_PARENT` | Note ID new notes go under when neither the call nor the type names a parent (default: `root`) |
| `MCP_AUTH_TOKEN` | **Required.** Clients send `Authorization: Bearer <token>`. Without it the server refuses to start. Generate with `openssl rand -hex 32` |
| `MCP_PATH` | The path the endpoint is served under — `/trilium-notecast-mcp` in the snippet |
| `MCP_ALLOWED_HOSTS` | Comma-separated. The hostname the reverse proxy answers on, or every request is answered `421 Invalid Host header`. Setting it *replaces* the localhost defaults, so the snippet lists `127.0.0.1:9151` as well |
| `MCP_ALLOWED_ORIGINS` | Comma-separated. A client that sends an `Origin` header is answered `403` unless it is listed |
| `MCP_ALLOW_UNAUTHENTICATED` | `1` starts without a token — only for a deployment that authenticates further out |
| `MCP_TRANSPORT`, `MCP_HOST`, `MCP_PORT` | Set by the snippet (`streamable-http`, `0.0.0.0`, `8000`); no need to change them |

## Deployment

The server runs as a container on a Docker host and is reached over HTTPS.
`deploy.sh` builds the image *on that host*, through a Docker context over SSH —
no registry, no source checkout on the server — and recreates the container
through the compose file that already lives there.

**Prerequisites**

- A server with Docker Compose and a compose stack, reachable by SSH key as a
  user in the `docker` group, with `curl` installed.
- Docker with the `buildx` plugin on your machine (`docker buildx version`).
  The build runs on the server, but the builder is chosen locally, and the
  `Dockerfile`'s `COPY --chmod` needs BuildKit.
- A reverse proxy with TLS on the server — Tailscale, Caddy, nginx with
  Let's Encrypt, whatever is already there.

### 1. Clone

```bash
git clone https://github.com/Stefan-Schmidbauer/trilium-notecast-mcp.git
cd trilium-notecast-mcp
```

### 2. Prepare the server (once)

- Paste [`deploy/docker-compose.snippet.yml`](deploy/docker-compose.snippet.yml)
  under `services:` in the compose file on the server and adjust what its
  header names. The port is published on localhost only (`127.0.0.1:9151`).
- Create `.env.trilium-notecast-mcp` next to the compose file from
  [`deploy/env.example`](deploy/env.example), mode `640`, and fill it in.
- Paste the snippet into the stack that runs Trilium, on the network Trilium is
  on — otherwise the service name in `TRILIUM_URL` does not resolve, and
  quietly so: `/healthz` answers `ok` and only the first tool call fails. There
  is deliberately no `depends_on` (the snippet says why).

`deploy.sh` never creates the compose file and never copies secrets: both stay
on the server and belong to its admin, who can operate the service (`up -d`,
`logs`) without this repository. The cost is that `deploy/` holds *copies* that
nothing keeps in sync.

### 3. Deploy

```bash
cp deploy/deploy.env.example deploy.env    # set DEPLOY_HOST
./deploy.sh
```

It builds the image on the server, recreates this one service — with
`--no-deps`, so nothing else in the stack is touched — and waits for `/healthz`.

| `deploy.env` | Default |
|---|---|
| `DEPLOY_HOST` | none — **required**, e.g. `admin@your-server.example.net`. Passed to `ssh` verbatim, so it must be the name your `~/.ssh/config` matches |
| `DOCKER_CTX` | the host from `DEPLOY_HOST`, e.g. `your-server.example.net` — the Docker context, created on the first run and shared by every service on that host. `deploy.sh` stops if an existing context of that name points elsewhere |
| `REMOTE_COMPOSE` | `/opt/docker/docker-compose.yml` |
| `HEALTH_URL` | `http://127.0.0.1:9151/healthz` — resolved on the server |

`deploy.env` is gitignored; exported variables of the same name take precedence.

### 4. Put TLS in front

The endpoint has no encryption of its own. Point the reverse proxy at
`http://127.0.0.1:9151/trilium-notecast-mcp`, keep the path unchanged, and put the hostname
it answers on into `MCP_ALLOWED_HOSTS` (and `https://<hostname>` into
`MCP_ALLOWED_ORIGINS`). With Tailscale, for example, inside the tailnet only:

```bash
tailscale serve --bg --set-path /trilium-notecast-mcp http://127.0.0.1:9151/trilium-notecast-mcp
```

Check it from your machine:

```bash
curl -s https://mcp.example.net/trilium-notecast-mcp -H 'Authorization: Bearer <MCP_AUTH_TOKEN>'
```

`Bad Request: Missing session ID` is the right answer: proxy, path, token and
host check are all in order, and the server is asking for an MCP session.
`Invalid Host header` means the hostname is missing from `MCP_ALLOWED_HOSTS`,
`unauthorized` that the token is wrong.

### 5. Connect a client

The endpoint is `https://mcp.example.net/trilium-notecast-mcp`, with the header
`Authorization: Bearer <MCP_AUTH_TOKEN>`. [`.mcp.json.example`](.mcp.json.example)
shows it for Claude Code; any MCP client that speaks streamable HTTP works the
same way.

### Updates and rollback

```bash
./deploy.sh              # after a code change: build, recreate, health check
./deploy.sh --no-build   # after a change to the env file: recreate only
ssh admin@your-server.example.net docker logs --tail 50 trilium-notecast-mcp
```

A changed env file takes effect only when the container is *recreated*;
`docker compose restart` keeps the old environment.

Each build is tagged twice: `:local`, which the compose file references, and a
version tag from `git describe` — `v1.2.0` on a release tag, `v1.2.0-3-gafe0bf9`
three commits past it, `-dirty` for uncommitted changes — that stays behind for
a rollback. It is also the version the server reports to clients
(`serverInfo.version`), so a client can tell which build answers. To roll back:

```bash
docker --context your-server.example.net images trilium-notecast-mcp      # what is on the server

ssh admin@your-server.example.net \
  'docker tag trilium-notecast-mcp:v1.2.0 trilium-notecast-mcp:local && \
   docker compose -f /opt/docker/docker-compose.yml up -d --no-deps trilium-notecast-mcp'
```

The image exists only in the server's local store, so the service definition
must keep `pull_policy: never` — without it, a routine `docker compose pull`
fails on this service and aborts the pull for the whole stack.

### Troubleshooting

| What you see | Cause and fix |
|---|---|
| The container restarts in a loop | It exits at startup — `docker logs trilium-notecast-mcp` has the reason, usually a missing value in the env file |
| It exits with `TRILIUM_API_KEY is not set` | The env file is missing the ETAPI token |
| `list_note_types` reports no types | Nothing in the instance carries `#notecastType` yet — install an output plugin, or try `tools/seed-demo-type.py` |
| A tool reports a type as AMBIGUOUS | Two notes carry `#notecastType=<id>` for the same id. Remove the label from one — see [Note type trust](#note-type-trust) |
| It exits with `MCP_AUTH_TOKEN is not set` | The server refuses to run without a token. Set one in the env file |
| `401 unauthorized` | Token missing or wrong. The header is exactly `Authorization: Bearer <token>` |
| `421 Invalid Host header` | The hostname the request arrives under is not in `MCP_ALLOWED_HOSTS` |
| `403 Invalid Origin header` | The client sends an `Origin` that is not in `MCP_ALLOWED_ORIGINS` |
| `404 Not Found` with a valid token | The URL path is not `MCP_PATH`, or the proxy rewrites it |
| A bare `Error executing tool <name>` | An unexpected failure; the traceback is in `docker logs trilium-notecast-mcp` |
| *healthy*, but every tool call fails | `/healthz` says the process is alive, nothing more — Trilium is contacted on the first tool call; check that the container is on Trilium's network |

## Security model

Trilium has no user management and no per-token scoping: an ETAPI token grants
access to the whole instance. This server holds such a token, and its tools
inherit that reach — `search_notes` searches every note, `get_note` returns the
content of any note ID, `delete_note` deletes any note. The tool names describe
the intended use, not a boundary.

Access to the endpoint is therefore access to the entire instance, and
`MCP_AUTH_TOKEN` is the only check in front of it — a static shared secret with
no rotation, no per-client identity and no rate limiting. Writes and deletions
leave no audit trail beyond Trilium's own revision history. Run the endpoint
inside an internal network or a VPN, with the bearer token as a second layer,
not on the public internet.

`get_note` and `search_notes` return whatever a note contains, so note content
reaches the model as text it reads, and a note can carry instructions aimed at
the model. The mitigation is the MCP client's tool-approval prompts; the reading
tools are marked read-only, and every tool that overwrites or removes something
destructive, so a client can tell them apart. Every ID a tool receives is
validated before it becomes part of an ETAPI path.

### Note type trust

A `#notecastType=<id>` note is not just data — its content is embedded into the
`create_note` / `update_note` tool descriptions, so it arrives at the model as
*instructions*. By design there is **no trust boundary**: anyone who can tag a
note — via a sync peer, an import, or anyone holding an ETAPI token — defines a
type. That is the point of the "just tag a note" workflow, and it puts the
responsibility on whoever administers the instance's notes.

The one safeguard is a *clarity* guard, not a boundary: if **more than one**
note carries `#notecastType=<id>` for the same id, the server refuses to pick
one and emits an "ambiguous — do not guess" notice, the same way it refuses when
the type is missing. A duplicate fails loudly instead of silently changing the
format. There is no pinning variable — pinning every type by ID would defeat the
workflow.

Vulnerabilities: see [SECURITY.md](SECURITY.md).

## Development

```bash
python3 -m venv venv
venv/bin/pip install --require-hashes -r requirements.txt
venv/bin/pip install -r requirements-dev.txt
venv/bin/ruff check .
venv/bin/pytest -q
```

| Test | What it pins |
|---|---|
| `tests/test_id_validation.py` | `_id()` guards the ETAPI path: no request is *sent* for an ID containing `..`, `?`, `#` or CRLF |
| `tests/test_type_resolution.py` | A type resolves to exactly one definition, or refuses loudly, without a request per type; the tree edits and label tools keep their refusals |
| `tests/test_tools.py` | The live format reaches `create_note` / `update_note`, end to end through a real client session |
| `tests/test_attachments.py` | An image survives the roundtrip byte for byte and gets the reference form its note's target type needs |
| `tests/test_auth.py` | The HTTP front door: bearer check, health endpoint, startup guard |

Without `MCP_TRANSPORT` the server speaks stdio, which is the quickest way to
try a change before deploying it — with the
[MCP Inspector](https://github.com/modelcontextprotocol/inspector), passing the
variables with `-e`:

```bash
npx @modelcontextprotocol/inspector --cli venv/bin/python server.py \
  --method tools/list \
  -e TRILIUM_URL=http://localhost:8080 -e TRILIUM_API_KEY=…
```

The tests need no Trilium: ETAPI is mocked at the HTTP layer with `respx`, so
the URL a tool actually requests is part of what is checked. What the mocks
cannot pin is whether Trilium itself still behaves as measured —
`tools/probe-attachment-binary.py` and `tools/probe-branch-deletion.py` re-measure
that against a live instance after a Trilium upgrade.

`mcp` is the dependency to watch: the live-format injection edits the
`tools/list` payload, and `test_the_block_reaches_a_real_client` is what turns a
middleware that stops being called into a failing test. After bumping it, also
check in the Inspector that `create_note`'s description still carries the format
block.

The label contract the three Notecast repositories share is in
[docs/notecast-contract.md](docs/notecast-contract.md) — changes belong there
first.

### Dependencies

`requirements.in` holds the direct dependencies; `requirements.txt` is generated
from it with hashes and is what the image installs (`--require-hashes`). To bump
a version, edit `requirements.in`, regenerate, and commit both together:

```bash
venv/bin/pip-compile --generate-hashes --strip-extras requirements.in
```

CI runs lint and tests, fails if `requirements.txt` no longer matches
`requirements.in`, and runs `pip-audit` against the pinned set. Dependabot
proposes the bumps monthly.

## License

MIT — see [LICENSE](LICENSE).

**Stefan Schmidbauer** — [GitHub](https://github.com/Stefan-Schmidbauer).
Built with [Claude Code](https://claude.com/claude-code) as co-author.
