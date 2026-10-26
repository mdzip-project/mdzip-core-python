# Upstream requests

## Baseline fixtures and conditional extension profiles

Requested 2026-10-25; scope revised to separate mandatory operations from extras.
Owner: `mdzip-spec`. Request: https://github.com/mdzip-project/mdzip-spec/issues/9

- Dependency: a versioned baseline R1-R7 contract/fixture schema and sync path is
  required for shared baseline acceptance and CI adapters. Local implementation
  and regression tests can proceed before the shared suite lands.
- Required API mapping/directory inspection: [#2](https://github.com/mdzip-project/mdzip-core-python/issues/2). Image analysis [#1](https://github.com/mdzip-project/mdzip-core-python/issues/1) and workspace model [#3](https://github.com/mdzip-project/mdzip-core-python/issues/3) are optional proposals deferred until a concrete consumer needs them.
- Optional profiles are conditional on advertised capabilities. Unsupported is
  not applicable, not failed baseline parity. Do not wait for all libraries to
  implement all extensions; do not silently skip failing advertised behavior.
- Do not block baseline completion on image/workspace/asset-presentation suites that Python does not advertise. Retain existing resource protections; test a resource profile against the policy Python actually supports.
- Reference: hub `.github/docs/core-contract.md` (draft 0.1) and
  `.github/docs/core-parity.md`. Format spec requirements remain separate from
  first-party library operations and optional extension contracts.

Close the baseline request when the shared baseline adapter is running in CI.
Optional profile adoption follows its own supported capability/consumer work;
it is not a universal completion prerequisite.
