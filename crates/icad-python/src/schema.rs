use crate::inspection_error;
use pyo3::prelude::*;
use std::path::PathBuf;

#[pyclass(frozen, module = "icadkit._core")]
pub struct CatalogHandle {
    pub(crate) inner: icad_core::SchemaCatalog,
}
fn limits(p: (usize, usize, usize, usize)) -> icad_core::CatalogLimits {
    icad_core::CatalogLimits {
        max_file_bytes: p.0,
        max_schema_types: p.1,
        max_fields_per_type: p.2,
        max_string_bytes: p.3,
    }
}
#[pyfunction]
#[pyo3(signature = (data, expected_id, expected_sha256, policy))]
pub fn catalog_bytes(
    py: Python<'_>,
    data: &[u8],
    expected_id: &str,
    expected_sha256: Option<&str>,
    policy: (usize, usize, usize, usize),
) -> PyResult<CatalogHandle> {
    Ok(CatalogHandle {
        inner: py
            .detach(|| {
                icad_core::SchemaCatalog::from_bytes(
                    data,
                    expected_id,
                    expected_sha256,
                    limits(policy),
                )
            })
            .map_err(inspection_error)?,
    })
}
#[pyfunction]
#[pyo3(signature = (path, expected_id, expected_sha256, policy))]
pub fn catalog_path(
    py: Python<'_>,
    path: PathBuf,
    expected_id: &str,
    expected_sha256: Option<&str>,
    policy: (usize, usize, usize, usize),
) -> PyResult<CatalogHandle> {
    Ok(CatalogHandle {
        inner: py
            .detach(|| {
                icad_core::SchemaCatalog::from_file(
                    path,
                    expected_id,
                    expected_sha256,
                    limits(policy),
                )
            })
            .map_err(inspection_error)?,
    })
}
#[pymethods]
impl CatalogHandle {
    fn summary(&self) -> (&str, &str, &str, usize) {
        (
            self.inner.schema_id(),
            self.inner.sha256(),
            self.inner.modeller_version(),
            self.inner.definition_count(),
        )
    }
}
