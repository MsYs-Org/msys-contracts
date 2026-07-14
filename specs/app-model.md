# MSYS Application Model

An MSYS application is a versioned directory plus a manifest. It is not a
Debian package, system service, Python environment, Electron special case, or
Qt special case. Qt, Electron, Tk, Python, C, C++, and shell programs all become
the same kind of supervised component.

The system only depends on the Linux kernel/process ABI and the files shipped
in the package. It does not call `apt`, `dpkg`, systemd, D-Bus, logind, polkit,
or a host language package manager to launch an app.

## Installed package boundary

```text
org.example.app/
  manifest.json
  files/
    app/                 application source/assets
    bin/                 native entry points
    lib/                 private native libraries
    runtime/             optional private Python/Qt/Electron/Tk/shell runtime
    share/               icons and other read-only data
```

The installer verifies and commits this whole directory atomically. It never
copies libraries into `/usr`, edits the host `PATH`, or installs target-machine
packages. Different applications may carry different runtime versions without
colliding. Shared read-only runtime images and content-addressed deduplication
are valid future storage optimizations, but they must preserve this logical
package boundary.

The workstation packager may stage a source repository through a bounded
`.msys-packageignore` list before creating this directory. That file is a build
input, not installed metadata: it contains exact root-relative source-only
paths, never globs or parent traversal, and cannot exclude `manifest.json`.
Package hashes and all `@package` closure checks apply after staging, so ignore
rules cannot create an unverified runtime dependency.

Explicit `@package/...` argv references are closed over that directory: every
referenced file/directory must be present, paths cannot escape through `..` or
symlinks, and a package-owned executable entry point must be a real executable
file. The same check is used by package inspection, remote update ingress, and
the final process launcher.

Package manifests describe identity and presentation:

```json
{
  "id": "org.example.app",
  "name": "Example App",
  "version": "1.0.0",
  "kind": "application",
  "vendor": "Example",
  "summary": "Short user-facing description",
  "icons": [{"size": 64, "mime": "image/png", "path": "files/share/icons/app.png"}]
}
```

One package may expose several components, such as a launchable window, a
resident sync agent, and an on-demand intent handler. They share installation
and versioning but have independent lifecycle, restart, readiness, roles, and
permissions.

`package.icons` is the fallback presentation for the package. A component may
declare its own `icons` array with the identical path/size/MIME shape; a
non-empty component-level set wins, while a missing or empty set uses the
package fallback. This matters for suites which expose Notes, Calculator, and
Device Info as three independently launchable apps from one atomically updated
package. Icons do not opt a component into the launcher and do not grant roles,
intents, or permissions; those declarations stay independent per component.

## Framework-neutral launch recipes

`runtime` labels the implementation for diagnostics; `exec` remains the only
launch instruction.

| Implementation | Typical self-contained `exec` |
| --- | --- |
| Python | `["files/runtime/python/bin/python3", "files/app/main.py"]` |
| Tk | `["files/runtime/python-tk/bin/python3", "files/app/main.py"]` |
| Qt C/C++ | `["files/bin/example-qt"]` with private Qt libraries/plugins under `files/` |
| Electron | `["files/runtime/electron/electron", "files/app"]` |
| C | `["files/bin/example-c"]` |
| C++ | `["files/bin/example-cpp"]` |
| shell | `["files/runtime/busybox", "sh", "files/app/start.sh"]` |

A native C/C++ build may be fully static or carry its dynamic loader and private
libraries. A Qt package carries its required platform plugin (for X11,
typically `xcb`) and selects it through literal environment settings. An
Electron package carries the matching aarch64 Electron distribution. A Tk
package carries a Python build with `_tkinter` plus its Tcl/Tk data. Python
dependencies live inside the package runtime/site-packages, not the host.

Installed packages also receive private state roots without requiring Linux
users or a host package manager: `HOME`, XDG config/data/cache, runtime, and tmp
all point below MSYS-managed per-package directories. Host `PYTHONPATH`,
`PYTHONHOME`, and virtual-environment variables are removed, with
`PYTHONNOUSERSITE=1` and `PYTHONDONTWRITEBYTECODE=1`, so a Python/Tk/Qt
application cannot accidentally resolve dependencies from the supervisor's
development runtime or mutate an installed version with `__pycache__`. Built-in system
providers retain the shared MSYS SDK path. A signed package declared as
`kind: system` may also use the version-pinned platform SDK ABI: the supervisor
replaces `PYTHONPATH` with the validated `MSYS_PLATFORM_PYTHONPATH`, which must
point directly to an `msys_sdk` tree selected by the active MSYS release.
Component manifest environment cannot select or extend that path. Ordinary
application packages never receive it and remain self-contained. This is dependency and state
isolation, not a security boundary; a future namespace/seccomp executor can
enforce manifest permissions without changing the app contract.

