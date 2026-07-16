# SceneActor Template Interaction Protocol 1.0

Status: Implemented  
Protocol identifier: `sceneactor-template/1.0`  
Consumer: SceneActor  
Provider: an external approved-template service such as Template Studio

## 1. Purpose

This standard defines the only supported boundary between SceneActor and a trend/template asset system. It lets SceneActor resolve one immutable, approved template version without importing platform collectors, trend rankings, databases, rights workflows, or editorial tooling into the NPC runtime.

The provider decides what is current, approved, licensed, and publishable. SceneActor verifies the returned identity, version, integrity, slot selection, and usage permission, then projects the accepted contract separately to Host, Actor, Review, and Adapter layers.

The protocol is transport-neutral. JSON over HTTP is a conventional deployment, not part of the semantic contract.

## 2. Normative language

`MUST`, `MUST NOT`, `SHOULD`, `SHOULD NOT`, and `MAY` are normative.

## 3. Trust boundary

### 3.1 Provider responsibilities

The provider MUST:

- publish only versioned template contracts;
- resolve an exact requested version, never silently substitute `latest`;
- maintain source, rights, expiry, and platform-policy records outside SceneActor;
- validate slot choices and fill declared defaults before responding;
- return a canonical SHA-256 content hash;
- mark restricted, expired, blocked, or unavailable assets with an error response;
- keep raw trend metrics, ranking signals, crawling metadata, editorial notes, and private platform credentials outside the contract.

### 3.2 SceneActor responsibilities

SceneActor MUST:

- request an exact `template_id` and `version`;
- reject request-ID, protocol-version, template-identity, or content-hash mismatches;
- re-check the usage grant and resolved slots;
- freeze `template_id`, `version`, `content_hash`, and `resolution_hash` into downstream evidence;
- expose only the Actor projection to actor-visible cognition;
- keep emotional payoff, mother formula, forbidden drift, relationship functions, rights state, and review objectives out of actor prompts;
- keep asset URIs and temporal anchors in the Adapter layer;
- preserve prior accepted versions for replay instead of re-resolving them as newer versions.

## 4. Protocol objects

### 4.1 `TemplateResolveRequest`

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `protocol_version` | string | yes | Exactly `sceneactor-template/1.0` |
| `request_id` | string | yes | Caller-generated correlation key; it is not a durable idempotency snapshot |
| `template_id` | string | yes | Stable provider template identity |
| `version` | string | yes | Exact opaque version; `latest` MUST be rejected |
| `selected_slots` | object<string,string> | yes | Requested slot values; slots with declared defaults MAY be omitted |
| `usage_context` | enum | yes | `research`, `noncommercial`, or `commercial` |
| `consumer` | string | no | Defaults to `sceneactor` when omitted; an explicit other consumer requires deployment registration |

Selected slot values are opaque string references in 1.0. Rich character, scene, or asset data remains in its owning registry and is joined by the authoring layer.

`request_id`, `template_id`, `version`, `consumer`, slot IDs, asset IDs, anchor IDs, rights references, and asset versions use the ASCII alphabet `A-Z a-z 0-9 - . _ : / @ +`, are non-empty, and compare byte-for-byte after JSON decoding. Version strings are opaque; SemVer is recommended but not required. Slot values are opaque non-empty Unicode strings and compare exactly. Reusing a request ID does not freeze provider state; transport profiles MAY add an idempotency store.

Example:

```json
{
  "protocol_version": "sceneactor-template/1.0",
  "request_id": "req-20260715-001",
  "template_id": "relationship.daily-discovery",
  "version": "1.2.0",
  "selected_slots": {
    "lead": "character:dog-001@4",
    "scene": "scene:night-market@2",
    "task_or_prop": "prop:broken-speaker@1",
    "ending": "ending:caretaker-pays@1"
  },
  "usage_context": "commercial",
  "consumer": "sceneactor"
}
```

### 4.2 `TemplatePerformanceContract`

The contract is an immutable authoring asset. Its required semantic fields are:

