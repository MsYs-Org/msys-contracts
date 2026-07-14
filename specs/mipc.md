# mIPC Protocol

mIPC is the private MSYS control protocol. It is not D-Bus and does not require
any host service.

## Transport

- `AF_UNIX`
- `SOCK_SEQPACKET`
- `SOCK_NONBLOCK`
- `SOCK_CLOEXEC`
- `SCM_RIGHTS` for file descriptor passing

Launched components receive a private inherited control FD in
`MSYS_CONTROL_FD`. This FD authenticates the exact component generation and its
manifest permissions. Public clients may connect to
`${MSYS_RUNTIME_DIR}/control.sock`; that socket is an administrator/operator
endpoint for an unmanaged root peer. The broker reads Linux `SO_PEERCRED`
before client input. If the peer PID, process group, or session belongs to a
live supervised component generation, the connection is attributed to that
component and its manifest call ACL is enforced exactly as on the inherited
FD. An unmatched non-root peer is denied. Deployments must still protect the
runtime directory and mode-`0600` socket with filesystem ownership.

## Frame

The MVP Python reference implementation uses newline-free JSON packets over
seqpacket sockets. The native ABI will freeze the binary header below before
non-development releases:

```c
struct MsysFrameHeader {
    char     magic[4];       /* MSY1 */
    uint8_t  major;
    uint8_t  minor;
    uint16_t header_len;
    uint16_t type;
    uint16_t flags;
    uint32_t payload_len;
    uint64_t request_id;
    uint64_t object_id;
    uint16_t fd_count;
    uint16_t reserved16;
    uint32_t reserved32;
};
```

JSON packets carry:

```json
{
  "type": "hello|welcome|ready|call|return|error|subscribe|event|shutdown",
  "id": 1,
  "target": "role:launcher",
  "method": "open",
  "payload": {}
}
```

### JSON-lines child bridge

Runtimes without a `SOCK_SEQPACKET` binding may be wrapped by the package-owned
`msys-mipc-stdio` bridge. The bridge alone retains `MSYS_CONTROL_FD`; it removes
that variable from the child and exposes `MSYS_MIPC_TRANSPORT=stdio-jsonl-v1`.
The child reads one inbound JSON object per line from stdin and writes one
outbound object per line to stdout. Diagnostics use stderr. It still performs
the ordinary `hello`/`welcome`/`ready` exchange and receives exactly the same
component identity, ACL, calls, events, and shutdown message.

The bridge validates UTF-8, object shape, line boundaries, and the same 256-KiB
limit before translating each line to one seqpacket. Invalid protocol output,
ordinary logs written to stdout, or closing stdout while continuing to run fail
the component. This is a transport adapter for Node/Electron, Lua, Ruby, Go
prototypes, and similar runtimes; it does not grant authority or change the
manifest contract.

## Addressing and activation

The `target` field is language neutral and has four v1 forms:

- `role:<name>` calls the selected replaceable system-job provider;
- `interface:<name>` calls the highest-priority live component declaring that
  interface, starting an on-demand provider first when necessary;
- `component:<package-id>:<component-id>` calls one explicit component and is
  the escape hatch when discovery returned several interface providers;
- `msys.core` calls supervisor discovery, lifecycle, activation, and broadcast
  methods.

`msys.core.activate_role({role})` is the generic visual-role activation method.
It resolves the current RoleRegistry provider and uses that provider's manifest
window identity/title; callers never send or guess a component id for Home.

The method names, JSON payload shapes, stable errors, and replay metadata for a
role can be frozen by an [`msys.role-contract.v1`](versioned-role-contracts.md)
descriptor. The descriptor documents mIPC; it does not add a fifth addressing
form, bypass role selection, or grant call/event authority.

Interfaces do not acquire role leases. Multiple providers may be installed and
`msys.core.discover` exposes all of them in deterministic priority order. A
call without an explicit component tries another provider only when the method
is declared idempotent and the first provider failed for a liveness reason.
Non-idempotent calls return `OUTCOME_UNKNOWN` instead of being replayed.

Calling an interface or component is also its wake-up operation. The
supervisor resolves dependencies, starts the component using its ordinary
manifest lifecycle, waits for readiness, then forwards the original request
and deadline. No executable name, shell command, or framework-specific launch
rule appears in the IPC request.

An on-demand provider may declare the manifest `idle_timeout_ms` policy. Core
holds that generation active while any forwarded RPC is in flight, cancels an
armed idle timer before each delivery, and rearms a full timeout only after the
last concurrent RPC completes. Timeout, send failure, and normal return all
close the broker's in-flight interval. The generation is rechecked before the
normal graceful stop path, so an old timer cannot stop a restarted provider.

## Component call authorization

Every `call` received on an inherited component channel is authorized before
target resolution, activation, or forwarding. The caller needs one exact
manifest permission:

```text
mipc.call:*                                      # all mIPC call targets
mipc.call:msys.core                              # every core method
mipc.call:msys.core.discover                     # one core method
mipc.call:role:window-manager                    # one role target
mipc.call:role:window-manager.get_layout         # one role method
mipc.call:interface:org.msys.hal.manager.v1      # one interface target
mipc.call:component:org.example.provider:main    # one component target
```

