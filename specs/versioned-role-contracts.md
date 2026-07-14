# MSYS Versioned Role Contracts v1

MSYS role names originally answered only “who owns this job?”. They did not
answer which methods, payload shapes, events, lifecycle artifacts, or error
codes a replacement provider implements. `msys.role-contract.v1` closes that
gap without binding a provider to Python, Qt, Electron, Tk, C/C++, systemd, or
D-Bus.

The normative descriptor meta-schema is
[`schemas/role-contract.v1.schema.json`](../schemas/role-contract.v1.schema.json).
The repository's zero-dependency validator is also normative for semantic
rules which ordinary JSON Schema cannot express, such as matching the role and
target, matching `.vN` to the semantic-version major, resolving local payload
types, and checking conformance cases against method/error declarations.

## Baseline catalog

The first catalog contains:

| Role | Contract | Interaction |
| --- | --- | --- |
| `launcher` | `org.msys.role.launcher.v1@1.0.0` | mIPC preferences plus Core-owned launch lifecycle |
| `window-manager` | `org.msys.role.window-manager.v1@1.0.0` | mIPC navigation, window handles/actions, and layout |
| `navigation-bar` | `org.msys.role.navigation-bar.v1@1.0.0` | mIPC-supervised presentation job with typed outbound navigation calls |
| `display-output` | `org.msys.role.display-output.v1@1.0.0` | supervisor lifecycle plus atomic display-session readiness document |
| `hal-manager` | `org.msys.role.hal-manager.v1@1.0.0` | mIPC HAL manager API and revisioned events |

The JSON files under [`contracts/roles`](../contracts/roles) are the
machine-readable source of method and payload detail. Prose role documents
explain policy and rationale; they do not override a descriptor's wire shape.

## Descriptor structure

Every descriptor is strict JSON. Duplicate object members, unknown unprefixed
fields, non-finite numbers, invalid references, duplicate methods/cases, and
undeclared errors are rejected.

Top-level fields are:

| Field | Meaning |
| --- | --- |
| `schema` | Always `msys.role-contract.v1`. |
| `id` | Stable reverse-DNS identifier ending in `.vN`; `N` is the wire major. |
| `role` / `target` | Role lease name and exact `role:<name>` mIPC address. |
| `version` | Exact published descriptor revision. |
| `status` | `experimental`, `stable`, or `deprecated`. |
| `channel` | `mipc` or `supervisor-lifecycle`. |
| `provider` | Static manifest requirements: exclusivity, readiness, lifecycle, windowing, interfaces, and capabilities. |
| `types` | Reusable bounded JSON value schemas referenced as `#/types/<name>`. |
| `methods` | Required/optional method names, mutability, replay safety, request/response schemas, errors, and probes. |
| `events` | Concrete publish/subscribe topics and payloads. |
| `artifacts` | Non-RPC lifecycle output such as an atomic readiness document. |
| `dependencies` | Typed outbound mIPC targets needed to perform the role; these are not authority grants by themselves. |
| `errors` | Stable error codes and retry guidance. |

`idempotent` describes whether a broker may retry after a liveness failure
*before delivery is known*. It does not make a mutating operation read-only.
`mutates` controls conformance safety: a default probe run never executes a
case for a mutating method.

`required` on a method means every provider claiming that exact contract
revision implements it. An optional method must still obey its complete shape
if implemented. An empty method list is valid. In particular, a navigation bar
is a supervised UI job which calls `role:window-manager`; it is not required to
invent inbound RPC. A display provider uses a supervisor-observed readiness
document and is not required to retain an mIPC control channel.

## Payload schema vocabulary

Request, response, event, and artifact payloads use a deliberately bounded
subset of JSON Schema Draft 2020-12. Supported keywords are:

```text
$ref, $comment, description, type, properties, required,
additionalProperties, items, enum, const, oneOf, anyOf, allOf,
minItems, maxItems, uniqueItems, minLength, maxLength, pattern,
minimum, maximum, minProperties, maxProperties
```

References are local and have exactly the form `#/types/<name>`. External
references, executable format hooks, conditionals, pattern properties, schema
network access, and implementation-defined coercion are deliberately absent.
Integers never accept JSON booleans; numbers must be finite. Regular-expression
`pattern` uses search semantics, matching JSON Schema. A provider must not
coerce an invalid wire type merely because its implementation language makes
that easy.

Unknown `x-...` descriptor fields are reserved for experiments. A conforming
consumer ignores an extension it does not understand. An extension cannot
weaken a base constraint or redefine a required method.

## Versioning and compatibility

The contract id freezes its major version:

- major: incompatible method, payload, error, artifact, or behavioral change;
- minor: backward-compatible addition, normally an optional method/field or a
  new conformance case for existing behavior;
- patch: clarification or a constraint correction which does not change valid
  wire messages.