| Group | Fields |
| --- | --- |
| Identity | `protocol_version`, `template_id`, `version`, `content_hash` |
| Formula | `mother_formula`, `emotional_core`, `reality_mode`, `performance_mode` |
| Public world | `public_rules` |
| Recognition anchors | `fixed_anchors.visual_asset_ids`, `fixed_anchors.temporal`, `fixed_anchors.narrative_rules` |
| Open variables | `slots` |
| Series relations | `relationship_roles` |
| Review-only goals | `payoff_condition`, `allowed_variation`, `forbidden_drift` |
| Presentation | `reaction_target`, `assets` |
| Governance | `rights` |

All fields in the following wire grammar are present. String fields and arrays not required for a specific template use `""` and `[]`, not `null`.

| Field | JSON type | Constraints |
| --- | --- | --- |
| `protocol_version` | string | Exactly `sceneactor-template/1.0` |
| `template_id`, `version` | string | Protocol identifiers; exact and immutable |
| `content_hash` | string | 64 lowercase hexadecimal SHA-256 characters |
| `mother_formula`, `emotional_core` | string | Non-empty review/authoring text |
| `reality_mode`, `performance_mode` | enum string | Values defined below |
| `public_rules` | array<string> | Every item is explicitly certified as scene-knowable and Actor-safe |
| `fixed_anchors` | object | Shape defined in 4.5 |
| `slots` | array<object> | Unique `slot_id`; shape defined in 4.3 |
| `relationship_roles` | array<object> | `{character_slot,function,stable_relation}`; character slot must exist |
| `payoff_condition`, `reaction_target` | string | Review-only and Adapter-only respectively; empty allowed |
| `allowed_variation`, `forbidden_drift` | array<string> | Review-only |
| `assets` | array<object> | Unique `asset_id`; shape defined in 4.4 |
| `rights` | object | Exactly one contract-level grant; shape defined in 4.6 |

Allowed `performance_mode` values:

- `actor_led`: NPC choice drives the beat;
- `anchor_led`: fixed action/audio timing drives the presentation and actor cognition MUST NOT rewrite it;
- `hybrid`: fixed anchors are preserved while NPC-specific reaction and local action remain open.

Allowed `reality_mode` values:

- `realistic`;
- `heightened`;
- `absurd_but_consistent`.

`absurd_but_consistent` permits a declared local rule to differ from ordinary reality. It does not permit a character to violate knowledge, body, tool, ownership, or authority boundaries that the contract did not explicitly change.

### 4.3 Slot definitions

Each slot is an array element with this exact shape:

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `slot_id` | string | yes | Unique protocol identifier |
| `kind` | enum string | yes | `character`, `scene`, `task_or_prop`, or `ending` |
| `description` | string | yes | Non-empty authoring description |
| `required` | boolean | yes | Whether a value must exist after default resolution |
| `visibility` | enum string | yes | `public`, `adapter`, or `review` |
| `allowed_values` | array<string> | yes | Empty means provider-defined open values |
| `default_value` | string | yes | Empty means no default |

`slots` is an array; resolved values are never inserted into these definitions. They are returned separately in response `selected_slots`. Unknown slots MUST be rejected. A caller MAY omit any slot with a non-empty default, including a required slot. After applying defaults, every required slot MUST exist. The provider MUST return every applied default as a concrete response value; SceneActor rejects unresolved defaults.

### 4.4 Asset references

Assets are immutable references, not inline media:

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `asset_id`, `version` | string | yes | Protocol identifiers |
| `kind` | enum string | yes | `character`, `visual`, `audio`, `video`, `action`, `prop`, or `scene` |
| `content_hash` | string | yes | 64 lowercase hexadecimal SHA-256 characters |
| `uri` | string | yes | Empty or deployment-resolvable URI; credentials MUST NOT be embedded |
| `rights_ref` | string | yes | MUST equal the single contract `rights.rights_ref` in 1.0 |

