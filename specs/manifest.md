# MSYS Manifest v1

An MSYS package installs one or more components. A component is the unit that
`msysd` starts, stops, activates, supervises, and grants a role lease to. The
format is language and framework neutral: `msysd` executes an argv array and
does not import application code.

The normative machine-readable contract is
[`examples/package-manifest.schema.json`](../examples/package-manifest.schema.json).
Unknown unprefixed fields are invalid. Experimental vendor fields must start
with `x-` so a typo cannot silently become configuration.

## Canonical shape

```json
{
  "schema": "msys.manifest.v1",
  "package": {
    "id": "org.example.clock",
    "name": "Clock",
    "version": "1.0.0",
    "kind": "application"
  },
  "components": [
    {
      "id": "main",
      "runtime": "python",
      "exec": ["files/runtime/python/bin/python3", "files/app/main.py"],
      "lifecycle": "manual",
      "restart": "never",
      "readiness": {"mode": "exec", "timeout_ms": 3000},
      "icons": [
        {"size": 64, "mime": "image/png", "path": "files/share/icons/clock.png"}
      ],
      "windowing": {
        "system": "x11",
        "display": "inherit",
        "mode": "window",
        "title": "Clock",
        "identity": {
          "app_id": "org.example.clock",
          "x11_wm_class": "ExampleClock"
        }
      },
      "activation": {
        "launchable": true,
        "intents": [{"action": "settings-panel", "name": "clock"}]
      },
      "isolation": "baseline",
      "permissions": ["display:x11"]
    }
  ]
}
```

Component ids must be unique inside a package. The fully-qualified identity is
`package.id:component.id`, for example `org.example.clock:main`.

Both `package.icons` and optional component-level `icons` contain objects with
`path`, optional pixel `size`, and optional MIME type. A non-empty component
icon set overrides the package fallback in launchers and choosers; a missing or
empty component set uses `package.icons`. Icons are presentation metadata only:
they never make a component launchable, register an intent or role, or grant a
permission. Those remain controlled solely by the component's explicit
`activation`, `provides`, and `permissions` fields.