Packages are built for a target architecture and libc ABI. The update index can
select the matching artifact (for example aarch64/glibc) before installation;
the v1 process supervisor does not emulate a foreign architecture.

Using a host command such as `python3`, `bash`, or `electron` is allowed for a
device-owned development/system manifest, but it is an explicit host dependency
and is not a portable application package. Application examples use bundled
paths for this reason.

## Plain apps and mIPC-aware apps

A program that only displays an X11 window needs no MSYS SDK. It receives its
declared environment, runs as a normal child process, and is managed using its
manifest window identity and process ownership.

An app uses mIPC when it needs activation arguments, notifications, role calls,
events, readiness signaling, or other MSYS services. It can use an SDK or speak
the documented framed protocol directly. Language choice never changes its
authority or lifecycle.

Electron/Node and other stream-oriented runtimes may put a package-owned
`msys-mipc-stdio` before their real executable. The wrapped process then needs
only built-in JSON and line-stream APIs: stdin/stdout carry protocol records and
stderr carries logs. Because the bridge retains the inherited launch FD and
removes it from the child, the application still has one authenticated manifest
identity and cannot accidentally treat its protocol stream as console output.

An app can advertise a callable `interface` or a discoverable `capability` in
the same `provides` list used by system roles. Calling `interface:<name>` wakes
an on-demand provider and routes the request to it. Discovery returns every
matching component, so a caller can deliberately address
`component:<package-id>:<component-id>` when several peer implementations are
installed. Generic events remain topic broadcasts with explicit subscriptions;
they do not require either side to know the other's language or PID.

## Roles are jobs, not privileged binaries

System UI pieces use the same manifest as ordinary applications. They advertise
replaceable roles such as:

```json
{"role": "launcher", "exclusive": true, "priority": 50}
{"role": "window-policy", "exclusive": true, "priority": 50}
{"role": "navigation-bar", "exclusive": true, "priority": 50}
{"role": "notification-presenter", "exclusive": true, "priority": 50}
{"role": "screen-shield", "exclusive": true, "priority": 50}
```

A profile selects preferred and fallback providers. Exactly one selected
provider owns an exclusive role at a time. A Qt launcher can therefore replace
a Tk launcher without changing application manifests, while HDMI and SPI output
providers can be swapped without changing window clients.

## Foreground and resident behavior

`background` and `session` components are normal foreground child processes
kept resident by `msysd`; they must not daemonize or write PID files merely to
stay alive. `manual` and `on-demand` components use the same supervision and IPC
identity. Restart policy describes what happens after exit and is orthogonal to
whether the component draws a window.

An `on-demand` component may opt into bounded idle reclamation with
`idle_timeout_ms`. Omission keeps an activated provider resident for
compatibility; the timeout never changes `manual`, `background`, or `session`
residency.

`activation.launchable` is the sole explicit launcher-listing switch.
`windowing.mode` describes how window policy lays the component out. Neither is
inferred from a language or framework.

Visual X11 applications normally declare `windowing.display` as `inherit` and
receive the actual `DISPLAY` from the profile-selected display session. Qt,
Electron, Tk, Python, shell, and native applications therefore use the same
manifest-level binding and do not need an SPI- or HDMI-specific variant.

## Window identity

Window titles are presentation text, not identifiers. They can contain a
document name, notification text, or translated label and may change at any
time. Every visible component declares a stable `windowing.identity.app_id` and
sets the corresponding toolkit metadata. X11-aware apps should set WM_CLASS to
the declared `x11_wm_class`; Electron, Qt, Tk, and native Xlib can all do this.
This lets Back, Home, recents, overlay layout, and close-active policy operate
without guessing from process names or titles.

## Activation and permissions

Launchers activate a component id. Intent dispatchers select among declared
`open-uri`, `open-mime`, settings, or custom intent handlers, then pass typed
arguments over mIPC. A manifest never interpolates an incoming URI into a shell
command. If several handlers match, the replaceable `chooser` role presents the
registry candidates and returns a component id. Remembering that choice is
user state scoped to the intent kind, not a special application format or a
change to either package.

Permissions describe the minimum resources and mIPC operations a component
expects. Brokered mIPC call/event permissions are enforced; filesystem, device,
network, and same-UID X11 declarations remain cooperative/auditable rather than
a hostile-code security boundary. Keeping every declaration accurate permits
later UID/namespace and resource isolation without creating a second app
format.

See [Manifest v1](manifest.md) for exact fields and the normative JSON Schema.