The contract MUST list every asset referenced by visual or temporal anchors. `asset_id` is unique within the contract. SceneActor validates identity, hash shape, and rights-reference equality; media download, authorization tokens, and storage credentials are Adapter/deployment concerns.
### 4.5 Temporal anchors

A temporal anchor is an array element with this exact shape:

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `anchor_id` | string | yes | Unique protocol identifier |
| `kind` | enum string | yes | `audio_cue`, `action_keyframe`, `pause`, `reaction`, or `loop` |
| `order` | non-negative integer | yes | Unique and strictly increasing array order |
| `instruction` | string | yes | Non-empty presentation instruction |
| `soft_time_hint` | string | yes | Empty or provider-neutral human-readable timing hint; no fixed unit in 1.0 |
| `asset_id` | string | yes | Empty for assetless pause/reaction anchors; otherwise must resolve in `assets` |
| `required` | boolean | yes | Whether an Adapter may omit the anchor |

`fixed_anchors` has the exact shape `{visual_asset_ids: array<string>, temporal: array<TemporalAnchor>, narrative_rules: array<string>}`. Visual IDs must resolve in `assets`. Temporal anchors are Presentation constraints, not NPC motives, and MUST NOT enter actor-visible cognition.
### 4.6 Rights grant

A contract contains exactly one rights grant:

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `rights_ref` | string | yes | Protocol identifier shared by every asset |
| `status` | enum string | yes | `approved`, `approved_noncommercial`, `research_only`, `restricted`, `expired`, or `blocked` |
| `allowed_usage` | array<enum string> | yes | Set drawn from `research`, `noncommercial`, `commercial` |
| `valid_until` | string | yes | Empty or RFC 3339 UTC timestamp ending in `Z` |
| `source_refs` | array<string> | yes | Provider-owned evidence references, never credentials |

Permission is the intersection of `status` and `allowed_usage`: `approved` permits any listed usage; `approved_noncommercial` permits only listed `research` or `noncommercial`; `research_only` permits only listed `research`; all other states deny every usage. The provider is authoritative for current-time expiry evaluation in 1.0. Before returning `ok`, it MUST convert an elapsed `valid_until` record to a non-allowing state. SceneActor re-checks status and requested usage but does not evaluate wall-clock expiry.
## 5. Response and error model

### 5.1 Success

Every response, including errors, uses this exact envelope:

| Field | Type | Success | Error |
| --- | --- | --- | --- |
| `protocol_version` | string | echoed | echoed when the request was parseable |
| `request_id` | string | echoed | echoed when the request was parseable |
| `status` | enum string | `ok` | one standard error status |
| `selected_slots` | object<string,string> | complete resolved map including defaults | `{}` |
| `contract` | object or null | sealed contract object | `null` |
| `fault` | object or null | `null` | `{code,message,retryable,details}` |

Success example:

```json
{
  "protocol_version": "sceneactor-template/1.0",
  "request_id": "req-20260715-001",
  "status": "ok",
  "selected_slots": {"lead": "character:dog-001@4", "scene": "scene:night-market@2"},
  "contract": {"protocol_version": "sceneactor-template/1.0", "template_id": "relationship.daily-discovery", "version": "1.2.0", "content_hash": "<64 lowercase hex>", "...": "remaining fields from section 4"},
  "fault": null
}
```

`sealed` in 1.0 means the contract contains a verified canonical `content_hash` and is treated as immutable. It does not imply a digital signature. Transport profiles MAY add signatures without changing the semantic contract.

### 5.2 Errors

A non-`ok` response contains `contract: null`, `selected_slots: {}`, and one fault. Fault `details` is an extension bag, not a contract object: it MUST be an object with string keys and values limited to strings, booleans, integers, arrays of those scalar values, or nested objects following the same rule. Consumers MUST preserve but need not interpret unknown keys inside this bag; unknown fields outside `details` are rejected. It MUST NOT contain credentials or raw private provider data.