Optional `x-msys-i18n` presentation metadata can point at a package-local
`msys.i18n.catalog.v1` and declare `name_key` and/or `summary_key`. It only
localizes labels; it cannot make a component launchable or alter identity. See
[`i18n.md`](i18n.md#package-layout-and-ownership).

## Runtime and execution

`runtime` is descriptive metadata for launchers, diagnostics, and installers.
The v1 built-in labels are `native`, `c`, `cpp`, `python`, `qt`, `electron`,
`tk`, `shell`, and `custom`; a portable extension label starts with `x-`.
It does not select an interpreter or alter process launch behavior.

`exec` is the exact, non-empty argv array. Its first element is the executable;
the remaining elements are passed unchanged. `msysd` does not invoke a shell,
join arguments, expand `$VARIABLES`, or process quoting. If shell behavior is
deliberately required, declare the shell explicitly, preferably from the
package itself:

```json
"exec": ["files/runtime/busybox", "sh", "files/app/start.sh"]
```

Installed packages run with the committed package version as their default
working directory. Relative `exec`, `cwd`, icon, and other file paths are
therefore package-root relative. Absolute paths are allowed for device-specific
system providers, but application packages should bundle every non-kernel
runtime dependency under `files/`. An executable name such as `python3` or
`bash` asks the host `PATH` to resolve it and is intentionally not portable.

An argv item beginning with `@package/` is an explicit package-root reference.
It must be a safe POSIX-relative path with no empty, dot, parent, backslash, or
root-escaping segment, and the referenced object must exist in the staged
package. If `exec[0]` uses this form, it must be a regular non-symlink file with
an executable mode. Install/archive inspection enforces this before commit,
and the supervisor repeats the containment check before launch. This prevents
a content-hashed but incomplete archive from passing validation and failing
only after activation.

`env` contains literal string values. It augments the clean session environment
prepared by `msysd`; it is not a shell script and performs no interpolation.

## Lifecycle and restart

- `session`: starts for the selected MSYS profile and stops with that session.
- `background`: starts eagerly and remains resident.
- `on-demand`: starts when a role, interface, capability, or intent needs it.
- `manual`: starts only through explicit activation, normally from a launcher
  or developer command.

Background residency is ordinary component policy, not a separate daemon
format. Components remain in the foreground and must not double-fork.

An `on-demand` component may opt into idle reclamation with
`idle_timeout_ms`, an integer from `1000` through `86400000` (one second
through 24 hours). Omission is the v1 compatibility default: the activated
provider remains resident until an explicit stop, exit, update, or session
shutdown.

The timeout starts only after readiness with no forwarded calls in flight. A
new forwarded call cancels the current timer before delivery. When several
calls overlap, Core starts a fresh full timeout only after the last call has
returned, failed, or reached its RPC deadline. Expiry rechecks both the live
component generation and its in-flight count, then uses the normal graceful
stop path. A stale timer can never stop a replacement generation.

`idle_timeout_ms` is invalid on `manual`, `background`, and `session`
components; their lifecycle semantics are unchanged.

- `never`: do not restart after exit.
- `on-failure`: restart only after a non-successful or abnormal exit.
- `always`: restart after every exit, including a successful one.

Restart attempts are bounded by supervisor backoff and quarantine policy. A
quarantined provider releases any exclusive role lease so a fallback can take
over.

## Readiness

`readiness` is optional; the v1 default is
`{"mode":"exec","timeout_ms":5000}`.

- `exec`: ready when the process survives the supervisor's initial execution
  check. This is appropriate for apps that do not use mIPC.
- `mipc-ready`: ready only after the component sends the mIPC `ready` message.
- `x11-display`: ready when the declared display provider's X11 display becomes
  reachable.

`timeout_ms` bounds startup, not total process lifetime. A component that exits
or misses its readiness deadline has failed startup and follows its restart
policy.

## Provides and dependencies

Every `provides` item declares exactly one of:

- `role`: a replaceable system job such as `launcher`, `window-manager`, or
  `display-output`;
- `interface`: an mIPC callable interface implemented by the component;
- `capability`: a discoverable feature such as `input.touch.ch347`.

Each item explicitly states `exclusive`. `priority` is an optional signed
preference; profile ordering still has the final say. Multiple packages may be
installed for the same role, while only the selected exclusive provider owns
its lease.

```json
"provides": [
  {
    "role": "launcher",
    "exclusive": true,
    "priority": 50,
    "x-msys-contract": {
      "id": "org.msys.role.launcher.v1",
      "version": "1.0.0"
    }
  },
  {"interface": "org.example.launcher.v1", "exclusive": false},
  {"capability": "launcher.search", "exclusive": false}
]
```

`x-msys-contract` is the versioned role-provider declaration. It names one
exact immutable descriptor revision; it neither grants the role lease nor adds
an mIPC permission. Static and behavioral checks are specified in
[`versioned-role-contracts.md`](versioned-role-contracts.md). Core may ignore
this reserved extension during staged adoption, but package/release validation
should reject an unknown or mismatched claim.

- `requires`: hard component dependency; readiness is blocked if it fails.
- `wants`: best-effort dependency.
- `after`: ordering constraint only and does not cause startup.

References may be local component ids or fully-qualified
`package.id:component.id` values. Hard dependencies must form an acyclic graph.

Interfaces and capabilities use the same installed catalog but have different
runtime meaning. `interface:<name>` is callable over mIPC and wakes an
on-demand provider; `capability` is discovery-only metadata such as a sensor or
display feature. Applications can call an exact discovered provider through
`component:<package-id>:<component-id>`. This keeps wake-up and cross-language
communication independent of Python, Qt, Electron, Tk, or native ABI choices.

## Window declaration

`windowing` declares policy metadata; it does not force an application toolkit.
X11 components must declare `display`. Ordinary visual clients should use
`"display": "inherit"`; the supervisor then injects the concrete `DISPLAY`
from the `display-output` provider selected by the active profile. A
`display-provider` can never use `inherit`; an X11 provider must instead
declare the concrete display it creates, such as `:0` or `:24`. A
component-level `env.DISPLAY` remains an explicit override
for diagnostics and unusual multi-display applications, not the normal product
binding. Modes are `window`, `fullscreen`, `background`, `overlay`, and
`display-provider`. Visible window, fullscreen, and overlay components must
declare both `title` and `identity`.

`title` is a user-facing initial/fallback label and may change while the app is
running. It must never be used as the stable window identity. `identity.app_id`
is stable across launches; X11 apps should also set `x11_wm_class` and, when
needed, `x11_wm_instance`. A future Wayland provider uses `wayland_app_id` from
the same declaration. Window-policy providers match identity first and use the
title only for presentation.

A single process that owns several independently identified surfaces may add
the component-level `x-msys-role-windows` map. Keys are versioned roles claimed
by that same component and values use the closed `windowing` shape, for example
`launcher` with mode `window` and `navigation-bar` with mode `overlay`. The
role-contract validator applies each descriptor only to its matching claim,
rejects unclaimed or unknown roles, unknown descriptor fields, duplicate stable
identities, and conflicting legacy metadata. For older catalog consumers,
`windowing` may remain as a compatibility primary surface, but it must exactly
mirror one entry in `x-msys-role-windows`. Components with one surface continue
to use `windowing` alone.

## Activation

`activation.launchable` explicitly controls whether a component appears in a
normal application launcher. It is independent of lifecycle: a manual UI app
is commonly launchable, while a manual screen shield or diagnostic provider is
commonly not launchable.

`activation.intents` declares typed entry points:

```json
"activation": {
  "launchable": true,
  "intents": [
    {"action": "open-uri", "schemes": ["https", "file"]},
    {"action": "open-mime", "mime": ["text/plain", "image/*"]},
    {"action": "settings-panel", "name": "display"},
    {"action": "org.example.scan", "priority": 20}
  ]
}
```

`open-uri`, `open-mime`, and `settings-panel` require `schemes`, `mime`, and
`name` respectively. Other reverse-domain or simple action names are custom
intents. Intent selection and arguments are transported through mIPC; no shell
command line is synthesized from untrusted URI or MIME data. When several
components match, core sends their component ids and presentation metadata to
`role:chooser.choose_intent`; the chooser returns one of those ids. Handler
preferences are chooser-owned user state and never mutate package manifests.

## Isolation

`isolation` is optional. When it is absent, the component keeps the compatible
launch path and no namespace, prctl, rlimit, or seccomp restriction is implied.
It can be a profile shorthand:

```json
"isolation": "baseline"
```

or a strict object:

```json
"isolation": {
  "profile": "custom",
  "failure": "fail-closed",
  "namespaces": ["user", "mount", "ipc", "uts", "network"],
  "no_new_privs": true,
  "dumpable": false,
  "rlimits": {
    "as": 536870912,
    "core": 0,
    "nofile": {"soft": 128, "hard": 256}
  },
  "seccomp": {"mode": "helper", "profile": "desktop-v1"}
}
```

The only profiles are:

- `none`: no kernel restrictions;
- `baseline`: no-new-privileges, non-dumpable, no core dumps, and a bounded
  descriptor limit;
- `namespaced`: baseline plus user, mount, IPC, UTS, and network namespaces
  and conservative process/descriptor limits;
- `custom`: no implicit settings beyond fields in the declaration.

An object with no `profile` is `custom`. The only failure policies are
`fail-closed` and `best-effort`. Any explicit isolation declaration defaults to
`fail-closed`; absence of the entire field remains the compatible path.
Fail-closed rejects an unavailable or failed requested capability.
Best-effort may start with a reported degradation, except after a partially
entered user namespace because that transition cannot be rolled back safely.

`namespaces` is a unique list containing only `user`, `mount`, `ipc`, `uts`,
and `network`. `pid` is intentionally invalid in v1: correct PID namespace
entry needs a dedicated double-fork/launcher helper. A mount namespace makes
mount propagation private but does not by itself hide the host root filesystem.

`rlimits` supports `as`, `core`, `cpu`, `data`, `fsize`, `memlock`, `nofile`,
`nproc`, and `stack`. A non-negative integer sets both soft and hard limits.
An object may contain `soft`, `hard`, or both; omitted sides inherit the other
side, and soft must not exceed hard. `null` removes a default supplied by a
built-in profile. JSON Schema cannot express the cross-field numerical
comparison, so the zero-dependency installer performs that normative check.

`seccomp` is `"off"`, `"helper"`, `null`, or an object with optional `mode`
(`off`/`helper`) and `profile`. The nested `seccomp.profile` is the seccomp
policy name and must match `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`. Helper mode
requires effective `no_new_privs=true`; `baseline` and `namespaced` supply that
default unless explicitly disabled. The helper itself is trusted
operator/runtime configuration, not a package dependency or proof that the
selected filter is secure.

During migration, `x-msys-isolation` accepts exactly the same value and is
validated just as strictly. A component must not declare both `isolation` and
`x-msys-isolation`. Other unknown fields inside an isolation, rlimit, or
seccomp object are invalid; there is no nested vendor-extension namespace.

Isolation is an optional containment layer, not a claim of a complete sandbox.
The v1 declaration does not promise filesystem hiding, cgroups, Landlock,
capability bounding, PID namespaces, or an automatically generated seccomp
allowlist. Capability discovery may prove that a kernel API exists while the
actual permission to use it remains a child-launch-time decision.

## Permissions

`permissions` is a unique list of explicit capability strings, for example:

```json
"permissions": [
  "mipc.call:role:notification-center",
  "mipc.call:msys.core.discover",
  "mipc.event:subscribe:msys.power.*",
  "mipc.event:publish:msys.power.changed",
  "display:x11::24",
  "device:input:touch"
]
```

`mipc.call:*`, exact `mipc.call:<target>`, and method-level
`mipc.call:<target>.<method>` grants are enforced for calls made through a
supervised component's inherited mIPC channel. Role, interface, and component
target spelling follows [mIPC](mipc.md). Event publish/subscribe permissions
are also enforced; their only pattern form is a wildcard at the final
character. Prefer the explicit `interface:<name>` call spelling, although the
broker accepts the early-v1 bare interface-name grant (including its method
suffix) for compatibility.

Other permission families remain policy/audit metadata unless their owning
subsystem explicitly documents enforcement. This must not be presented as a
complete sandbox: only unmatched root `control.sock` peers are operator
administrators (managed peers retain their ACL), same-UID X11 clients can
generally inspect or control each other, and isolation profiles do not
translate filesystem/device strings into syscall allowlists.
