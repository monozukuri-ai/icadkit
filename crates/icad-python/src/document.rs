use crate::{inspection_dict, inspection_error};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyList};
use std::path::PathBuf;

#[pyclass(frozen, module = "icadkit._core")]
pub struct DocumentHandle {
    inner: icad_core::Document,
}

fn limits(values: (u64, u64, u64, u64, u64)) -> icad_core::ReadLimits {
    icad_core::ReadLimits {
        max_file_bytes: values.0,
        max_record_bytes: values.1,
        max_resource_bytes: values.2,
        max_records: values.3,
        max_resources: values.4,
    }
}

#[pyfunction]
pub fn read_bytes(
    py: Python<'_>,
    data: &[u8],
    policy: (u64, u64, u64, u64, u64),
) -> PyResult<DocumentHandle> {
    let inner = py
        .detach(|| icad_core::Document::from_bytes(data, limits(policy)))
        .map_err(inspection_error)?;
    Ok(DocumentHandle { inner })
}

#[pyfunction]
pub fn read_path(
    py: Python<'_>,
    path: PathBuf,
    policy: (u64, u64, u64, u64, u64),
) -> PyResult<DocumentHandle> {
    let inner = py
        .detach(move || icad_core::Document::from_file(path, limits(policy)))
        .map_err(inspection_error)?;
    Ok(DocumentHandle { inner })
}