Malformed JSON or a request whose top-level JSON type is not an object cannot carry a reliable response `request_id`. A transport profile MUST return HTTP 400 (or its equivalent) and a machine-readable transport error; it MUST NOT invent a semantic `TemplateResolveResponse`. Once a top-level object is parseable, missing or invalid protocol fields MUST return the standard envelope with `status: protocol_error`, echoing `request_id` only when it is a valid parsed identifier, and with no contract.

```json
{
  "protocol_version": "sceneactor-template/1.0",
  "request_id": "req-20260715-001",
  "status": "invalid_slots",
  "selected_slots": {},
  "contract": null,
  "fault": {"code": "invalid_slots", "message": "required slot is missing: lead", "retryable": false, "details": {}}
}
``` 

Standard response statuses:

| Status | Retry | Meaning |
| --- | --- | --- |
| `not_found` | no | Template identity does not exist |
| `version_conflict` | no | Template exists, requested exact version does not |
| `rights_restricted` | no | Requested usage is not permitted |
| `invalid_slots` | no | Unknown, missing, or disallowed slot selection |
| `protocol_error` | no | Malformed or incompatible exchange |
| `unavailable` | yes | Temporary provider or storage failure |

SceneActor raises `TemplateProtocolError` for every non-`ok` response. It MUST NOT fabricate an empty template or silently remove required anchors.

### 5.3 Validation precedence

Providers select one fault in this order: JSON/envelope syntax and protocol version; template identity; exact version; current rights/expiry; selected slots; asset/anchor availability. Consumers validate an `ok` response in this order: envelope and request ID; returned identity/version; content hash; rights for requested usage; resolved slots and unchanged requested values; asset/anchor references. A higher-precedence failure hides lower-precedence details.

## 6. Canonical integrity

`content_hash` is lowercase SHA-256 over canonical UTF-8 JSON of the contract excluding the `content_hash` field itself. The 1.0 canonical domain contains objects with string keys, arrays, strings, booleans, and integers only; `null` and floating-point values are forbidden in hashed payloads. Strings are normalized to Unicode NFC. Dynamic object keys are protocol ASCII identifiers, duplicate keys (including NFC-colliding keys) are rejected, keys are sorted, integers use base-10 JSON form, booleans use lowercase JSON form, arrays retain order, and no insignificant whitespace is emitted. JSON strings use RFC 8259 escaping and UTF-8 without ASCII-only escaping.

`content_hash` is computed after removing the `content_hash` member from the parsed contract object (the member MUST be absent from the hash input), validating the remaining closed schema, NFC-normalizing strings, and applying the canonical rules below. The provider returns ordinary JSON; the consumer parses it, rejects unknown contract fields, performs the same canonicalization locally, and compares the resulting digest. Replay stores the canonicalized contract object plus its hash, not an arbitrary pre-parser byte stream.

```json
{
  "protocol_version": "sceneactor-template/1.0",
  "template_hash": "<content_hash>",
  "selected_slots": {"<slot>": "<resolved-value>"},
  "usage_context": "<usage>"
}
```

`template_hash` MUST first pass the 64-character lowercase hexadecimal check. `selected_slots` uses the same canonical object-key ordering and Unicode NFC value normalization; no response-envelope or other contract fields enter the resolution hash.


### 6.1 Canonical and wire exactness

Protocol 1.0 rejects unknown top-level and nested fields in all contract, request, and response objects; providers and consumers MUST fail with `protocol_error` rather than include unknown fields in a hash. The canonical implementation domain excludes `null` and floating-point values from hashed contract objects. JSON strings use UTF-8, NFC normalization, no ASCII-only escaping, no solidus escaping, and RFC 8259 escaping for control characters, quotation marks, backslashes, and valid surrogate pairs; unpaired surrogates are rejected. Integer values are signed base-10 JSON integers; `-0` is not accepted as a distinct value. `selected_slots` may omit optional slots with no default; such slots remain absent from the resolved map. Every returned slot key MUST be declared.

`consumer` may be omitted only by a transport decoder that supplies the default `sceneactor`; an explicit other consumer is allowed only when the deployment has registered it. `relationship_roles` has the exact object shape `{character_slot:string,function:string,stable_relation:string}` and all three fields are non-empty; matching is byte-exact after NFC normalization. `allowed_values` uses exact string equality after NFC normalization and rejects duplicates.

