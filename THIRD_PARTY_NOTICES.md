# Third-party notices

icadkit project material is offered under PolyForm Noncommercial 1.0.0, with separate
commercial licensing available from UnRobotics Inc. Earlier MIT material retains
its permissions; its original notice is in `LICENSES/icadkit-legacy-MIT.txt`.
See `LICENSE` and `COMMERCIAL-LICENSE.md`.

Binary distributions also contain `parasolid-core 0.2.0` (MIT AND Apache-2.0)
and PyO3 (MIT OR Apache-2.0, MIT selected). The source metadata declares
`PolyForm-Noncommercial-1.0.0`; the combined Python distribution declares
`PolyForm-Noncommercial-1.0.0 AND MIT AND Apache-2.0`. These expressions describe
different portions of the package, not a choice to use all icadkit material
under MIT or Apache-2.0. Third-party components are not relicensed.

- `parasolid-core`: Copyright (c) 2026 parasolid-kit contributors.
  See `LICENSES/parasolid-core-MIT.txt` and `LICENSES/Apache-2.0.txt`.
  https://github.com/monozukuri-ai/parasolid-kit
- PyO3 and its transitive Rust dependencies: see the notices and license copies
  under `LICENSES/`. Exact resolved versions are recorded in `Cargo.lock`.
- Resource decoding uses `flate2` with the Rust `miniz_oxide` backend; provenance
  hashing uses `sha2`. Their license copies and transitive dependency notices are
  included in `LICENSES/rust-dependencies.txt` (MIT selected where offered).
- The dependency notices also preserve build/development dependency terms,
  including the LLVM exception and Unicode terms. Their sources are not vendored.
- The eight fixed synthetic cases in `corpus/public.jsonl` retain their earlier
  MIT permissions. Their V30 scalar layouts derive from the MIT/Apache-2.0
  `parasolid-core` profile; see `tests/geometry_fixtures.py` for attribution.

No Siemens/iCAD binaries, external schema catalogs, or sample CAD files are
included in wheel or sdist distributions. Bundled Parasolid profiles are the
project-owned profiles distributed as part of `parasolid-core`.
