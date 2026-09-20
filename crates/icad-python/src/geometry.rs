use crate::{GeometryError, inspection_error};
use icad_core::{GeometryDiagnostic, GeometryResult};
use parasolid_core::{FieldValue, RawNode};
use pyo3::exceptions::{PyKeyError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyList};
use std::sync::Arc;

#[pyclass(frozen, module = "icadkit._core")]
pub struct GeometryHandle {
    pub(crate) inner: Arc<GeometryResult>,
}

pub(crate) fn page<T>(values: &[T], start: usize, count: usize) -> PyResult<&[T]> {
    if count > 1000 {
        return Err(PyValueError::new_err("page count must be at most 1000"));
    }
    let start = start.min(values.len());
    Ok(&values[start..start.saturating_add(count).min(values.len())])
}
pub(crate) fn diagnostic<'py>(
    py: Python<'py>,
    d: &GeometryDiagnostic,
) -> PyResult<Bound<'py, PyDict>> {
    let out = PyDict::new(py);
    out.set_item("category", d.category.as_str())?;
    out.set_item("code", d.code)?;
    out.set_item("message", &d.message)?;
    out.set_item("scope", d.scope)?;
    out.set_item("resource_id", &d.resource_id)?;
    out.set_item(
        "container_range",
        (d.container_range.start, d.container_range.end),
    )?;
    out.set_item("byte_offset", d.byte_offset)?;
    out.set_item("decoded_offset", d.decoded_offset)?;
    out.set_item("backend_code", d.backend_code)?;
    out.set_item("node_type", d.node_type)?;
    out.set_item("node_index", d.node_index)?;
    Ok(out)
}
pub(crate) fn error(py: Python<'_>, e: icad_core::GeometryError) -> PyErr {
    match e {
        icad_core::GeometryError::Input(e) => inspection_error(e),
        icad_core::GeometryError::Limit(d) => match diagnostic(py, &d) {
            Ok(d) => GeometryError::new_err((d.unbind(),)),
            Err(e) => e,
        },
    }
}
fn node_dict<'py>(py: Python<'py>, n: &RawNode) -> PyResult<Bound<'py, PyDict>> {
    let out = PyDict::new(py);
    out.set_item("index", n.index)?;
    out.set_item("node_type", n.node_type)?;
    out.set_item("type_name", &n.definition.name)?;
    out.set_item("decoded_range", (n.byte_range.start, n.byte_range.end))?;
    out.set_item("field_count", n.fields.len())?;
    out.set_item("user_field_count", n.user_fields.len())?;
    out.set_item("variable_length", n.variable_length)?;
    Ok(out)
}
#[pymethods]
impl GeometryHandle {
    fn summary<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let r = &self.inner;
        let out = PyDict::new(py);
        out.set_item("resource_id", r.resource_id())?;
        out.set_item("source_sha256", r.source_sha256())?;
        let status = PyDict::new(py);
        let s = r.status();
        for (key, value) in [
            ("extraction", s.extraction),
            ("raw_geometry", s.raw_geometry),
            ("brep", s.brep),
            ("topology", s.topology),
        ] {
            status.set_item(key, value.as_str())?;
        }
        out.set_item("status", status)?;
        let source = r
            .source()
            .map(|s| -> PyResult<_> {
                let d = PyDict::new(py);
                d.set_item("resource_id", &s.resource_id)?;
                d.set_item("source_sha256", &s.source_sha256)?;
                d.set_item("payload_sha256", &s.payload_sha256)?;
                d.set_item("encoding", s.encoding.as_str())?;
                d.set_item(
                    "container_range",
                    (s.container_range.start, s.container_range.end),
                )?;
                d.set_item(
                    "decoded_range",
                    (s.decoded_range.start, s.decoded_range.end),
                )?;
                Ok(d)
            })
            .transpose()?;
        out.set_item("source", source)?;
        let schema = r
            .schema()
            .map(|s| -> PyResult<_> {
                let d = PyDict::new(py);
                d.set_item("kind", s.kind)?;
                d.set_item("schema_key", &s.schema_key)?;
                d.set_item("provider_schema", &s.provider_schema)?;
                d.set_item("catalog_sha256", &s.catalog_sha256)?;
                d.set_item("profile_id", &s.profile_id)?;
                d.set_item("profile_revision", s.profile_revision)?;
                d.set_item("profile_sha256", &s.profile_sha256)?;
                d.set_item("coverage", s.coverage)?;
                Ok(d)
            })
            .transpose()?;
        out.set_item("schema", schema)?;
        let ds = PyList::empty(py);
        for d in r.diagnostics() {
            ds.append(diagnostic(py, d)?)?;
        }
        out.set_item("diagnostics", ds)?;
        out.set_item("has_raw", r.raw().is_some())?;
        out.set_item("has_brep", r.brep().is_some())?;
        Ok(out)
    }
    fn raw_summary<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let raw = self
            .inner
            .raw()
            .ok_or_else(|| PyValueError::new_err("raw geometry unavailable"))?;
        let d = PyDict::new(py);
        d.set_item("node_count", raw.nodes.len())?;
        d.set_item("schema_count", raw.schemas.len())?;
        d.set_item("schema_key", &raw.header.schema_key)?;
        d.set_item(
            "terminator_range",
            (
                raw.terminator.byte_range.start,
                raw.terminator.byte_range.end,
            ),
        )?;
        Ok(d)
    }
    fn nodes<'py>(
        &self,
        py: Python<'py>,
        start: usize,
        count: usize,
    ) -> PyResult<Bound<'py, PyList>> {
        let raw = self
            .inner
            .raw()
            .ok_or_else(|| PyValueError::new_err("raw geometry unavailable"))?;
        let out = PyList::empty(py);
        for n in page(&raw.nodes, start, count)? {
            out.append(node_dict(py, n)?)?;
        }
        Ok(out)
    }
    fn node<'py>(&self, py: Python<'py>, index: u32) -> PyResult<Bound<'py, PyDict>> {
        node_dict(
            py,
            self.inner
                .node(index)
                .ok_or_else(|| PyKeyError::new_err(index))?,
        )
    }
    fn fields<'py>(
        &self,
        py: Python<'py>,
        index: u32,
        start: usize,
        count: usize,
    ) -> PyResult<Bound<'py, PyList>> {
        let n = self
            .inner
            .node(index)
            .ok_or_else(|| PyKeyError::new_err(index))?;
        let out = PyList::empty(py);
        for (i, f) in page(&n.fields, start, count)?.iter().enumerate() {
            let d = PyDict::new(py);
            d.set_item("index", start + i)?;
            d.set_item("name", &f.definition.name)?;
            d.set_item("field_type", f.definition.field_type.code().to_string())?;
            d.set_item("pointer_class", f.definition.pointer_class)?;
            d.set_item("element_count", f.definition.element_count)?;
            d.set_item("transmitted", f.definition.transmitted)?;
            d.set_item("value_count", f.values.len())?;
            d.set_item("decoded_range", (f.byte_range.start, f.byte_range.end))?;
            out.append(d)?;
        }
        Ok(out)
    }
    fn field_values<'py>(
        &self,
        py: Python<'py>,
        index: u32,
        field: usize,
        start: usize,
        count: usize,
    ) -> PyResult<Bound<'py, PyList>> {
        let n = self
            .inner
            .node(index)
            .ok_or_else(|| PyKeyError::new_err(index))?;
        let f = n
            .fields
            .get(field)
            .ok_or_else(|| PyKeyError::new_err(field))?;
        let out = PyList::empty(py);
        for value in page(&f.values, start, count)? {
            match value {
                FieldValue::UnsignedByte(v) | FieldValue::Character(v) => out.append(v)?,
                FieldValue::Logical(v) => out.append(v)?,
                FieldValue::ShortInteger(v) => out.append(v)?,
                FieldValue::UnicodeCharacter(v) => out.append(v)?,
                FieldValue::Integer(v) | FieldValue::Tag(v) => out.append(v)?,
                FieldValue::PointerIndex(v) => out.append(v)?,
                FieldValue::Double(v) => out.append(v)?,
                FieldValue::Interval(v) => out.append(v)?,
                FieldValue::Vector(v) | FieldValue::IntersectionPoint(v) => out.append(v)?,
                FieldValue::Box3(v) => out.append(v)?,
            }
        }
        Ok(out)
    }
    fn user_fields(&self, index: u32, start: usize, count: usize) -> PyResult<Vec<Option<i32>>> {
        let n = self
            .inner
            .node(index)
            .ok_or_else(|| PyKeyError::new_err(index))?;
        Ok(page(&n.user_fields, start, count)?.to_vec())
    }
    fn raw_bytes<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyBytes>> {
        let raw = self
            .inner
            .raw()
            .ok_or_else(|| PyValueError::new_err("raw geometry unavailable"))?;
        Ok(PyBytes::new(py, raw.raw_bytes()))
    }
    fn brep_summary<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        crate::brep::summary(
            py,
            self.inner
                .brep()
                .ok_or_else(|| PyValueError::new_err("B-Rep unavailable"))?,
        )
    }
    fn entities<'py>(
        &self,
        py: Python<'py>,
        collection: &str,
        start: usize,
        count: usize,
    ) -> PyResult<Bound<'py, PyList>> {
        crate::brep::entities(
            py,
            self.inner
                .brep()
                .ok_or_else(|| PyValueError::new_err("B-Rep unavailable"))?,
            collection,
            start,
            count,
        )
    }
}
