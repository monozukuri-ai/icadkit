use crate::{
    ByteRange, Document, ErrorKind, GeometryLimits, InspectError, ResourceRef, SchemaCatalog,
    SourceRef, Status,
};
use parasolid_core::brep::{BrepModel, map_xb_brep_with_diagnostic_limit};
use parasolid_core::{
    BuiltinProfileRegistry, ErrorDetails, ParseError, SchemaKey, SchemaProviderResolution,
    XbDocument, inspect_xb, parse_xb,
};
use std::collections::BTreeMap;
use std::fmt;

/// Coordinates in a geometry diagnostic never mix compressed and decoded bytes.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GeometryDiagnostic {
    pub category: ErrorKind,
    pub code: &'static str,
    pub message: String,
    pub scope: &'static str,
    pub resource_id: String,
    pub container_range: ByteRange,
    pub byte_offset: Option<u64>,
    pub decoded_offset: Option<u64>,
    pub backend_code: Option<&'static str>,
    pub node_type: Option<u16>,
    pub node_index: Option<u32>,
}

#[derive(Debug)]
pub enum GeometryError {
    Input(InspectError),
    Limit(Box<GeometryDiagnostic>),
}
impl fmt::Display for GeometryError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Input(e) => e.fmt(f),
            Self::Limit(d) => write!(f, "{}: {}", d.code, d.message),
        }
    }
}
impl std::error::Error for GeometryError {}
impl From<InspectError> for GeometryError {
    fn from(value: InspectError) -> Self {
        Self::Input(value)
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SchemaSelection {
    pub kind: &'static str,
    pub schema_key: String,
    pub provider_schema: String,
    pub catalog_sha256: Option<String>,
    pub profile_id: Option<String>,
    pub profile_revision: Option<u32>,
    pub profile_sha256: Option<String>,
    pub coverage: Option<&'static str>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GeometryStatus {
    pub extraction: Status,
    pub raw_geometry: Status,
    pub brep: Status,
    pub topology: Status,
}

/// Owns all parsed data. No reference to the source Document or catalog is kept.
/// Raw bytes are retained by XbDocument; successful mapping adds owned B-Rep data.
#[derive(Debug)]
pub struct GeometryResult {
    pub(crate) resource_id: String,
    pub(crate) source_sha256: String,
    pub(crate) source: Option<SourceRef>,
    pub(crate) schema: Option<SchemaSelection>,
    pub(crate) status: GeometryStatus,
    pub(crate) diagnostics: Vec<GeometryDiagnostic>,
    raw: Option<XbDocument>,
    brep: Option<BrepModel>,
    node_positions: BTreeMap<u32, usize>,
}

impl GeometryResult {
    pub fn resource_id(&self) -> &str {
        &self.resource_id
    }
    pub fn source_sha256(&self) -> &str {
        &self.source_sha256
    }
    pub fn source(&self) -> Option<&SourceRef> {
        self.source.as_ref()
    }
    pub fn schema(&self) -> Option<&SchemaSelection> {
        self.schema.as_ref()
    }
    pub fn status(&self) -> &GeometryStatus {
        &self.status
    }
    pub fn diagnostics(&self) -> &[GeometryDiagnostic] {
        &self.diagnostics
    }
    pub fn raw(&self) -> Option<&XbDocument> {
        self.raw.as_ref()
    }
    pub fn brep(&self) -> Option<&BrepModel> {
        self.brep.as_ref()
    }
    pub fn node(&self, index: u32) -> Option<&parasolid_core::RawNode> {
        self.raw
            .as_ref()?
            .nodes
            .get(*self.node_positions.get(&index)?)
    }

    fn diagnose(&mut self, diagnostic: GeometryDiagnostic) -> Result<(), GeometryError> {
        if diagnostic.category == ErrorKind::LimitExceeded {
            return Err(GeometryError::Limit(Box::new(diagnostic)));
        }
        self.diagnostics.push(diagnostic);
        Ok(())
    }
}

pub(crate) fn category(kind: parasolid_core::ErrorKind) -> ErrorKind {
    use parasolid_core::ErrorKind as P;
    match kind {
        P::LimitExceeded => ErrorKind::LimitExceeded,
        P::UnsupportedBinaryFormat
        | P::UnknownSchemaOpcode
        | P::UnsupportedSchemaFieldType
        | P::MissingBaseSchema
        | P::MissingSchemaType
        | P::UnsupportedBaseSchemaType
        | P::UnknownBaseSchemaType
        | P::UnsupportedBuiltinSchemaKey
        | P::BuiltinProfileUncoveredType
        | P::UnsupportedUserFields
        | P::MissingBrepBody => ErrorKind::Unsupported,
        _ => ErrorKind::Invalid,
    }
}

fn status(kind: ErrorKind) -> Status {
    match kind {
        ErrorKind::Unsupported => Status::Unsupported,
        _ => Status::Invalid,
    }
}

fn diagnostic(
    resource: &ResourceRef,
    range: ByteRange,
    scope: &'static str,
    error: &ParseError,
) -> GeometryDiagnostic {
    let node_type = match error.details() {
        ErrorDetails::SchemaLookup { node_type, .. }
        | ErrorDetails::BuiltinProfileLookup { node_type, .. }
        | ErrorDetails::NodeType { node_type } => Some(*node_type),
        _ => None,
    };
    let node_index = match error.details() {
        ErrorDetails::NodeIndex { node_index }
        | ErrorDetails::BrepField { node_index, .. }
        | ErrorDetails::BrepReference { node_index, .. }
        | ErrorDetails::BrepInvariant { node_index, .. } => Some(*node_index),
        _ => None,
    };
    GeometryDiagnostic {
        category: category(error.kind()),
        code: error.kind().code(),
        message: error.message().to_string(),
        scope,
        resource_id: resource.resource_id.clone(),
        container_range: range,
        byte_offset: None,
        decoded_offset: Some(error.offset() as u64),
        backend_code: Some(error.kind().code()),
        node_type,
        node_index,
    }
}

impl Document {
    pub fn read_geometry(
        &self,
        resource_id: &str,
        catalog: Option<&SchemaCatalog>,
        limits: GeometryLimits,
    ) -> Result<GeometryResult, GeometryError> {
        limits.validate()?;
        let resource = self
            .resources()
            .iter()
            .find(|r| r.resource_id == resource_id)
            .ok_or_else(|| {
                InspectError::format(
                    ErrorKind::Invalid,
                    "resource.not_found",
                    0,
                    "resource ID is not in this document",
                )
            })?;
        let mut result = GeometryResult {
            resource_id: resource_id.to_string(),
            source_sha256: self.source_sha256().to_string(),
            source: None,
            schema: None,
            status: GeometryStatus {
                extraction: Status::NotChecked,
                raw_geometry: Status::NotChecked,
                brep: Status::NotChecked,
                topology: Status::NotChecked,
            },
            diagnostics: Vec::new(),
            raw: None,
            brep: None,
            node_positions: BTreeMap::new(),
        };
        if resource.declared_decoded_bytes > limits.max_payload_bytes as u64 {
            return Err(GeometryError::Limit(Box::new(GeometryDiagnostic {
                category: ErrorKind::LimitExceeded,
                code: "limit.payload_bytes",
                message: "declared payload exceeds geometry byte limit".into(),
                scope: "extraction",
                resource_id: resource_id.into(),
                container_range: resource.storage_range,
                byte_offset: Some(resource.storage_range.start),
                decoded_offset: None,
                backend_code: None,
                node_type: None,
                node_index: None,
            })));
        }
        let extraction = match self.extract(resource_id) {
            Ok(extraction) => extraction,
            Err(InspectError::Format(d)) => {
                result.status.extraction = status(d.kind);
                result.diagnose(GeometryDiagnostic {
                    category: d.kind,
                    code: d.code,
                    message: d.message,
                    scope: "extraction",
                    resource_id: resource_id.into(),
                    container_range: resource.storage_range,
                    byte_offset: Some(d.byte_offset),
                    decoded_offset: None,
                    backend_code: None,
                    node_type: None,
                    node_index: None,
                })?;
                return Ok(result);
            }
            Err(e) => return Err(e.into()),
        };
        let range = extraction.source.container_range;
        result.source = Some(extraction.source);
        result.status.extraction = Status::Complete;
        let header = match inspect_xb(
            &extraction.payload,
            parasolid_core::InspectionLimits {
                max_file_size: limits.max_payload_bytes,
                max_string_bytes: limits.max_string_bytes,
            },
        ) {
            Ok(header) => header,
            Err(error) => {
                result.status.raw_geometry = status(category(error.kind()));
                result.diagnose(diagnostic(resource, range, "raw_geometry", &error))?;
                return Ok(result);
            }
        };
        // inspect_xb already validated the key, but preserve any backend error.
        let key = SchemaKey::parse(&header.schema_key).map_err(|e| {
            GeometryError::Input(InspectError::format(
                ErrorKind::Invalid,
                e.kind().code(),
                range.start,
                e.message(),
            ))
        })?;
        let mut selection = SchemaSelection {
            kind: "unavailable",
            schema_key: header.schema_key.clone(),
            provider_schema: key.provider_schema().into(),
            catalog_sha256: None,
            profile_id: None,
            profile_revision: None,
            profile_sha256: None,
            coverage: None,
        };
        let parsed = if let Some(catalog) = catalog {
            selection.kind = "external";
            selection.catalog_sha256 = Some(catalog.sha256().into());
            if catalog.schema_id() != key.provider_schema() {
                result.status.raw_geometry = Status::Unsupported;
                result.schema = Some(selection);
                result.diagnose(GeometryDiagnostic {
                    category: ErrorKind::Unsupported,
                    code: "schema.catalog_mismatch",
                    message: format!(
                        "resource requires catalog {}, supplied {}",
                        key.provider_schema(),
                        catalog.schema_id()
                    ),
                    scope: "raw_geometry",
                    resource_id: resource_id.into(),
                    container_range: range,
                    byte_offset: None,
                    decoded_offset: None,
                    backend_code: None,
                    node_type: None,
                    node_index: None,
                })?;
                return Ok(result);
            }
            parse_xb(&extraction.payload, catalog.provider(), limits.backend())
        } else {
            match BuiltinProfileRegistry::compiled() {
                Ok(registry) => {
                    if let Some(provider) = registry.provider_for_key(&key) {
                        use parasolid_core::SchemaProvider;
                        selection.kind = "builtin";
                        if let SchemaProviderResolution::Builtin {
                            profile_id,
                            profile_revision,
                            profile_sha256,
                            coverage,
                            ..
                        } = provider.provenance().into()
                        {
                            selection.profile_id = Some(profile_id);
                            selection.profile_revision = Some(profile_revision);
                            selection.profile_sha256 = Some(profile_sha256);
                            selection.coverage = Some(coverage.as_str());
                        }
                        parse_xb(&extraction.payload, &provider, limits.backend())
                    } else {
                        result.schema = Some(selection);
                        result.status.raw_geometry = Status::Unsupported;
                        result.diagnose(GeometryDiagnostic { category: ErrorKind::Unsupported,
                            code: "schema.missing_base_schema", message: "no built-in profile supports this exact schema key; supply the explicit matching catalog".into(),
                            scope: "raw_geometry", resource_id: resource_id.into(), container_range: range,
                            byte_offset: None, decoded_offset: Some(header.header_range.end as u64),
                            backend_code: None, node_type: None, node_index: None })?;
                        return Ok(result);
                    }
                }
                Err(error) => Err(error),
            }
        };
        result.schema = Some(selection);
        let raw = match parsed {
            Ok(raw) => raw,
            Err(error) => {
                result.status.raw_geometry = status(category(error.kind()));
                result.diagnose(diagnostic(resource, range, "raw_geometry", &error))?;
                return Ok(result);
            }
        };
        result.status.raw_geometry = Status::Complete;
        match map_xb_brep_with_diagnostic_limit(&raw, limits.max_diagnostics) {
            Ok(brep) => {
                result.status.brep = if brep.complete {
                    Status::Complete
                } else {
                    Status::Partial
                };
                result.status.topology = if brep.topology.valid {
                    Status::Complete
                } else {
                    Status::Invalid
                };
                for d in &brep.diagnostics {
                    result.diagnose(GeometryDiagnostic {
                        category: ErrorKind::Unsupported,
                        code: d.code,
                        message: d.message.clone(),
                        scope: "brep",
                        resource_id: resource_id.into(),
                        container_range: range,
                        byte_offset: None,
                        decoded_offset: Some(d.source.byte_range.start as u64),
                        backend_code: Some(d.code),
                        node_type: Some(d.source.node_type),
                        node_index: Some(d.source.node_index),
                    })?;
                }
                result.brep = Some(brep);
            }
            Err(error) => {
                result.status.brep = status(category(error.kind()));
                if error.kind() == parasolid_core::ErrorKind::InvalidBrepTopology {
                    result.status.topology = Status::Invalid;
                }
                result.diagnose(diagnostic(resource, range, "brep", &error))?;
            }
        }
        result.node_positions = raw
            .nodes
            .iter()
            .enumerate()
            .map(|(i, n)| (n.index, i))
            .collect();
        result.raw = Some(raw);
        Ok(result)
    }
}
