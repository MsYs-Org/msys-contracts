# Install And Remote Update Contract

MSYS install/update is self-contained. It does not call systemd, D-Bus, PackageKit,
Snap, Flatpak, or an OS package manager at runtime.

## Local layout

```text
$MSYS_STATE_DIR/
  packages/
    org.example.app/
      versions/
        1.0.0/
          manifest.json
          files/
      current.json
      previous.json
  registry/
    installed.json
    install-transaction.json
  updates/
    incoming/
    staged/
    applied/
```

The installer writes an immutable new version directory, verifies the archive
digest and package content hashes, then atomically replaces `current.json` and
the installed registry with fsync-backed transaction recovery. `msysd` only
reads committed package versions.  A supervised install/update transaction
does not report success until Core has reloaded that committed registry and
the changed critical components are ready.

Uninstall uses the same transaction rather than deleting a version tree. Its
committed state is `current = null`, `previous = old current`; the immutable
`versions/<version>` directory remains available. The existing rollback
operation can therefore restore an uninstalled package by promoting that
previous pointer back to current.

Before the pointer move, Core validates the prospective complete catalog
(built-ins plus installed packages), including dependencies, cycles, roles,
and services.  Between commit and the readiness result the journal is
`health_pending` and rollback-biased.  A reload or readiness failure restores
`current.json`, `previous.json`, and `registry/installed.json`, then reloads the
restored catalog.  Recovery after interruption follows the same idempotent
path before an install agent advertises readiness.

For install/rollback candidates the precommit call is
`msys.core.preflight_registry({package, path})`. For removal it is
`msys.core.preflight_registry_remove({package})`; Core constructs the complete
prospective catalog without that package and rejects broken dependencies,
roles, services, or cycles before `current.json` moves. After either kind of
commit, `msys.core.reload_registry({verify_health: true})` is the postcommit
health gate. An unavailable or incompatible Core fails closed.

## Remote delivery

Remote delivery is pull-based for MVP:

1. `msys-update-agent` fetches a strict repository index over HTTPS or from a
   local file/USB path.
2. It downloads package artifacts into `updates/incoming`.
3. It verifies the artifact SHA-256, complete package content hashes, and the
   declared package identity/version.
4. It stages the package under `updates/staged`.
5. It asks `msys-install` to commit.
6. It emits `msys.install.package_changed`.

Push delivery uploads one regular archive into the private
`updates/staged-rpc` ingress and calls the install-agent RPC with its SHA-256.
Paths outside that ingress, nested paths, symlinks, missing digests, and
incomplete content hashes are rejected; a caller cannot weaken this policy
with `remote: false`.  Pull and push therefore share the same validation,
catalog preflight, commit, health gate, and recovery path.

## Waitable agent RPC

New clients use ordinary mIPC calls rather than treating an event
acknowledgement as transaction completion:

```text
role:update-agent.check_updates({source, package?, allow_downgrade?})
role:update-agent.apply_updates({source, package?, allow_downgrade?})
role:install-agent.install_archive({path, sha256, package?, version?})
role:install-agent.uninstall({package})
role:install-agent.rollback({package})
role:install-agent.registry({})
```

A successful terminal response has schema
`msys.install-agent-result.v1`, the requested `operation`, and `ok`.  Check and
apply responses place their typed plan/summary in `result`.  An apply-all may
return `ok: false` with independent per-package entries in `result.errors`;
that is still a completed RPC response, never a queued acknowledgement.
Install, uninstall, and rollback responses include the committed
package/version/action and `registry_reloaded: true`. An uninstall result
describes the removed current version and retained immutable path with
`action: "uninstall"` and `installed: false`.

Failures are mIPC `error` replies whose payload has schema
`msys.install-agent-error.v1`, the operation, and bounded structured details.
Stable codes include `INSTALL_RPC_BAD_REQUEST`, `INSTALL_RPC_NO_METHOD`,
`INSTALL_RPC_BUSY`, `INSTALL_PACKAGE_INVALID`, `INSTALL_INTEGRITY_FAILED`,
`INSTALL_ROLLBACK_UNAVAILABLE`, `INSTALL_PACKAGE_NOT_INSTALLED`,
`INSTALL_SELF_UNINSTALL_FORBIDDEN`, `INSTALL_CATALOG_PREFLIGHT_FAILED`, and
`INSTALL_COMMIT_HEALTH_FAILED`.

Legacy `msys.update.*` and `msys.install.*` broadcast requests remain a
compatibility surface only.  Their delivery acknowledgement cannot be used as
proof that a health-checked transaction committed; developer tools expose
that path only through an explicit legacy option.

## Package uninstall semantics

`role:install-agent.uninstall` requires a path-safe `package` id. The
agent rejects an absent current pointer with `INSTALL_PACKAGE_NOT_INSTALLED`.
The supervised agent also rejects its own containing package with
`INSTALL_SELF_UNINSTALL_FORBIDDEN`, because stopping the transaction
coordinator would make its terminal health result unknowable.

The state lock covers removal preflight, journal creation, pointer/registry
commit, Core reload and health verification, and any failure restoration. A
`health_pending` removal journal is rollback-biased: recovery restores the old
current and previous pointers, rewrites `installed.json`, asks Core to reload
that restored catalog, and only then removes the journal. A failed recovery
reload leaves the journal for an idempotent retry. No uninstall transaction
removes an immutable version directory.

After a successful typed or compatibility-event uninstall, the agent publishes
`msys.install.package_changed` with the same package, removed version, retained
path, `action: "uninstall"`, `installed: false`, and
`registry_reloaded: true`. Failures publish `msys.install.error` with
`operation: "uninstall"` and the same stable error code/details used by the
typed error reply. The compatibility request topic is
`msys.install.uninstall`; its delivery acknowledgement is not completion.

## Runtime update semantics

`msysd` never executes half-installed software.

Installed Python processes must use `PYTHONDONTWRITEBYTECODE=1`; mode bits are
not sufficient when the supervisor runs as root. During an idempotent repair,
the installer may remove only a complete `__pycache__` subtree for which no
path is declared by `hashes.json`. Other unexpected content, including an
uncovered non-cache file, fails immutable-version verification.

- New on-demand launches use the new committed version.
- Background/session components can be restarted by policy.
- Exclusive role providers switch through the normal role transaction:
  start candidate, wait ready, transfer lease, stop old provider.
- Failed new versions restore the previous current/previous pointers and
  installed registry before the terminal RPC error is returned.

## Package artifact

The MVP artifact is a deterministic tar/zip archive or a local development
directory tree:

```text
manifest.json
files/
hashes.json
```

Remote archives require a complete `hashes.json` and an externally declared
archive SHA-256.  Local operator installs may omit hashes for development, but
any present hash manifest is always verified.  Signature policy, SquashFS, and
delta updates are future extensions, not claims of the v1 implementation.
