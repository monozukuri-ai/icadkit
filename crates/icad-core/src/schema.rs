use crate::provenance::sha256;
use crate::{CatalogLimits, ErrorKind, InspectError};
use parasolid_core::{InMemorySchemaProvider, SchemaCatalogLimits, parse_schema_catalog};
use std::fs::File;
use std::io::Read;
use std::path::Path;

/// A validated immutable snapshot of one explicitly supplied catalog. Loading
/// does not register global providers, inspect the environment, or search paths.
#[derive(Debug)]
pub struct SchemaCatalog {
    schema_id: String,
    sha256: String,
    modeller_version: String,
    definition_count: usize,
    provider: InMemorySchemaProvider,
}

impl SchemaCatalog {
    pub fn schema_id(&self) -> &str {
        &self.schema_id
    }
    pub fn sha256(&self) -> &str {
        &self.sha256
    }
    pub fn modeller_version(&self) -> &str {
        &self.modeller_version
    }
    pub fn definition_count(&self) -> usize {
        self.definition_count
    }
    pub(crate) fn provider(&self) -> &InMemorySchemaProvider {
        &self.provider
    }

    pub fn from_file(
        path: impl AsRef<Path>,
        expected_id: &str,
        expected_sha256: Option<&str>,
        limits: CatalogLimits,
    ) -> Result<Self, InspectError> {
        limits.validate()?;
        let path = path.as_ref();
        let check = |metadata: std::fs::Metadata| -> Result<(), InspectError> {
            if !metadata.is_file() {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::InvalidInput,
                    "catalog requires a regular file",
                )
                .into());
            }
            if metadata.len() > limits.max_file_bytes as u64 {
                return Err(InspectError::format(
                    ErrorKind::LimitExceeded,
                    "limit.catalog_bytes",
                    0,
                    "catalog exceeds its byte limit",
                ));
            }
            Ok(())
        };
        check(std::fs::metadata(path)?)?;
        let file = File::open(path)?;
        check(file.metadata()?)?;
        let mut bytes = Vec::new();
        file.take((limits.max_file_bytes as u64).saturating_add(1))
            .read_to_end(&mut bytes)?;
        Self::from_bytes(&bytes, expected_id, expected_sha256, limits)
    }

    pub fn from_bytes(
        data: &[u8],
        expected_id: &str,
        expected_sha256: Option<&str>,
        limits: CatalogLimits,
    ) -> Result<Self, InspectError> {
        limits.validate()?;
        if data.len() > limits.max_file_bytes {
            return Err(InspectError::format(
                ErrorKind::LimitExceeded,
                "limit.catalog_bytes",
                0,
                "catalog exceeds its byte limit",
            ));
        }
        if expected_id.is_empty() || !expected_id.bytes().all(|b| b.is_ascii_digit()) {
            return Err(InspectError::format(
                ErrorKind::Invalid,
                "schema.invalid_expected_id",
                0,
                "expected_id must be a nonempty ASCII numeric ID",
            ));
        }
        let hash = sha256(data);
        if expected_sha256.is_some_and(|expected| expected != hash) {
            return Err(InspectError::format(
                ErrorKind::Invalid,
                "schema.hash_mismatch",
                0,
                "catalog SHA-256 differs from the requested hash",
            ));
        }
        let parsed = parse_schema_catalog(
            data,
            SchemaCatalogLimits {
                max_file_size: limits.max_file_bytes,
                max_schema_types: limits.max_schema_types,
                max_fields_per_type: limits.max_fields_per_type,
                max_string_bytes: limits.max_string_bytes,
            },
        )
        .map_err(|e| {
            InspectError::format(
                crate::parasolid::category(e.kind()),
                e.kind().code(),
                e.offset() as u64,
                e.message(),
            )
        })?;
        if parsed.schema_id != expected_id {
            return Err(InspectError::format(
                ErrorKind::Invalid,
                "schema.id_mismatch",
                0,
                format!("expected catalog {expected_id}, found {}", parsed.schema_id),
            ));
        }
        let mut provider = InMemorySchemaProvider::new();
        provider.add_schema(&parsed.schema_id);
        let definition_count = parsed.definitions.len();
        for definition in parsed.definitions {
            provider.insert(&parsed.schema_id, definition);
        }
        Ok(Self {
            schema_id: parsed.schema_id,
            sha256: hash,
            modeller_version: parsed.modeller_version,
            definition_count,
            provider,
        })
    }
}
