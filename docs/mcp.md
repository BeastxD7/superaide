# Tools (MCP): any MCP server becomes Ava's tools

Where: Dashboard → Setup → Tools (MCP) (issue #666).

[MCP](https://modelcontextprotocol.io) is a standard way for a program or a web service to offer tools to an assistant.
Add a server here and its tools appear to Ava as `mcp__<server>__<tool>`. Only you, the owner, can add servers.
Visitors and other people never get MCP tools, and one owner's servers are never visible to another owner.

## Adding a server

- **Remote server**: a name and an address (`https://notes.example.com/mcp`). Optional headers, such as an API key,
  go to the vault and are shown afterwards only as a masked hint. The address must resolve to a public host; private
  and local addresses are refused, and the check is repeated on the connection itself.
- **Program on this computer**: a command and its arguments, with optional environment variables (also vault only).
  This runs a program with your permissions, so it sits behind the same "I understand" warning as other sensitive
  changes (and your password when the server asks for one). The program starts with a scrubbed environment: it never
  sees Ava's own secrets. Not available on a hosted server, where the page does not offer it and the API refuses it.

At most 20 servers per owner. Press **Test connection** to list the tools. Nothing is callable yet.

## Approving tools

**Approve tools as listed** records each tool's name, description, schema and hints as a hash. A server that later
changes a tool (a new description is the classic way to smuggle instructions) makes that tool blocked and shown as
*Changed since approved* until you approve it again. Unapproved tools are not offered to Ava at all.

## Risk class

Each tool has a class you set, saved in code and bound to the tool's hash:

| Class | What happens |
| --- | --- |
| read | runs at once |
| write (the default for anything unknown) | waits for your OK on an approval card every time |
| destructive | waits for your OK every time, and is refused when the turn has read untrusted content |

What the server says about itself (read-only or destructive hints) is shown as a hint and never lowers the class.

## What a server returns is information, never instructions

A result is marked untrusted and the turn becomes tainted: the rest of that turn can only read after your OK, and
destructive tools are refused. Results are cut at 20,000 characters. Arguments are checked against the tool's JSON
schema before anything is sent. The model only proposes a call; code decides whether it runs.

## Limits

Each server has a timeout (default 30 seconds, up to 120) and a calls-per-hour limit (default 60, up to 1,000), both
changeable through the API. Calls count against the owner's Claude spend cap like any other turn. Every add, change,
approval, risk change, removal and call is written to the audit log.

## Removing

**Remove** deletes the server, its approvals and its stored secrets.

## API

`GET/POST /app/mcp/servers`, `PATCH/DELETE /app/mcp/servers/{id}`, `POST /app/mcp/servers/{id}/test`,
`POST /app/mcp/servers/{id}/approve`, `PUT /app/mcp/servers/{id}/tools/{tool}/risk`. Owner session and CSRF header
required. See `/docs` for the schemas.