Every `(id, version)` pair is an immutable published revision. Do not overwrite
`1.0.0` with new meaning. Add another descriptor revision and keep old revisions
while installed providers claim them. A provider must claim an exact published
revision; `>=1`, ranges, “latest”, and unregistered versions are invalid. This
removes ambiguous feature negotiation from a small embedded runtime.

A new required method or newly forbidden previously-valid value is a major
change. A minor descriptor may add an optional method or optional response
field only where `additionalProperties` already permits compatible readers.
Whether a change is source-compatible with one SDK is irrelevant; the JSON
wire behavior decides compatibility.

## Manifest provider claim

Manifest v1 already reserves `x-...` extension fields. A role provider opts
into a published contract on the corresponding `provides` item:

```json
{
  "role": "launcher",
  "exclusive": true,
  "priority": 50,
  "x-msys-contract": {
    "id": "org.msys.role.launcher.v1",
    "version": "1.0.0"
  }
}
```

The claim is descriptive and auditable; it does not grant a role lease or an
mIPC permission. Core can ignore the extension during staged adoption. The
contract checker verifies:

- exact published id and version;
- claim role and descriptor role agree;
- `exclusive`, readiness, lifecycle, and windowing declarations agree;
- required interface and capability declarations exist on the component.

It cannot prove behavior from a manifest. A package/update pipeline should run
both the static claim check and the behavioral provider suite before signing a
release. During migration only, `--allow-unversioned` accepts a known role with
no claim; malformed or unknown claims are never downgraded to warnings.

See the complete language-neutral example at
[`examples/providers/role-provider-manifest.json`](../examples/providers/role-provider-manifest.json).

## Behavioral conformance adapter

The runner talks JSON Lines to an adapter over stdin/stdout. This small adapter
is test code, not a production transport and not a new broker. It can call a
provider's dispatch function in-process, wrap a native test binary, or forward
to a separately launched development mIPC endpoint. The role provider itself
remains framework- and language-neutral.

The runner first writes:

```json
{"schema":"msys.provider-conformance.v1","id":0,"op":"describe"}
```

The adapter must claim the exact revision:

```json
{
  "schema": "msys.provider-conformance.v1",
  "id": 0,
  "type": "describe",
  "contracts": [
    {"id": "org.msys.role.launcher.v1", "version": "1.0.0"}
  ]
}
```

For each enabled case the runner writes one call:

```json
{
  "schema": "msys.provider-conformance.v1",
  "id": 1,
  "op": "call",
  "contract": {"id": "org.msys.role.launcher.v1", "version": "1.0.0"},
  "case": "read-preferences",
  "target": "role:launcher",
  "method": "get_preferences",
  "payload": {}
}
```

The adapter returns an mIPC-shaped result with the adapter schema and matching
id:

```json
{"schema":"msys.provider-conformance.v1","id":1,"type":"return","payload":{}}
```

or:

```json
{"schema":"msys.provider-conformance.v1","id":1,"type":"error","code":"BAD_REQUEST","message":"payload must be an object"}
```

Stdout is protocol-only; diagnostics go to stderr. Responses are bounded by
the runner timeout. Duplicate JSON members, malformed UTF-8/JSON, extra log
lines, wrong ids, missing exact claims, unexpected error codes, and invalid
return payloads fail the suite. The adapter process is kept alive for the
suite, so ordered mutating cases may test provider-owned state.

Safe cases run by default. `--include-mutating` is an explicit authorization
to execute cases which may alter provider state or presentation. A descriptor
must never label a mutating method's case safe. Conformance probes do not grant
filesystem, device, process, or mIPC authority; a live adapter still operates
with its own ordinary permissions.

The included
[`launcher_adapter.py`](../examples/conformance/launcher_adapter.py) is a fully
working protocol example, not a production launcher.

## Commands

From the `msys-contracts` repository, with Python 3 and no third-party package:

```sh
python3 -m tools.contract_tool validate

python3 -m tools.contract_tool manifest \
  examples/providers/role-provider-manifest.json

python3 -m tools.contract_tool run \
  contracts/roles/launcher.v1.json -- \
  python3 examples/conformance/launcher_adapter.py

python3 -m tools.contract_tool run \
  contracts/roles/launcher.v1.json --include-mutating -- \
  python3 examples/conformance/launcher_adapter.py

python3 -m unittest discover -s tests -v
```

`validate` accepts individual descriptor files or directories. `manifest`
accepts `--contracts <directory>` for a packaged catalog. `run` accepts
`--timeout-ms`; it passes adapter arguments unchanged after `--` and never uses
a shell.

## Conformance levels and limits

“Descriptor-valid” means only that the contract is structurally coherent.
“Declaration-conformant” means the static manifest claim matches it.
“Behavior-conformant” means the selected adapter passed the descriptor's cases
in the requested safe/mutating mode. Tools must report these levels separately.

No finite suite proves arbitrary native code safe or proves every possible
payload. mIPC ACLs, package containment, process isolation, device policy, and
release signing remain separate boundaries. The conformance foundation catches
wire incompatibility and false provider declarations; it is not a security
sandbox or a replacement for provider-specific tests.
