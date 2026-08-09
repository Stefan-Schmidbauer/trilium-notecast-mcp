# Security Policy

## Reporting a vulnerability

Please report security issues **privately**, through GitHub's private
vulnerability reporting: open the
[Security tab](https://github.com/Stefan-Schmidbauer/trilium-notecast-mcp/security)
of this repository and use **Report a vulnerability**. That opens a private
advisory visible only to you and the maintainer.

Please do not open a public issue for a vulnerability, and do not include a
working ETAPI token or bearer token in the report — describe the setup instead.

This is a spare-time project maintained by one person. Expect an initial reply
within about a week; there is no guaranteed fix window.

## Supported versions

Only the latest commit on `main` is supported. There are no backports to tags.

## Scope

This server holds a Trilium ETAPI token and exposes tools that act with it. The
[Security model](README.md#security-model) section of the README describes what
that grants and which properties are *not* claimed. Reports about the following
are expected and useful:

- a way to reach an ETAPI endpoint the tools do not intend to expose — for
  example an entity ID that escapes `_id()` validation and alters the request
  path
- a way to pass the `MCP_AUTH_TOKEN` bearer check without the token
- the token leaking into logs, tool output, or an error message
- a way for a note that is *not* labelled `#notecastType` to end up embedded in
  a tool description

Known and documented, so **not** a finding on their own — see the README:

- an ETAPI token grants access to the whole Trilium instance; the tools inherit
  that reach and are not confined to notes of a known type
- `MCP_AUTH_TOKEN` is a static shared secret with no rotation, no per-client
  identity and no rate limiting; running the endpoint without a network boundary
  in front of it is out of scope
- anyone who can label a note defines a note type, and a type's content reaches
  the model as instructions — this is the design, not a boundary that failed
- note content returned by `get_note` / `search_notes` reaches the model as text
  it reads, so a note can carry instructions aimed at the model; the mitigation
  is the MCP client's tool-approval prompts

If you are unsure which side of that line a finding falls on, report it.
