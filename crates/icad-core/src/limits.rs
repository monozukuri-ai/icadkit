use crate::{ErrorKind, InspectError};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CatalogLimits {
    pub max_file_bytes: usize,
    pub max_schema_types: usize,
    pub max_fields_per_type: usize,
    pub max_string_bytes: usize,
}

impl Default for CatalogLimits {
    fn default() -> Self {
        Self {
            max_file_bytes: 4 * 1024 * 1024,
            max_schema_types: 65_536,
            max_fields_per_type: 4096,
            max_string_bytes: 1024 * 1024,
        }
    }
}

impl CatalogLimits {
    pub(crate) fn validate(self) -> Result<(), InspectError> {
        positive(&[
            self.max_file_bytes,
            self.max_schema_types,
            self.max_fields_per_type,
            self.max_string_bytes,
        ])
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct GeometryLimits {
    pub max_payload_bytes: usize,
    pub max_nodes: usize,
    pub max_schema_types: usize,
    pub max_fields_per_type: usize,
    pub max_string_bytes: usize,
    pub max_variable_elements: usize,
    pub max_diagnostics: usize,
}

impl Default for GeometryLimits {
    fn default() -> Self {
        Self {
            max_payload_bytes: 64 * 1024 * 1024,
            max_nodes: 100_000,
            max_schema_types: 65_536,
            max_fields_per_type: 4096,
            max_string_bytes: 1024 * 1024,
            max_variable_elements: 1_000_000,
            max_diagnostics: 1000,
        }
    }
}

impl GeometryLimits {
    pub(crate) fn validate(self) -> Result<(), InspectError> {
        positive(&[
            self.max_payload_bytes,
            self.max_nodes,
            self.max_schema_types,
            self.max_fields_per_type,
            self.max_string_bytes,
            self.max_variable_elements,
            self.max_diagnostics,
        ])
    }

    pub(crate) fn backend(self) -> parasolid_core::DocumentLimits {
        parasolid_core::DocumentLimits {
            max_file_size: self.max_payload_bytes,
            max_nodes: self.max_nodes,
            max_schema_types: self.max_schema_types,
            max_fields_per_type: self.max_fields_per_type,
            max_string_bytes: self.max_string_bytes,
            max_variable_elements: self.max_variable_elements,
        }
    }
}

fn positive(values: &[usize]) -> Result<(), InspectError> {
    if values.contains(&0) {
        return Err(InspectError::format(
            ErrorKind::Invalid,
            "limits.invalid",
            0,
            "all limits must be positive",
        ));
    }
    Ok(())
}

/// Limits for an owned document and a single lazy extraction. No decoded cache
/// or bulk extraction is kept, so there is no hidden cumulative output buffer.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ReadLimits {
    pub max_file_bytes: u64,
    pub max_record_bytes: u64,
    pub max_resource_bytes: u64,
    pub max_records: u64,
    pub max_resources: u64,
}

impl Default for ReadLimits {
    fn default() -> Self {
        Self {
            max_file_bytes: 512 * 1024 * 1024,
            max_record_bytes: 512 * 1024 * 1024,
            max_resource_bytes: 64 * 1024 * 1024,
            max_records: 200_000,
            max_resources: 100_000,
        }
    }
}

impl ReadLimits {
    pub(crate) fn inspection(self) -> InspectionLimits {
        InspectionLimits {
            max_file_bytes: self.max_file_bytes,
            max_record_bytes: self.max_record_bytes,
        }
    }

    pub fn validate(self) -> Result<(), InspectError> {
        self.inspection().validate()?;
        if self.max_resource_bytes == 0 || self.max_records == 0 || self.max_resources == 0 {
            return Err(InspectError::format(
                ErrorKind::Invalid,
                "limits.invalid",
                0,
                "read limits must be positive",
            ));
        }
        Ok(())
    }

    pub(crate) fn resource(self, size: u64, offset: u64) -> Result<(), InspectError> {
        if size > self.max_resource_bytes {
            return Err(InspectError::format(
                ErrorKind::LimitExceeded,
                "limit.resource_bytes",
                offset,
                format!(
                    "resource size {size} exceeds {} bytes",
                    self.max_resource_bytes
                ),
            ));
        }
        Ok(())
    }
}

/// Size policy for inspection. Record payloads are never allocated or decoded.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct InspectionLimits {
    pub max_file_bytes: u64,
    pub max_record_bytes: u64,
}

impl Default for InspectionLimits {
    fn default() -> Self {
        Self {
            max_file_bytes: 512 * 1024 * 1024,
            max_record_bytes: 64 * 1024 * 1024,
        }
    }
}

impl InspectionLimits {
    pub(crate) fn validate(self) -> Result<(), InspectError> {
        if self.max_file_bytes == 0 || self.max_record_bytes == 0 {
            return Err(InspectError::format(
                ErrorKind::Invalid,
                "limits.invalid",
                0,
                "inspection limits must be positive",
            ));
        }
        Ok(())
    }

    pub(crate) fn check_file(self, size: u64) -> Result<(), InspectError> {
        if size > self.max_file_bytes {
            return Err(InspectError::format(
                ErrorKind::LimitExceeded,
                "limit.file_bytes",
                0,
                format!("file size {size} exceeds {} bytes", self.max_file_bytes),
            ));
        }
        Ok(())
    }
}