Appending `.<method>` narrows any target grant. Every candidate is compared as
a complete string; prefix and embedded wildcard matching are not supported.
Target/method names which would make the dotted permission string ambiguous
must not be combined in one service catalog. The sole all-call wildcard is the
complete `mipc.call:*` permission.

For compatibility with early v1 manifests, an interface grant may omit the
literal `interface:` target prefix. Thus
`mipc.call:org.msys.hal.manager.v1` authorizes calls to
`interface:org.msys.hal.manager.v1`. New manifests should use the explicit
form. Role and component grants have no abbreviated form.

An interface grant also authorizes a call addressed to one exact
`component:<package>:<component>` when the current trusted catalog declares
that component as a provider of the granted interface. This lets a manager
discover, probe, and compare multiple providers without an all-component
grant. Core validates the catalog edge at authorization time; component-name
similarity or a stale discovery result never grants access. Method-scoped
interface grants remain method-scoped. A component grant does not gain the
inverse ability to call every provider of an interface.

Calls issued internally by `msys.core`, and calls from an unmatched root
operator through the public socket, are exempt. A public peer attributed to a
managed component is not exempt. An unauthorized component call returns an
`error` with code `ACCESS_DENIED`, is logged against the authenticated
component identity, and is never resolved, activated, or forwarded.

## Event subscriptions and fan-out

A component subscribes with an exact topic, a bounded prefix pattern ending in
`*`, or the global `*` pattern:

```text
subscribe("msys.hal.changed")
subscribe("msys.hal.*")
subscribe("*")
```

Publishers always emit a concrete topic; wildcard characters are subscription
syntax only. Prefix matching is literal and deterministic, so `msys.hal.*`
matches `msys.hal.changed` but not `msys.power.changed`. Each component has a
bounded subscription set. Fan-out is best effort and never gives a subscriber
process or role authority over the publisher.

Both sides of event fan-out are authorized on inherited component channels:

```text
mipc.event:subscribe:msys.hal.changed
mipc.event:subscribe:msys.hal.*
mipc.event:publish:msys.hal.changed
mipc.event:publish:msys.hal.*
```

Only a final `*` is wildcard syntax. A wildcard subscription request must be a
subset of its grant: an `msys.hal.*` grant may request
`msys.hal.power.*`, but an exact `msys.hal.changed` grant cannot request
`msys.hal.*`. Publishers must always send a concrete topic. Core-originated
events are administrator traffic and remain exempt from publish ACLs.

Legacy `subscribe` frames have no `id` and successful subscriptions remain
fire-and-forget. A sender may include an integer `id`; success then returns:

```json
{"type":"return","id":7,"payload":{"subscribed":"msys.hal.changed"}}
```

A rejected subscription is always observable. It returns `ACCESS_DENIED` (or
`BAD_SUBSCRIPTION`/`SUBSCRIPTION_LIMIT`) with the supplied id; when the legacy
frame omitted an id the error uses reserved `id: 0`. A rejection never changes
the component's subscription set. Unauthorized event publishes follow the
same `ACCESS_DENIED` error rule and are never fanned out.

## Required message behavior

- Every RPC has a request id and deadline.
- RPC responses must not be silently dropped.
- Events may be dropped or coalesced when a client is slow.
- Provider disconnect cancels its leases, subscriptions, and pending calls.
- Component call, subscribe, and publish ACL failures use stable
  `ACCESS_DENIED` errors and must not be forwarded or fanned out.
- Already delivered non-idempotent RPCs are not replayed after provider loss.
- High-frequency pixels/audio/video use `memfd`/`eventfd`, not JSON payloads.

## Threat model and current boundary

The ACL is an enforced broker policy boundary, but it is not a privilege
sandbox for mutually hostile root processes. Current OpenStick components run
with the same UID and may retain enough Linux authority to inspect other
processes. A deliberately malicious root component can also create a new
session (`setsid`), re-parent helpers, manipulate `/proc`, or attack the broker
outside mIPC; PID/PGID/SID attribution alone cannot prove origin against that
attacker.

A hardened deployment needs distinct component UIDs and/or user namespaces,
capability removal, protected cgroups or another non-forgeable launch label,
and filesystem/device isolation. Until those exist, mIPC ACLs reliably prevent
undeclared routing through ordinary inherited or attributable public channels
and catch manifest mistakes, but must not be advertised as isolation between
hostile same-UID/root native programs.

## Lifecycle transition event

Foreground application generations publish `msys.lifecycle.transition` so a
replaceable shell provider can render launch/close animation without owning
process lifecycle. The bounded payload is:

```json
{
  "phase": "launching|launched|closing|closed|failed",
  "component": "org.example.app:main",
  "title": "Example App",
  "identity": "org.example.app",
  "generation": 4,
  "timestamp_ms": 1700000000000,
  "returncode": 0
}
```

`returncode` and an error `message` are optional. The event is presentation
input only: missing or crashing animation providers never block starting or
stopping the application, and they receive no authority over its process.