#[pymethods]
impl DocumentHandle {
    fn read_views<'py>(
        &self,
        py: Python<'py>,
        policy: (usize, usize),
    ) -> PyResult<Bound<'py, PyDict>> {
        let index = py
            .detach(|| {
                self.inner.read_views(icad_core::ViewLimits {
                    max_views: policy.0,
                    max_records: policy.1,
                })
            })
            .map_err(inspection_error)?;
        let result = PyDict::new(py);
        result.set_item("status", index.status.as_str())?;
        result.set_item("document_kind", index.document_kind)?;
        result.set_item("profile_id", index.profile_id)?;
        let diagnostics = |items: Vec<icad_core::Diagnostic>| -> PyResult<Bound<'py, PyList>> {
            let rows = PyList::empty(py);
            for d in items {
                let row = PyDict::new(py);
                row.set_item("category", d.kind.as_str())?;
                row.set_item("code", d.code)?;
                row.set_item("byte_offset", d.byte_offset)?;
                row.set_item("message", d.message)?;
                rows.append(row)?;
            }
            Ok(rows)
        };
        result.set_item("diagnostics", diagnostics(index.diagnostics)?)?;
        let views = PyList::empty(py);
        for v in index.views {
            let value = PyDict::new(py);
            value.set_item("byte_range", (v.byte_range.start, v.byte_range.end))?;
            value.set_item("header_range", v.header_range.map(|r| (r.start, r.end)))?;
            value.set_item("raw_name", PyBytes::new(py, &v.raw_name))?;
            value.set_item("kind", v.kind)?;
            value.set_item("raw_view_number", v.raw_view_number)?;
            value.set_item("status", v.status.as_str())?;
            value.set_item("diagnostics", diagnostics(v.diagnostics)?)?;
            value.set_item(
                "opaque_ranges",
                v.opaque_ranges
                    .iter()
                    .map(|r| (r.start, r.end))
                    .collect::<Vec<_>>(),
            )?;
            let entries = PyList::empty(py);
            for e in v.entries {
                let entry = PyDict::new(py);
                entry.set_item("byte_range", (e.byte_range.start, e.byte_range.end))?;
                entry.set_item("kind", e.kind)?;
                entry.set_item("tag", e.tag)?;
                entry.set_item("owner_offset", e.owner_offset)?;
                entries.append(entry)?;
            }
            value.set_item("entries", entries)?;
            views.append(value)?;
        }
        result.set_item("views", views)?;
        Ok(result)
    }

    fn read_parts<'py>(
        &self,
        py: Python<'py>,
        policy: (usize, usize, usize, usize),
    ) -> PyResult<Bound<'py, PyDict>> {
        let index = py
            .detach(|| {
                self.inner.read_parts(icad_core::PartLimits {
                    max_parts: policy.0,
                    max_entities: policy.1,
                    max_depth: policy.2,
                    max_property_bytes: policy.3,
                })
            })
            .map_err(inspection_error)?;
        let result = PyDict::new(py);
        result.set_item("index_status", index.index_status.as_str())?;
        result.set_item("hierarchy_status", index.hierarchy_status.as_str())?;
        if let Some(profile) = &index.profile {
            let value = PyDict::new(py);
            value.set_item("profile_id", profile.profile_id)?;
            value.set_item("byte_order", profile.byte_order.as_str())?;
            value.set_item("raw_version", PyBytes::new(py, &profile.raw_version))?;
            value.set_item("part_tag", profile.part_tag)?;
            value.set_item("coordinate_convention", profile.coordinate_convention)?;
            value.set_item("mirror_policy", profile.mirror_policy)?;
            value.set_item("entity_policy", profile.entity_policy)?;
            value.set_item("source_length_unit", profile.source_length_unit)?;
            value.set_item("saved_body_layout", profile.saved_body_layout)?;
            value.set_item("csg_layout", profile.csg_layout)?;
            value.set_item(
                "view_byte_range",
                (profile.view_byte_range.start, profile.view_byte_range.end),
            )?;
            value.set_item(
                "part_byte_range",
                (profile.part_byte_range.start, profile.part_byte_range.end),
            )?;
            result.set_item("profile", value)?;
        } else {
            result.set_item("profile", py.None())?;
        }
        let views = PyList::empty(py);
        for view in index.views {
            let value = PyDict::new(py);
            value.set_item("kind", view.kind)?;
            value.set_item("byte_range", (view.byte_range.start, view.byte_range.end))?;
            views.append(value)?;
        }
        result.set_item("views", views)?;
        // No MOD field has yet been qualified as a document/library discriminator.
        result.set_item("document_kind", "unknown")?;
        let parts = PyList::empty(py);
        for part in index.parts {
            let p = PyDict::new(py);
            p.set_item("byte_range", (part.byte_range.start, part.byte_range.end))?;
            p.set_item("view_offset", part.view_offset)?;
            p.set_item("source_id", part.source_id)?;
            p.set_item("flags", part.flags)?;
            p.set_item("is_root", part.is_root)?;
            p.set_item("raw_name", PyBytes::new(py, &part.raw_name))?;
            p.set_item("raw_comment", PyBytes::new(py, &part.raw_comment))?;
            p.set_item("placement_values", part.placement_values)?;
            p.set_item("coordinate_values", part.coordinate_values)?;
            p.set_item(
                "raw_reference_name",
                PyBytes::new(py, &part.raw_reference_name),
            )?;
            let fields = PyList::empty(py);
            for field in part.extra_fields {
                fields.append((
                    (field.byte_range.start, field.byte_range.end),
                    PyBytes::new(py, &field.raw_value),
                ))?;
            }
            p.set_item("extra_fields", fields)?;
            let attributes = PyList::empty(py);
            for field in part.opaque_attributes {
                attributes.append((
                    (field.byte_range.start, field.byte_range.end),
                    field.source_id,
                    field.subtype,
                    PyBytes::new(py, &field.raw_bytes),
                ))?;
            }
            p.set_item("opaque_attributes", attributes)?;
            let entities = PyList::empty(py);
            for entity in part.entities {
                let e = PyDict::new(py);
                e.set_item(
                    "byte_range",
                    (entity.byte_range.start, entity.byte_range.end),
                )?;
                e.set_item("source_id", entity.source_id)?;
                e.set_item("raw_type", entity.raw_type)?;
                e.set_item("layer", entity.layer)?;
                e.set_item("color_index", entity.color_index)?;
                e.set_item("visible", entity.visible)?;
                e.set_item("is_mirror", entity.is_mirror)?;
                e.set_item("appearance_status", entity.appearance_status.as_str())?;
                e.set_item("geometry_status", entity.geometry_status.as_str())?;
                if let Some(primitive) = entity.primitive {
                    let value = PyDict::new(py);
                    value.set_item("kind", primitive.kind)?;
                    value.set_item("frame", primitive.frame)?;
                    value.set_item("parameters", primitive.parameters)?;
                    e.set_item("primitive", value)?;
                } else {
                    e.set_item("primitive", py.None())?;
                }
                let diagnostics = PyList::empty(py);
                for d in entity.diagnostics {
                    let value = PyDict::new(py);
                    value.set_item("category", d.kind.as_str())?;
                    value.set_item("code", d.code)?;
                    value.set_item("byte_offset", d.byte_offset)?;
                    value.set_item("message", d.message)?;
                    diagnostics.append(value)?;
                }
                e.set_item("diagnostics", diagnostics)?;
                entities.append(e)?;
            }
            p.set_item("entities", entities)?;
            p.set_item("parent_source_id", part.parent_source_id)?;
            p.set_item("first_child_source_id", part.first_child_source_id)?;
            p.set_item("previous_source_id", part.previous_source_id)?;
            p.set_item("next_source_id", part.next_source_id)?;
            parts.append(p)?;
        }
        result.set_item("parts", parts)?;
        let ranges = PyList::empty(py);
        for r in index.opaque_ranges {
            ranges.append(((r.byte_range.start, r.byte_range.end), r.reason))?;
        }
        result.set_item("opaque_ranges", ranges)?;
        let diagnostics = PyList::empty(py);
        for d in index.diagnostics {
            let value = PyDict::new(py);
            value.set_item("category", d.kind.as_str())?;
            value.set_item("code", d.code)?;
            value.set_item("byte_offset", d.byte_offset)?;
            value.set_item("message", d.message)?;
            diagnostics.append(value)?;
        }
        result.set_item("diagnostics", diagnostics)?;
        Ok(result)
    }

    #[pyo3(signature = (resource_id, catalog, policy))]
    fn read_geometry(
        &self,
        py: Python<'_>,
        resource_id: &str,
        catalog: Option<&crate::schema::CatalogHandle>,
        policy: (usize, usize, usize, usize, usize, usize, usize),
    ) -> PyResult<crate::geometry::GeometryHandle> {
        let limits = icad_core::GeometryLimits {
            max_payload_bytes: policy.0,
            max_nodes: policy.1,
            max_schema_types: policy.2,
            max_fields_per_type: policy.3,
            max_string_bytes: policy.4,
            max_variable_elements: policy.5,
            max_diagnostics: policy.6,
        };
        let inner = py
            .detach(|| {
                self.inner
                    .read_geometry(resource_id, catalog.map(|c| &c.inner), limits)
            })
            .map_err(|e| crate::geometry::error(py, e))?;
        Ok(crate::geometry::GeometryHandle {
            inner: std::sync::Arc::new(inner),
        })
    }

    fn summary<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let result = PyDict::new(py);
        result.set_item(
            "inspection",
            inspection_dict(py, self.inner.inspection().clone())?,
        )?;
        result.set_item("source_sha256", self.inner.source_sha256())?;
        result.set_item(
            "resource_index_status",
            if self.inner.resource_index_complete() {
                "complete"
            } else {
                "partial"
            },
        )?;
        let resources = PyList::empty(py);
        for resource in self.inner.resources() {
            let value = PyDict::new(py);
            value.set_item("resource_id", &resource.resource_id)?;
            value.set_item("kind", "parasolid_x_b")?;
            value.set_item(
                "owner_range",
                (resource.owner_range.start, resource.owner_range.end),
            )?;
            value.set_item(
                "storage_range",
                (resource.storage_range.start, resource.storage_range.end),
            )?;
            value.set_item("encoding", resource.encoding.as_str())?;
            value.set_item("declared_decoded_bytes", resource.declared_decoded_bytes)?;
            value.set_item("owner_type", resource.owner_type)?;
            value.set_item("layout_version", resource.layout_version)?;
            value.set_item("source_id", resource.source_id)?;
            resources.append(value)?;
        }
        result.set_item("resources", resources)?;
        let records = PyList::empty(py);
        for record in self.inner.records() {
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
        result.set_item("records", records)?;
        let ranges = PyList::empty(py);
        for item in self.inner.unparsed_ranges() {
            let value = PyDict::new(py);
            value.set_item("byte_range", (item.byte_range.start, item.byte_range.end))?;
            value.set_item("reason", item.reason)?;
            value.set_item("record_tag", item.record_tag)?;
            ranges.append(value)?;
        }
        result.set_item("unparsed_ranges", ranges)?;
        let diagnostics = PyList::empty(py);
        for diagnostic in self.inner.diagnostics() {
            let value = PyDict::new(py);
            value.set_item("category", diagnostic.kind.as_str())?;
            value.set_item("code", diagnostic.code)?;
            value.set_item("byte_offset", diagnostic.byte_offset)?;
            value.set_item("message", &diagnostic.message)?;
            diagnostics.append(value)?;
        }
        result.set_item("diagnostics", diagnostics)?;
        Ok(result)
    }

    fn extract<'py>(&self, py: Python<'py>, resource_id: &str) -> PyResult<Bound<'py, PyDict>> {
        let extracted = py
            .detach(|| self.inner.extract(resource_id))
            .map_err(inspection_error)?;
        let result = PyDict::new(py);
        result.set_item("payload", PyBytes::new(py, &extracted.payload))?;
        result.set_item("source_sha256", extracted.source.source_sha256)?;
        result.set_item("resource_id", extracted.source.resource_id)?;
        result.set_item(
            "container_range",
            (
                extracted.source.container_range.start,
                extracted.source.container_range.end,
            ),
        )?;
        result.set_item(
            "decoded_range",
            (
                extracted.source.decoded_range.start,
                extracted.source.decoded_range.end,
            ),
        )?;
        result.set_item("payload_sha256", extracted.source.payload_sha256)?;
        result.set_item("encoding", extracted.source.encoding.as_str())?;
        Ok(result)
    }

    fn source_bytes<'py>(
        &self,
        py: Python<'py>,
        start: u64,
        end: u64,
    ) -> PyResult<Bound<'py, PyBytes>> {
        let data = self
            .inner
            .source_bytes(icad_core::ByteRange { start, end })
            .map_err(inspection_error)?;
        Ok(PyBytes::new(py, data))
    }
}