## 7. Layer-safe projections

The accepted response is never sent wholesale to a model.

### 7.1 Host projection

Receives:

- immutable template binding;
- declared public world rules;
- selected `public` slot values.

It does not receive emotional payoff, trend state, rights workflow, or forbidden-drift notes.

### 7.2 Actor projection

Receives only:

- `public_rules` that are actually knowable in the scene;
- selected `public` slot values.

It MUST NOT receive:

- `mother_formula`;
- `emotional_core`;
- `payoff_condition`;
- `relationship_roles` as author functions;
- `allowed_variation` or `forbidden_drift`;
- rights, provenance, trend metrics, popularity, or platform strategy;
- temporal anchors or reaction targets.

Actor goals and private relationship understanding continue to come from SceneActor authoring/runtime state, not directly from the template registry.

### 7.3 Review projection

Receives the full authoring intent required to verify recognition, payoff, allowed variation, drift, rights state, and relationship function. This projection remains outside actor cognition.

### 7.4 Adapter projection

Receives:

- template binding;
- performance mode;
- `public` and `adapter` slot values;
- approved asset references;
- visual and temporal anchors;
- reaction target.

It does not receive trend metrics or editorial selection rationale.

## 8. Replay and version evolution

- A replay record MUST retain the accepted canonical contract JSON, `selected_slots`, `usage_context`, `content_hash`, and `resolution_hash`; hashes alone are insufficient to reconstruct projections.
- Replay verifies the recorded hashes and uses the recorded bytes without contacting the provider or re-evaluating current rights. Replay permission does not authorize a new publication.
- New resolution requests are evaluated against current provider rights and expiry state, so the same request may later be denied. `request_id` is correlation only; deterministic idempotency across changing provider state is not promised by 1.0.
- A provider MUST NOT mutate bytes behind an existing template version. Any semantic contract change requires a new version and content hash.
- SceneActor MUST reject unknown major protocol versions.
- Additive optional fields require a minor protocol revision only when 1.0 readers can safely ignore them. Changed hashing, visibility, rights, slot, or projection semantics require a new major protocol version.

## 9. Security and privacy

The exchange MUST NOT contain:

- platform access tokens or cookies;
- raw creator private data;
- undisclosed ranking or moderation signals;
- unrestricted source media bytes;
- model chain-of-thought or editorial rationale;
- NPC private state from another scene or user;
- a direct instruction for a named actor to say a line solely to force template payoff.

Transport authentication, authorization, rate limiting, request signing, and URI credential exchange are deployment profiles layered above this semantic protocol.

## 10. Conformance

A conforming provider MUST pass:

1. exact-version resolution and explicit rejection of `latest`;
2. not-found and version-conflict separation;
3. rights denial for every unapproved status/usage intersection and elapsed expiry;
4. required, unknown, default, and allowlisted slot cases;
5. content-hash tamper and malformed-hash rejection;
6. request-ID mismatch rejection;
7. asset, rights-reference, anchor-reference, and anchor-order validation;
8. validation-precedence behavior and explicit rejection of unknown fields.

A conforming SceneActor consumer MUST pass:

1. success resolution and resolution-hash stability;
2. all response error mappings;
3. Actor projection privacy checks;
4. Review and Adapter projection completeness checks;
5. replay binding checks;
6. absence of raw trend, rights, payoff, and implementation data from actor prompts.

## 11. Repository boundary

This repository owns:

- protocol contracts and validators;
- `TemplateProvider` interface;
- deterministic fixture provider;
- layer-safe projections;
- protocol conformance tests;
- future compilation of accepted projections into SceneActor authoring inputs.

This repository does not own:

- platform crawlers and API collectors;
- trend databases or ranking;
- vector clustering;
- editorial approval UI;
- media storage;
- rights adjudication;
- Remix lineage, publishing, analytics, or revenue attribution.
