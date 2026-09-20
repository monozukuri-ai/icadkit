use crate::geometry::page;
use parasolid_core::brep::*;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

fn source<'py>(py: Python<'py>, s: &SourceNodeRef) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    d.set_item("node_index", s.node_index)?;
    d.set_item("node_type", s.node_type)?;
    d.set_item("type_name", &s.type_name)?;
    d.set_item("node_id", s.node_id)?;
    d.set_item("decoded_range", (s.byte_range.start, s.byte_range.end))?;
    Ok(d)
}
fn optional_source<'py>(
    py: Python<'py>,
    s: &Option<SourceNodeRef>,
) -> PyResult<Option<Bound<'py, PyDict>>> {
    s.as_ref().map(|s| source(py, s)).transpose()
}
fn sources<'py>(py: Python<'py>, values: &[SourceNodeRef]) -> PyResult<Bound<'py, PyList>> {
    let out = PyList::empty(py);
    for v in values {
        out.append(source(py, v)?)?;
    }
    Ok(out)
}
pub fn summary<'py>(py: Python<'py>, b: &BrepModel) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    let counts = PyDict::new(py);
    counts.set_item("bodies", b.bodies.len())?;
    counts.set_item("regions", b.regions.len())?;
    counts.set_item("shells", b.shells.len())?;
    counts.set_item("faces", b.faces.len())?;
    counts.set_item("loops", b.loops.len())?;
    counts.set_item("half_edges", b.half_edges.len())?;
    counts.set_item("edges", b.edges.len())?;
    counts.set_item("vertices", b.vertices.len())?;
    counts.set_item("points", b.points.len())?;
    counts.set_item("curves", b.curves.len())?;
    counts.set_item("surfaces", b.surfaces.len())?;
    d.set_item("counts", counts)?;
    d.set_item("complete", b.complete)?;
    d.set_item("schema_key", &b.schema_key)?;
    d.set_item("source_format", b.source_format.as_str())?;
    d.set_item("topology_valid", b.topology.valid)?;
    d.set_item("closed_loop_count", b.topology.closed_loop_count)?;
    d.set_item("closed_edge_ring_count", b.topology.closed_edge_ring_count)?;
    d.set_item("euler_characteristic", b.topology.euler_characteristic)?;
    d.set_item(
        "vertex_bounds",
        b.metrics
            .bounding_box
            .as_ref()
            .map(|v| (v.minimum.to_array(), v.maximum.to_array())),
    )?;
    d.set_item("surface_area", b.metrics.surface_area)?;
    d.set_item("volume", b.metrics.volume)?;
    let curves = PyDict::new(py);
    let surfaces = PyDict::new(py);
    let mut cc = std::collections::BTreeMap::new();
    let mut sc = std::collections::BTreeMap::new();
    for c in &b.curves {
        *cc.entry(c.kind.as_str()).or_insert(0usize) += 1;
    }
    for c in &b.surfaces {
        *sc.entry(c.kind.as_str()).or_insert(0usize) += 1;
    }
    for (k, v) in cc {
        curves.set_item(k, v)?;
    }
    for (k, v) in sc {
        surfaces.set_item(k, v)?;
    }
    d.set_item("curve_kinds", curves)?;
    d.set_item("surface_kinds", surfaces)?;
    Ok(d)
}
fn curvekind(py: Python<'_>, d: &Bound<'_, PyDict>, kind: &CurveKind) -> PyResult<()> {
    d.set_item("kind", kind.as_str())?;
    match kind {
        CurveKind::Line { point, direction } => {
            d.set_item("point", point.to_array())?;
            d.set_item("direction", direction.to_array())?;
        }
        CurveKind::Circle {
            center,
            normal,
            x_axis,
            radius,
        } => {
            d.set_item("center", center.to_array())?;
            d.set_item("normal", normal.to_array())?;
            d.set_item("x_axis", x_axis.to_array())?;
            d.set_item("radius", radius)?;
        }
        CurveKind::Ellipse {
            center,
            normal,
            x_axis,
            major_radius,
            minor_radius,
        } => {
            d.set_item("center", center.to_array())?;
            d.set_item("normal", normal.to_array())?;
            d.set_item("x_axis", x_axis.to_array())?;
            d.set_item("major_radius", major_radius)?;
            d.set_item("minor_radius", minor_radius)?;
        }
        CurveKind::Parabola {
            origin,
            normal,
            x_axis,
            focal_length,
        } => {
            d.set_item("origin", origin.to_array())?;
            d.set_item("normal", normal.to_array())?;
            d.set_item("x_axis", x_axis.to_array())?;
            d.set_item("focal_length", focal_length)?;
        }
        CurveKind::Hyperbola {
            origin,
            normal,
            x_axis,
            transverse_radius,
            conjugate_radius,
        } => {
            d.set_item("origin", origin.to_array())?;
            d.set_item("normal", normal.to_array())?;
            d.set_item("x_axis", x_axis.to_array())?;
            d.set_item("transverse_radius", transverse_radius)?;
            d.set_item("conjugate_radius", conjugate_radius)?;
        }
        CurveKind::Trimmed {
            basis_curve,
            start_point,
            end_point,
            start_parameter,
            end_parameter,
        } => {
            d.set_item("basis_curve", basis_curve)?;
            d.set_item("start_point", start_point.to_array())?;
            d.set_item("end_point", end_point.to_array())?;
            d.set_item("start_parameter", start_parameter)?;
            d.set_item("end_parameter", end_parameter)?;
        }
        CurveKind::SurfaceParametric {
            surface,
            parameter_curve,
            original_curve,
            tolerance_to_original,
        } => {
            d.set_item("surface", surface)?;
            d.set_item("parameter_curve", parameter_curve)?;
            d.set_item("original_curve", original_curve)?;
            d.set_item("tolerance_to_original", tolerance_to_original)?;
        }
        CurveKind::Intersection {
            surfaces,
            chart,
            chart_points,
            start,
            start_points,
            end,
            end_points,
            intersection_data,
        } => {
            d.set_item("surfaces", surfaces)?;
            d.set_item("chart", source(py, chart)?)?;
            d.set_item(
                "chart_points",
                chart_points
                    .iter()
                    .map(|v| v.to_array())
                    .collect::<Vec<_>>(),
            )?;
            d.set_item("start", source(py, start)?)?;
            d.set_item(
                "start_points",
                start_points
                    .iter()
                    .map(|v| v.to_array())
                    .collect::<Vec<_>>(),
            )?;
            d.set_item("end", source(py, end)?)?;
            d.set_item(
                "end_points",
                end_points.iter().map(|v| v.to_array()).collect::<Vec<_>>(),
            )?;
            d.set_item("intersection_data", optional_source(py, intersection_data)?)?;
        }
        CurveKind::Unsupported { type_name } => {
            d.set_item("type_name", type_name)?;
        }
        CurveKind::Nurbs(n) => {
            d.set_item("degree", n.degree)?;
            d.set_item("control_vertex_count", n.control_vertex_count)?;
            d.set_item("vertex_dimension", n.vertex_dimension)?;
            d.set_item("knot_type", n.knot_type)?;
            d.set_item("periodic", n.periodic)?;
            d.set_item("closed", n.closed)?;
            d.set_item("rational", n.rational)?;
            d.set_item("curve_form", n.curve_form)?;
            d.set_item("control_vertices", &n.control_vertices)?;
            d.set_item("knots", &n.knots)?;
            d.set_item("knot_multiplicities", &n.knot_multiplicities)?;
            d.set_item("sources", sources(py, &n.sources)?)?;
        }
    }
    Ok(())
}
fn surfacekind(py: Python<'_>, d: &Bound<'_, PyDict>, kind: &SurfaceKind) -> PyResult<()> {
    d.set_item("kind", kind.as_str())?;
    match kind {
        SurfaceKind::Plane {
            point,
            normal,
            x_axis,
        } => {
            d.set_item("point", point.to_array())?;
            d.set_item("normal", normal.to_array())?;
            d.set_item("x_axis", x_axis.to_array())?;
        }
        SurfaceKind::Cylinder {
            point,
            axis,
            radius,
            x_axis,
        } => {
            d.set_item("point", point.to_array())?;
            d.set_item("axis", axis.to_array())?;
            d.set_item("radius", radius)?;
            d.set_item("x_axis", x_axis.to_array())?;
        }
        SurfaceKind::Cone {
            point,
            axis,
            radius,
            sin_half_angle,
            cos_half_angle,
            x_axis,
        } => {
            d.set_item("point", point.to_array())?;
            d.set_item("axis", axis.to_array())?;
            d.set_item("radius", radius)?;
            d.set_item("sin_half_angle", sin_half_angle)?;
            d.set_item("cos_half_angle", cos_half_angle)?;
            d.set_item("x_axis", x_axis.to_array())?;
        }
        SurfaceKind::Sphere {
            center,
            radius,
            axis,
            x_axis,
        } => {
            d.set_item("center", center.to_array())?;
            d.set_item("radius", radius)?;
            d.set_item("axis", axis.to_array())?;
            d.set_item("x_axis", x_axis.to_array())?;
        }
        SurfaceKind::Torus {
            center,
            axis,
            major_radius,
            minor_radius,
            x_axis,
        } => {
            d.set_item("center", center.to_array())?;
            d.set_item("axis", axis.to_array())?;
            d.set_item("major_radius", major_radius)?;
            d.set_item("minor_radius", minor_radius)?;
            d.set_item("x_axis", x_axis.to_array())?;
        }
        SurfaceKind::BlendedEdge {
            blend_type,
            supporting_surfaces,
            spine_curve,
            ranges,
            thumb_weights,
            boundary_surfaces,
            start,
            end,
        } => {
            d.set_item("blend_type", blend_type.as_str())?;
            d.set_item("supporting_surfaces", supporting_surfaces)?;
            d.set_item("spine_curve", spine_curve)?;
            d.set_item("ranges", ranges)?;
            d.set_item("thumb_weights", thumb_weights)?;
            d.set_item("boundary_surfaces", boundary_surfaces)?;
            d.set_item("start", optional_source(py, start)?)?;
            d.set_item("end", optional_source(py, end)?)?;
        }
        SurfaceKind::BlendBoundary {
            boundary_index,
            blend_surface,
        } => {
            d.set_item("boundary_index", boundary_index)?;
            d.set_item("blend_surface", blend_surface)?;
        }
        SurfaceKind::Offset {
            basis_surface,
            offset,
        } => {
            d.set_item("basis_surface", basis_surface)?;
            d.set_item("offset", offset)?;
        }
        SurfaceKind::Unsupported { type_name } => {
            d.set_item("type_name", type_name)?;
        }
        SurfaceKind::Nurbs(n) => {
            d.set_item("u_degree", n.u_degree)?;
            d.set_item("v_degree", n.v_degree)?;
            d.set_item("u_control_vertex_count", n.u_control_vertex_count)?;
            d.set_item("v_control_vertex_count", n.v_control_vertex_count)?;
            d.set_item("vertex_dimension", n.vertex_dimension)?;
            d.set_item("u_knot_type", n.u_knot_type)?;
            d.set_item("v_knot_type", n.v_knot_type)?;
            d.set_item("u_periodic", n.u_periodic)?;
            d.set_item("v_periodic", n.v_periodic)?;
            d.set_item("u_closed", n.u_closed)?;
            d.set_item("v_closed", n.v_closed)?;
            d.set_item("rational", n.rational)?;
            d.set_item("surface_form", n.surface_form)?;
            d.set_item("control_vertices", &n.control_vertices)?;
            d.set_item("u_knots", &n.u_knots)?;
            d.set_item("v_knots", &n.v_knots)?;
            d.set_item("u_knot_multiplicities", &n.u_knot_multiplicities)?;
            d.set_item("v_knot_multiplicities", &n.v_knot_multiplicities)?;
            d.set_item("sources", sources(py, &n.sources)?)?;
        }
    }
    Ok(())
}
pub fn entities<'py>(
    py: Python<'py>,
    b: &BrepModel,
    collection: &str,
    start: usize,
    count: usize,
) -> PyResult<Bound<'py, PyList>> {
    let out = PyList::empty(py);
    match collection {
        "bodies" => {
            for e in page(&b.bodies, start, count)? {
                let d = PyDict::new(py);
                d.set_item("id", e.id)?;
                d.set_item("source", source(py, &e.source)?)?;
                let a = PyDict::new(py);
                a.set_item("kind", e.kind.as_str())?;
                a.set_item("size_resolution", e.size_resolution)?;
                a.set_item("linear_resolution", e.linear_resolution)?;
                a.set_item("regions", &e.regions)?;
                a.set_item("edges", &e.edges)?;
                a.set_item("vertices", &e.vertices)?;
                d.set_item("attributes", a)?;
                out.append(d)?;
            }
        }
        "regions" => {
            for e in page(&b.regions, start, count)? {
                let d = PyDict::new(py);
                d.set_item("id", e.id)?;
                d.set_item("source", source(py, &e.source)?)?;
                let a = PyDict::new(py);
                a.set_item("kind", e.kind.as_str())?;
                a.set_item("body", e.body)?;
                a.set_item("shells", &e.shells)?;
                d.set_item("attributes", a)?;
                out.append(d)?;
            }
        }
        "shells" => {
            for e in page(&b.shells, start, count)? {
                let d = PyDict::new(py);
                d.set_item("id", e.id)?;
                d.set_item("source", source(py, &e.source)?)?;
                let a = PyDict::new(py);
                a.set_item("region", e.region)?;
                a.set_item("back_faces", &e.back_faces)?;
                a.set_item("front_faces", &e.front_faces)?;
                a.set_item("wire_edges", &e.wire_edges)?;
                a.set_item("isolated_vertex", e.isolated_vertex)?;
                d.set_item("attributes", a)?;
                out.append(d)?;
            }
        }
        "faces" => {
            for e in page(&b.faces, start, count)? {
                let d = PyDict::new(py);
                d.set_item("id", e.id)?;
                d.set_item("source", source(py, &e.source)?)?;
                let a = PyDict::new(py);
                a.set_item("back_shell", e.back_shell)?;
                a.set_item("front_shell", e.front_shell)?;
                a.set_item("loops", &e.loops)?;
                a.set_item("surface", e.surface)?;
                a.set_item("sense", e.sense.as_str())?;
                d.set_item("attributes", a)?;
                out.append(d)?;
            }
        }
        "loops" => {
            for e in page(&b.loops, start, count)? {
                let d = PyDict::new(py);
                d.set_item("id", e.id)?;
                d.set_item("source", source(py, &e.source)?)?;
                let a = PyDict::new(py);
                a.set_item("face", e.face)?;
                a.set_item("half_edges", &e.half_edges)?;
                d.set_item("attributes", a)?;
                out.append(d)?;
            }
        }
        "half_edges" => {
            for e in page(&b.half_edges, start, count)? {
                let d = PyDict::new(py);
                d.set_item("id", e.id)?;
                d.set_item("source", source(py, &e.source)?)?;
                let a = PyDict::new(py);
                a.set_item("loop_id", e.loop_id)?;
                a.set_item("forward", e.forward)?;
                a.set_item("backward", e.backward)?;
                a.set_item("vertex", e.vertex)?;
                a.set_item("other", e.other)?;
                a.set_item("edge", e.edge)?;
                a.set_item("curve", e.curve)?;
                a.set_item("sense", e.sense.as_str())?;
                a.set_item("dummy", e.dummy)?;
                d.set_item("attributes", a)?;
                out.append(d)?;
            }
        }
        "edges" => {
            for e in page(&b.edges, start, count)? {
                let d = PyDict::new(py);
                d.set_item("id", e.id)?;
                d.set_item("source", source(py, &e.source)?)?;
                let a = PyDict::new(py);
                a.set_item("owner", source(py, &e.owner)?)?;
                a.set_item("half_edges", &e.half_edges)?;
                a.set_item("start_vertex", e.start_vertex)?;
                a.set_item("end_vertex", e.end_vertex)?;
                a.set_item("curve", e.curve)?;
                a.set_item("tolerance", e.tolerance)?;
                d.set_item("attributes", a)?;
                out.append(d)?;
            }
        }
        "vertices" => {
            for e in page(&b.vertices, start, count)? {
                let d = PyDict::new(py);
                d.set_item("id", e.id)?;
                d.set_item("source", source(py, &e.source)?)?;
                let a = PyDict::new(py);
                a.set_item("point", e.point)?;
                a.set_item("tolerance", e.tolerance)?;
                a.set_item("owner", source(py, &e.owner)?)?;
                d.set_item("attributes", a)?;
                out.append(d)?;
            }
        }
        "points" => {
            for e in page(&b.points, start, count)? {
                let d = PyDict::new(py);
                d.set_item("id", e.id)?;
                d.set_item("source", source(py, &e.source)?)?;
                let a = PyDict::new(py);
                a.set_item("position", e.position.to_array())?;
                a.set_item("owner", optional_source(py, &e.owner)?)?;
                d.set_item("attributes", a)?;
                out.append(d)?;
            }
        }
        "curves" => {
            for e in page(&b.curves, start, count)? {
                let d = PyDict::new(py);
                d.set_item("id", e.id)?;
                d.set_item("source", source(py, &e.source)?)?;
                let a = PyDict::new(py);
                a.set_item("sense", e.sense.as_str())?;
                a.set_item("owner", optional_source(py, &e.owner)?)?;
                curvekind(py, &a, &e.kind)?;
                d.set_item("attributes", a)?;
                out.append(d)?;
            }
        }
        "surfaces" => {
            for e in page(&b.surfaces, start, count)? {
                let d = PyDict::new(py);
                d.set_item("id", e.id)?;
                d.set_item("source", source(py, &e.source)?)?;
                let a = PyDict::new(py);
                a.set_item("sense", e.sense.as_str())?;
                a.set_item("owner", optional_source(py, &e.owner)?)?;
                surfacekind(py, &a, &e.kind)?;
                d.set_item("attributes", a)?;
                out.append(d)?;
            }
        }
        _ => return Err(PyValueError::new_err("unknown B-Rep collection")),
    }
    Ok(out)
}
