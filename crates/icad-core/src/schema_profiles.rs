//! Exact legacy iCAD keys qualified for raw parsing. B-Rep roles remain an
//! independent gate in parasolid-core; no key or provider provenance is changed.
use crate::provenance::sha256;
use parasolid_core::{
    BuiltinProfileCoverage, BuiltinProfileMetadata, BuiltinProfileRegistry, BuiltinSchemaProfile,
    ParseError,
};
use std::sync::OnceLock;

pub(crate) const LEGACY_PROFILE: &str = "icad-legacy-13006-raw-r1";
const KEYS: &[&str] = &[
    "SCH_1500137_15003_13006",
    "SCH_1500245_15003_13006",
    "SCH_1700223_16100_13006",
    "SCH_1700256_16100_13006",
    "SCH_1901315_19008_13006",
];

pub(crate) fn registry() -> Result<&'static BuiltinProfileRegistry, ParseError> {
    static REGISTRY: OnceLock<Result<BuiltinProfileRegistry, ParseError>> = OnceLock::new();
    REGISTRY
        .get_or_init(|| {
            let mut registry = BuiltinProfileRegistry::compiled()?.clone();
            let base = parasolid_core::schema::profiles::icad_sch34101_13006()?;
            // Pin public project-owned declarations, not a vendor catalog.
            // The digest covers this qualification code, keys and source hash.
            let mut canonical = include_bytes!("schema_profiles.rs").to_vec();
            canonical.extend_from_slice(base.metadata().profile_sha256.as_bytes());
            registry.insert(BuiltinSchemaProfile::new_embedded(
                BuiltinProfileMetadata {
                    profile_id: LEGACY_PROFILE.into(),
                    revision: 1,
                    provider_schema: "13006".into(),
                    producer_scope: "Legacy iCAD extracted resources; raw nodes only".into(),
                    coverage: BuiltinProfileCoverage::VerifiedSubset,
                    evidence_manifest_sha256: None,
                    profile_sha256: sha256(&canonical),
                },
                KEYS.iter().map(|key| (*key).into()).collect(),
                base.definitions().cloned().collect(),
                base.unsupported_base_types().collect(),
                base.absent_base_types().collect(),
            )?)?;
            Ok(registry)
        })
        .as_ref()
        .map_err(Clone::clone)
}
