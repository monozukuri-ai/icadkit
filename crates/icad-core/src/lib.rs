//! Bounded iCAD SX framing, structural resource indexing and lazy extraction.

mod compression;
mod document;
mod error;
mod header;
mod limits;
mod native;
mod parasolid;
mod parts;
mod provenance;
mod reader;
mod record;
mod resource;
mod schema;

pub use document::Document;
pub use error::{Diagnostic, ErrorKind, InspectError};
pub use header::{
    Header, Inspection, InspectionStatus, Status, UnparsedRange, inspect_bytes, inspect_file,
    inspect_reader,
};
pub use limits::{CatalogLimits, GeometryLimits, InspectionLimits, ReadLimits};
pub use native::{NativeEntityRecord, NativePrimitiveRecord};
pub use parasolid::{
    GeometryDiagnostic, GeometryError, GeometryResult, GeometryStatus, SchemaSelection,
};
pub use parts::{PartIndex, PartLimits, PartOpaqueRange, PartRecord};
pub use provenance::{Extraction, SourceRef};
pub use reader::ByteOrder;
pub use record::{ByteRange, RecordInfo};
pub use resource::{Encoding, ResourceRef};
pub use schema::SchemaCatalog;

use parasolid_core::{BuiltinProfileRegistry, ParseError};

/// Version of this Rust crate, in Cargo SemVer notation.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

/// Exact registry dependency; checked against the manifest and lock in CI.
pub const PARASOLID_CORE_VERSION: &str = "0.3.0";

/// Metadata of the compiled Parasolid backend, not ICD format coverage.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BackendInfo {
    pub version: &'static str,
    pub builtin_profile_ids: Vec<String>,
}

/// Initialize the compiled registry and report its profile identifiers.
///
/// # Errors
/// Returns the backend error if its compiled profile table is inconsistent.
pub fn backend_info() -> Result<BackendInfo, ParseError> {
    let registry = BuiltinProfileRegistry::compiled()?;
    let mut builtin_profile_ids: Vec<_> = registry
        .profiles()
        .map(|profile| profile.metadata().profile_id.clone())
        .collect();
    builtin_profile_ids.sort();
    Ok(BackendInfo {
        version: PARASOLID_CORE_VERSION,
        builtin_profile_ids,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use parasolid_core::SchemaKey;

    #[test]
    fn registry_initializes_and_keeps_exact_key_selection() -> Result<(), ParseError> {
        let info = backend_info()?;
        assert!(!info.builtin_profile_ids.is_empty());
        let registry = BuiltinProfileRegistry::compiled()?;
        let covered = SchemaKey::parse("SCH_3000310_30000_13006")?;
        let uncovered = SchemaKey::parse("SCH_2601246_26105_13006")?;
        assert!(registry.provider_for_key(&covered).is_some());
        assert!(registry.provider_for_key(&uncovered).is_none());
        Ok(())
    }
}
