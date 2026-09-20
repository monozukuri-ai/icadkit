use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyList};
use std::path::PathBuf;
mod brep;
mod document;
mod geometry;
mod schema;
pyo3::create_exception!(_core, GeometryError, pyo3::exceptions::PyException);

pyo3::create_exception!(_core, InspectionError, pyo3::exceptions::PyException);

fn inspection_error(error: icad_core::InspectError) -> PyErr {
    match error {
        icad_core::InspectError::Format(diagnostic) => InspectionError::new_err((
            diagnostic.kind.as_str(),
            diagnostic.code,
            diagnostic.byte_offset,
            diagnostic.message,
        )),
        icad_core::InspectError::Io(error) => error.into(),
    }
}

fn inspection_dict(
    py: Python<'_>,
    inspection: icad_core::Inspection,
) -> PyResult<Bound<'_, PyDict>> {
    let result = PyDict::new(py);
    result.set_item("file_size", inspection.file_size)?;
    result.set_item("bytes_read", inspection.bytes_read)?;
    let header = PyDict::new(py);
    header.set_item("byte_order", inspection.header.byte_order.as_str())?;
    header.set_item(
        "raw_version",
        PyBytes::new(py, &inspection.header.raw_version),
    )?;
    header.set_item("raw_name", PyBytes::new(py, &inspection.header.raw_name))?;
    header.set_item("raw_mod", PyBytes::new(py, &inspection.header.raw_mod))?;
    result.set_item("header", header)?;
    let records = PyList::empty(py);
    for record in inspection.leading_records {
        let value = PyDict::new(py);
        value.set_item("tag", record.tag)?;
        value.set_item(
            "byte_range",
            (record.byte_range.start, record.byte_range.end),
        )?;
        value.set_item(
            "payload_range",
            (record.payload_range.start, record.payload_range.end),
        )?;
        records.append(value)?;
    }
    result.set_item("leading_records", records)?;
    let unknown = PyList::empty(py);
    for item in inspection.unparsed_ranges {
        let value = PyDict::new(py);
        value.set_item("byte_range", (item.byte_range.start, item.byte_range.end))?;
        value.set_item("reason", item.reason)?;
        value.set_item("record_tag", item.record_tag)?;
        unknown.append(value)?;
    }
    result.set_item("unparsed_ranges", unknown)?;
    let status = PyDict::new(py);
    status.set_item("header", inspection.status.header.as_str())?;
    status.set_item("container", inspection.status.container.as_str())?;
    status.set_item("extraction", inspection.status.extraction.as_str())?;
    status.set_item("raw_geometry", inspection.status.raw_geometry.as_str())?;
    status.set_item("brep", inspection.status.brep.as_str())?;
    status.set_item("model", inspection.status.model.as_str())?;
    result.set_item("status", status)?;
    Ok(result)
}

#[pyfunction]
fn inspect_bytes<'py>(
    py: Python<'py>,
    data: &[u8],
    max_file_bytes: u64,
    max_record_bytes: u64,
) -> PyResult<Bound<'py, PyDict>> {
    // This bounded 280-byte inspection retains the GIL and borrows immutable
    // Python bytes; it never copies the complete input into a Rust Vec.
    let limits = icad_core::InspectionLimits {
        max_file_bytes,
        max_record_bytes,
    };
    inspection_dict(
        py,
        icad_core::inspect_bytes(data, limits).map_err(inspection_error)?,
    )
}

#[pyfunction]
fn inspect_path(
    py: Python<'_>,
    path: PathBuf,
    max_file_bytes: u64,
    max_record_bytes: u64,
) -> PyResult<Bound<'_, PyDict>> {
    let limits = icad_core::InspectionLimits {
        max_file_bytes,
        max_record_bytes,
    };
    let inspected = py.detach(move || icad_core::inspect_file(path, limits));
    inspection_dict(py, inspected.map_err(inspection_error)?)
}

#[pyfunction]
fn build_info(py: Python<'_>) -> PyResult<(&'static str, &'static str, Vec<String>)> {
    let backend = py
        .detach(icad_core::backend_info)
        .map_err(|error| PyRuntimeError::new_err(error.to_string()))?;
    Ok((
        icad_core::VERSION,
        backend.version,
        backend.builtin_profile_ids,
    ))
}

#[pymodule]
fn _core(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(build_info, module)?)?;
    module.add_function(wrap_pyfunction!(inspect_bytes, module)?)?;
    module.add_function(wrap_pyfunction!(inspect_path, module)?)?;
    module.add_function(wrap_pyfunction!(document::read_bytes, module)?)?;
    module.add_function(wrap_pyfunction!(document::read_path, module)?)?;
    module.add_class::<document::DocumentHandle>()?;
    module.add_class::<schema::CatalogHandle>()?;
    module.add_class::<geometry::GeometryHandle>()?;
    module.add_function(wrap_pyfunction!(schema::catalog_bytes, module)?)?;
    module.add_function(wrap_pyfunction!(schema::catalog_path, module)?)?;
    module.add("GeometryError", module.py().get_type::<GeometryError>())?;
    module.add("InspectionError", module.py().get_type::<InspectionError>())?;
    Ok(())
}
