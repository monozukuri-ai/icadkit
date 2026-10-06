//! Qualified native primitive records, separate from evaluated resource B-Rep.
use crate::{ByteRange, Diagnostic, ErrorKind, Status};

#[derive(Debug, Clone)]
pub struct NativePrimitiveRecord {
    pub kind: &'static str,
    /// Stored frame: origin, Z axis, X axis, in millimetres. all qualified versions
    /// require inverse(saved root frame) before exposing global placement.
    pub frame: [f64; 9],
    /// Box: height, xmin, ymin, xmax, ymax. Cylinder: radius, signed height.
    /// Full sphere: radius, repeated radius, pi. Cone: height, four zero offsets,
    /// base radius, top radius. Torus: full sweep, major radius, minor radius, zero.
    /// Polygon extrusion: signed height, then six distinct XY vertex pairs.
    /// Profile extrusion: signed height, then seven values per segment: start
    /// XY, end XY, arc centre XY and signed sweep (zeros for a line).
    /// Revolution: (radius, axial) pairs of the profile, closed along the axis.
    pub parameters: Vec<f64>,
}

#[derive(Debug, Clone)]
pub struct NativeEntityRecord {
    pub byte_range: ByteRange,
    pub source_id: Option<u32>,
    pub raw_type: Option<u32>,
    pub layer: Option<u32>,
    pub color_index: Option<u32>,
    pub visible: Option<bool>,
    pub is_mirror: Option<bool>,
    pub appearance_status: Status,
    pub geometry_status: Status,
    pub primitive: Option<NativePrimitiveRecord>,
    pub diagnostics: Vec<Diagnostic>,
}

fn word(bytes: &[u8], at: usize) -> u32 {
    u32::from_le_bytes([bytes[at], bytes[at + 1], bytes[at + 2], bytes[at + 3]])
}

impl NativeEntityRecord {
    fn issue(&mut self, kind: ErrorKind, code: &'static str, offset: u64, message: &str) {
        self.diagnostics.push(Diagnostic {
            kind,
            code,
            byte_offset: self.byte_range.start + offset,
            message: message.to_owned(),
        });
    }
}

fn undecoded_entity(bytes: &[u8], byte_range: ByteRange) -> NativeEntityRecord {
    NativeEntityRecord {
        byte_range,
        source_id: None,
        raw_type: (bytes.len() >= 16).then(|| word(bytes, 12) >> 24),
        layer: None,
        color_index: None,
        visible: None,
        is_mirror: None,
        appearance_status: Status::Unsupported,
        geometry_status: Status::Unsupported,
        primitive: None,
        diagnostics: Vec::new(),
    }
}

pub(crate) fn read_opaque_entity(bytes: &[u8], byte_range: ByteRange) -> NativeEntityRecord {
    let mut result = undecoded_entity(bytes, byte_range);
    result.issue(
        ErrorKind::Unsupported,
        "native.v7_owner",
        0,
        "V7L7 owner is not a complete standalone primitive list; entities remain opaque",
    );
    result
}

pub(crate) fn read_legacy_entity(bytes: &[u8], byte_range: ByteRange) -> NativeEntityRecord {
    let mut result = undecoded_entity(bytes, byte_range);
    result.issue(
        ErrorKind::Unsupported,
        "native.legacy_inventory",
        0,
        "Legacy entity layout or owner is not qualified; source range retained",
    );
    result
}

pub(crate) fn read_unqualified_owner_entity(
    bytes: &[u8],
    byte_range: ByteRange,
) -> NativeEntityRecord {
    let mut result = undecoded_entity(bytes, byte_range);
    result.issue(
        ErrorKind::Unsupported,
        "native.v8_owner",
        0,
        "V8L1/V8L2 owner is not a complete standalone primitive list; entities remain opaque",
    );
    result
}

/// Inventory of the observed parametric owner's saved entities. The body
/// marker (raw type 85), dimensions and hidden tables are never primitives.
pub(crate) fn read_parametric_entity(bytes: &[u8], byte_range: ByteRange) -> NativeEntityRecord {
    let mut result = undecoded_entity(bytes, byte_range);
    if bytes.len() >= 24
        && word(bytes, 4) > 0
        && word(bytes, 8) == 0
        && word(bytes, 16) & 0xf0000000 == 0x80000000
        && word(bytes, 20) == 0
        && matches!(
            word(bytes, 12),
            0x550108c0
                | 0x550108c1
                | 0x190d80c0
                | 0x190c80c0
                | 0x1b0c80c0
                | 0x1b0d80c0
                | 0x1a1080c0
                | 0x4d022081
                | 0x4d032081
                | 0x4d042081
                | 0x4d020081
                | 0x4d030081
                | 0x4d040081
        )
    {
        result.source_id = Some(word(bytes, 16));
        result.layer = Some(word(bytes, 4));
        result.visible = Some(word(bytes, 12) & 0x40 != 0);
    }
    result.issue(
        ErrorKind::Unsupported,
        "native.parametric_geometry",
        0,
        "parametric entity inventory does not evaluate final bodies, dimensions or condition tables",
    );
    result
}

pub(crate) fn read_entity(bytes: &[u8], byte_range: ByteRange) -> NativeEntityRecord {
    decode_entity(bytes, byte_range, false)
}

/// A component record of a saved boolean body: the standalone layout with the
/// component bit set, an optional explicit-result bit, and a result backlink
/// where a standalone record stores zero. The caller checks that backlink and
/// the saved program; nothing here establishes a finished solid.
pub(crate) fn read_component_entity(bytes: &[u8], byte_range: ByteRange) -> NativeEntityRecord {
    decode_entity(bytes, byte_range, true)
}

fn decode_entity(bytes: &[u8], byte_range: ByteRange, component: bool) -> NativeEntityRecord {
    let mut result = undecoded_entity(bytes, byte_range);
    // Layer, saved visibility and palette index are qualified only for these
    // exact native layouts. A Parasolid body has a different attribute layout.
    // Variable-length layouts carry their own length in the marker word.
    let variable = |tag: u32, fixed: usize, step: usize, least: usize| {
        let length = bytes.len();
        (length >= fixed + step * least
            && (length - fixed).is_multiple_of(step)
            && length - 24 <= 0xffff)
            .then(|| (length, tag << 24 | 0x00440000 | (length - 24) as u32))
    };
    let marker_byte = if bytes.len() >= 28 { bytes[27] } else { 0 };
    let shape = match (result.raw_type, marker_byte) {
        (Some(75), _) => Some((160, 0x56440088, "box")),
        (Some(71), _) => Some((136, 0x50440070, "cylinder")),
        (Some(72), _) => Some((144, 0x53440078, "sphere")),
        (Some(68), _) => Some((176, 0x57440098, "cone")),
        (Some(74), _) => Some((152, 0x54440080, "torus")),
        // Frame, height, at least three profile elements and a flag trailer.
        (Some(76), 0x59) => variable(0x59, 136, 16, 3).map(|(n, m)| (n, m, "profile_extrusion")),
        (Some(76), _) => Some((368, 0x5a440158, "polygon_extrusion")),
        // Frame and at least two (radius, axial) points.
        (Some(67), _) => variable(0x52, 120, 16, 2).map(|(n, m)| (n, m, "revolution")),
        _ => None,
    };
    let layout = shape.is_some_and(|(length, marker, kind)| {
        bytes.len() == length
            && word(bytes, 4) > 0
            && word(bytes, 8) == 0
            && if component {
                // Operands carry the component bit 0x20 and, under an explicit
                // result marker, bit 0x100. The mirror bit stays separate.
                word(bytes, 12) & 0x20 != 0
                    && matches!(word(bytes, 12) & 0x00ffeedf, 0x000100c1 | 0x00010081)
            } else {
                matches!(word(bytes, 12) & 0x00ffefff, 0x000100c1 | 0x00010081)
            }
            && word(bytes, 16) & 0xf0000000 == 0x80000000
            && word(bytes, 20) == 0
            && word(bytes, 24) == marker
            && (component || word(bytes, 32) == 0)
            // The upper word is retained as opaque producer metadata.
            && word(bytes, 40) & 0xffff == if kind == "polygon_extrusion" { 0x1b0 } else { 0x180 }
            && word(bytes, 44) == 0
    });
    if !layout {
        result.issue(
            ErrorKind::Unsupported,
            "native.layout",
            0,
            "Unqualified native entity layout; source range retained",
        );
        return result;
    }
    let (_, _, kind) = match shape {
        Some(shape) => shape,
        None => return result,
    };
    result.source_id = Some(word(bytes, 16));
    result.layer = Some(word(bytes, 4));
    result.visible = Some(word(bytes, 12) & 0x40 != 0);
    result.is_mirror = Some(word(bytes, 12) & 0x1000 != 0);
    // The high palette bit shares the byte holding line width. No RGB or
    // inherited/effective face colour is inferred from this saved index.
    let color = u32::from((bytes[29] & 15) | (bytes[28] & 16));
    if color == 0 {
        result.issue(
            ErrorKind::Unsupported,
            "native.color",
            28,
            "Unqualified zero palette index; source bytes retained",
        );
    } else {
        result.color_index = Some(color);
        result.appearance_status = Status::Complete;
    }
    let mirrored = result.is_mirror == Some(true);
    // The last eight bytes of a profile extrusion are element flags.
    let numeric = if kind == "profile_extrusion" {
        &bytes[48..bytes.len() - 8]
    } else {
        &bytes[48..]
    };
    let mut values = Vec::new();
    for raw in numeric.chunks_exact(8) {
        let mut value = [0; 8];
        value.copy_from_slice(raw);
        values.push(f64::from_le_bytes(value));
    }
    let finite = values.iter().all(|v| v.is_finite());
    let z = &values[3..6];
    let x = &values[6..9];
    let dot = |a: &[f64], b: &[f64]| a.iter().zip(b).map(|(a, b)| a * b).sum::<f64>();
    let orthonormal = (dot(z, z) - 1.0).abs() <= 1e-8
        && (dot(x, x) - 1.0).abs() <= 1e-8
        && dot(z, x).abs() <= 1e-8;
    if matches!(kind, "profile_extrusion" | "revolution") {
        // A profile checks its own elements: the second value of a sweep
        // element is arbitrary and must not invalidate the record.
        let checked = if kind == "revolution" {
            finite
        } else {
            values[..10].iter().all(|v| v.is_finite())
        };
        if !checked || !orthonormal {
            result.geometry_status = Status::Invalid;
            result.issue(
                ErrorKind::Invalid,
                "native.parameters",
                48,
                "Nonfinite parameters or non-rigid frame",
            );
            return result;
        }
        let outcome = if kind == "revolution" {
            revolution(&values[9..])
        } else {
            profile_extrusion(&values[9..], &bytes[bytes.len() - 8..])
        };
        match outcome {
            Ok(parameters) => {
                let mut frame = [0.0; 9];
                frame.copy_from_slice(&values[..9]);
                result.primitive = Some(NativePrimitiveRecord {
                    kind,
                    frame,
                    parameters,
                });
                result.geometry_status = Status::Complete;
            }
            Err((kind, code, text)) => {
                if kind == ErrorKind::Invalid {
                    result.geometry_status = Status::Invalid;
                }
                result.issue(kind, code, 120, text);
            }
        }
        return result;
    }
    let signed_height = if mirrored {
        values[9] < 0.0
    } else if matches!(kind, "box" | "polygon_extrusion") {
        // Saved nonmirrored boxes can extrude in either Z direction. The
        // source sign is independent of occurrence mirror parity.
        values[9] != 0.0
    } else {
        values[9] > 0.0
    };
    let dimensions = if kind == "box" {
        signed_height
            && values[12] > values[10]
            && values[13] > values[11]
            && (values[12] - values[10]).is_finite()
            && (values[13] - values[11]).is_finite()
    } else if kind == "polygon_extrusion" {
        signed_height
    } else if kind == "sphere" {
        values[9] > 0.0
    } else if kind == "cone" {
        signed_height && values[14] > 0.0 && values[15] >= 0.0
    } else if kind == "torus" {
        values[10] > 0.0 && values[11] > 0.0 && (values[10] + values[11]).is_finite()
    } else {
        // A cylinder's saved height can be negative with or without the
        // mirror flag: the solid then extends along the frame's negative Z.
        values[9] > 0.0 && values[10] != 0.0
    };
    if !finite || !orthonormal || !dimensions {
        result.geometry_status = Status::Invalid;
        result.issue(
            ErrorKind::Invalid,
            "native.parameters",
            48,
            "Nonfinite parameters, non-rigid frame or invalid primitive dimensions",
        );
        return result;
    }
    if kind == "sphere" && (values[10] != values[9] || values[11] != std::f64::consts::PI) {
        result.issue(
            ErrorKind::Unsupported,
            "native.sphere_extent",
            128,
            "Only the qualified full-sphere extent is supported",
        );
        return result;
    }
    if kind == "polygon_extrusion" {
        // Two identical closed seven-point profiles and a zero trailer are
        // qualified. Curved edges, holes, tapers and other layouts stay opaque.
        if values[10..24] != values[24..38]
            || values[10..12] != values[22..24]
            || values[38..40] != [0.0, 0.0]
        {
            result.issue(
                ErrorKind::Unsupported,
                "native.polygon_layout",
                128,
                "Only identical closed six-vertex straight profiles are qualified",
            );
            return result;
        }
        if !simple_polygon(&values[10..22]) {
            result.geometry_status = Status::Invalid;
            result.issue(
                ErrorKind::Invalid,
                "native.polygon",
                128,
                "Polygon is degenerate, self-intersecting or numerically unqualified",
            );
            return result;
        }
        values.truncate(22);
    }
    if (kind == "cone" && values[10..14].iter().any(|v| *v != 0.0))
        || (kind == "torus"
            && (values[9]
                != if mirrored {
                    -std::f64::consts::TAU
                } else {
                    std::f64::consts::TAU
                }
                || values[12] != 0.0
                || values[10] <= values[11]))
    {
        result.issue(
            ErrorKind::Unsupported,
            "native.primitive_extent",
            120,
            "Only coaxial circular cones and full ring tori are qualified",
        );
        return result;
    }
    let mut frame = [0.0; 9];
    frame.copy_from_slice(&values[..9]);
    result.primitive = Some(NativePrimitiveRecord {
        kind,
        frame,
        parameters: values[9..].to_vec(),
    });
    result.geometry_status = Status::Complete;
    result
}

type ShapeError = (ErrorKind, &'static str, &'static str);

/// A closed loop with nonzero area whose nonadjacent edges neither cross nor
/// touch and whose adjacent edges do not fold back. Collinear consecutive
/// vertices and disjoint collinear edges are accepted.
fn simple_loop(points: &[[f64; 2]]) -> bool {
    let n = points.len();
    if n < 3 {
        return false;
    }
    let scale = points
        .iter()
        .flat_map(|p| [(p[0] - points[0][0]).abs(), (p[1] - points[0][1]).abs()])
        .fold(0.0_f64, f64::max);
    let epsilon = 1e-12 * scale * scale;
    let reach = 1e-9 * scale;
    if !epsilon.is_finite() || epsilon == 0.0 {
        return false;
    }
    let cross = |a: [f64; 2], b: [f64; 2], c: [f64; 2]| {
        (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    };
    // A point already known to be collinear with a segment lies on it.
    let within = |a: [f64; 2], b: [f64; 2], p: [f64; 2]| {
        (0..2).all(|k| p[k] >= a[k].min(b[k]) - reach && p[k] <= a[k].max(b[k]) + reach)
    };
    let area: f64 = (1..n - 1)
        .map(|i| cross(points[0], points[i], points[i + 1]))
        .sum();
    if !area.is_finite() || area.abs() <= epsilon {
        return false;
    }
    for i in 0..n {
        let a = points[i];
        let b = points[(i + 1) % n];
        let c = points[(i + 2) % n];
        let dot = (b[0] - a[0]) * (c[0] - b[0]) + (b[1] - a[1]) * (c[1] - b[1]);
        if cross(a, b, c).abs() <= epsilon && dot <= 0.0 {
            return false;
        }
        for j in i + 2..n {
            if i == 0 && j == n - 1 {
                continue;
            }
            let c = points[j];
            let d = points[(j + 1) % n];
            let (d1, d2) = (cross(a, b, c), cross(a, b, d));
            let (d3, d4) = (cross(c, d, a), cross(c, d, b));
            let opposite =
                |x: f64, y: f64| (x > epsilon && y < -epsilon) || (x < -epsilon && y > epsilon);
            if (opposite(d1, d2) && opposite(d3, d4))
                || (d1.abs() <= epsilon && within(a, b, c))
                || (d2.abs() <= epsilon && within(a, b, d))
                || (d3.abs() <= epsilon && within(c, d, a))
                || (d4.abs() <= epsilon && within(c, d, b))
            {
                return false;
            }
        }
    }
    true
}

/// Closed profile of straight and circular segments. Elements are XY pairs.
/// A flagged pair of elements following a vertex describes an arc from that
/// vertex: its centre, then its signed sweep in the first value. The second
/// value of the sweep element is unqualified and ignored.
fn profile_extrusion(values: &[f64], flags: &[u8]) -> Result<Vec<f64>, ShapeError> {
    const LAYOUT: ShapeError = (
        ErrorKind::Unsupported,
        "native.profile_layout",
        "Unqualified profile element flags; source range retained",
    );
    const INVALID: ShapeError = (
        ErrorKind::Invalid,
        "native.profile",
        "Profile is open, degenerate, self-intersecting or has an invalid arc",
    );
    let height = values[0];
    let elements: Vec<[f64; 2]> = values[1..].chunks_exact(2).map(|v| [v[0], v[1]]).collect();
    let n = elements.len();
    let flagged = |i: usize| i < 64 && flags[i / 8] >> (7 - i % 8) & 1 == 1;
    if n > 64 || (n..64).any(flagged) || flagged(0) {
        return Err(LAYOUT);
    }
    // Within a run of flagged elements, centres and sweeps alternate.
    let sweep_slot = |i: usize| (0..i).rev().take_while(|k| flagged(*k)).count() % 2 == 1;
    let is_sweep = |i: usize| flagged(i) && sweep_slot(i);
    if elements
        .iter()
        .enumerate()
        .any(|(i, p)| !p[0].is_finite() || (!is_sweep(i) && !p[1].is_finite()))
    {
        return Err((
            ErrorKind::Invalid,
            "native.parameters",
            "Nonfinite profile coordinate or sweep",
        ));
    }
    if height == 0.0 {
        return Err(INVALID);
    }
    // Sweep elements are not coordinates; their second value is arbitrary.
    let scale = elements
        .iter()
        .enumerate()
        .filter(|(i, _)| !is_sweep(*i))
        .flat_map(|(_, p)| [p[0].abs(), p[1].abs()])
        .fold(1.0_f64, f64::max);
    let first = elements[0];
    let mut current = first;
    let mut segments = Vec::new();
    let mut outline = vec![first];
    let mut i = 1;
    while i < n {
        if flagged(i) {
            if i + 1 >= n || !flagged(i + 1) {
                return Err(LAYOUT);
            }
            let centre = elements[i];
            let sweep = elements[i + 1][0];
            let (dx, dy) = (current[0] - centre[0], current[1] - centre[1]);
            let radius = dx.hypot(dy);
            if radius <= 1e-9 * scale || sweep == 0.0 || sweep.abs() > std::f64::consts::TAU + 1e-9
            {
                return Err(INVALID);
            }
            // Chords no longer than a 1/32 turn approximate the arc for the
            // crossing test only; the exact arc is returned.
            let steps = (sweep.abs() / (std::f64::consts::TAU / 32.0))
                .ceil()
                .max(2.0) as usize;
            let mut end = current;
            for k in 1..=steps {
                let (sin, cos) = (sweep * k as f64 / steps as f64).sin_cos();
                end = [
                    centre[0] + dx * cos - dy * sin,
                    centre[1] + dx * sin + dy * cos,
                ];
                outline.push(end);
            }
            segments.extend([
                current[0], current[1], end[0], end[1], centre[0], centre[1], sweep,
            ]);
            current = end;
            i += 2;
        } else {
            let next = elements[i];
            if next != current {
                segments.extend([current[0], current[1], next[0], next[1], 0.0, 0.0, 0.0]);
                outline.push(next);
                current = next;
            }
            i += 1;
        }
    }
    // The outline must return to its first vertex, exactly or by a final arc.
    if (current[0] - first[0]).hypot(current[1] - first[1]) > 1e-7 * scale || outline.len() < 4 {
        return Err(INVALID);
    }
    outline.pop();
    if !simple_loop(&outline) {
        return Err(INVALID);
    }
    let mut parameters = vec![height];
    parameters.extend(segments);
    Ok(parameters)
}

/// Solid of revolution about the frame Z axis. The (radius, axial) polyline
/// is closed along the axis; no sweep angle is stored in this layout.
fn revolution(values: &[f64]) -> Result<Vec<f64>, ShapeError> {
    const INVALID: ShapeError = (
        ErrorKind::Invalid,
        "native.revolution",
        "Revolution profile has a negative radius, or is degenerate or self-intersecting",
    );
    let points: Vec<[f64; 2]> = values.chunks_exact(2).map(|v| [v[0], v[1]]).collect();
    if points.iter().any(|p| p[0] < 0.0) || points.windows(2).any(|w| w[0] == w[1]) {
        return Err(INVALID);
    }
    let mut outline = Vec::with_capacity(points.len() + 2);
    if points[0][0] > 0.0 {
        outline.push([0.0, points[0][1]]);
    }
    outline.extend_from_slice(&points);
    let last = points[points.len() - 1];
    if last[0] > 0.0 {
        outline.push([0.0, last[1]]);
    }
    if !simple_loop(&outline) {
        return Err(INVALID);
    }
    Ok(values.to_vec())
}

fn simple_polygon(values: &[f64]) -> bool {
    let points: Vec<_> = values.chunks_exact(2).map(|v| [v[0], v[1]]).collect();
    let n = points.len();
    let scale = points
        .iter()
        .flat_map(|p| [(p[0] - points[0][0]).abs(), (p[1] - points[0][1]).abs()])
        .fold(0.0_f64, f64::max);
    let epsilon = 1e-12 * scale * scale;
    if !epsilon.is_finite() || epsilon == 0.0 {
        return false;
    }
    let cross = |a: [f64; 2], b: [f64; 2], c: [f64; 2]| {
        (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    };
    let area: f64 = (1..n - 1)
        .map(|i| cross(points[0], points[i], points[i + 1]))
        .sum();
    if !area.is_finite() || area.abs() <= epsilon {
        return false;
    }
    for i in 0..n {
        let a = points[i];
        let b = points[(i + 1) % n];
        let turn = cross(a, b, points[(i + 2) % n]);
        if !turn.is_finite() || turn.abs() <= epsilon {
            return false;
        }
        for j in i + 1..n {
            if j == i + 1 || (i == 0 && j == n - 1) {
                continue;
            }
            let c = points[j];
            let d = points[(j + 1) % n];
            let signs = [
                cross(a, b, c),
                cross(a, b, d),
                cross(c, d, a),
                cross(c, d, b),
            ];
            if signs.iter().any(|v| !v.is_finite()) {
                return false;
            }
            // Conservative tolerance also rejects nonadjacent touching edges.
            let same_side =
                |x: f64, y: f64| (x > epsilon && y > epsilon) || (x < -epsilon && y < -epsilon);
            if !same_side(signs[0], signs[1]) && !same_side(signs[2], signs[3]) {
                return false;
            }
        }
    }
    true
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A closed rectangle profile of five straight elements with a flag trailer.
    fn record(flags: u32, backlink: u32) -> Vec<u8> {
        let mut bytes = vec![0_u8; 216];
        for (at, value) in [
            (0, 216_u32),
            (4, 1),
            (12, 76 << 24 | flags),
            (16, 0x8000_0003),
            (24, 0x5944_00c0),
            (32, backlink),
            (40, 0x180),
        ] {
            bytes[at..at + 4].copy_from_slice(&value.to_le_bytes());
        }
        bytes[29] = 0x11;
        let values: [f64; 20] = [
            0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 0.0, 0.0, 13.0, 0.0, 0.0, 10.0, 0.0, 10.0, 5.0, 0.0,
            5.0, 0.0, 0.0,
        ];
        for (i, value) in values.iter().enumerate() {
            bytes[48 + 8 * i..56 + 8 * i].copy_from_slice(&value.to_le_bytes());
        }
        bytes
    }

    const RANGE: ByteRange = ByteRange {
        start: 1000,
        end: 1216,
    };

    fn decoded(entity: &NativeEntityRecord) -> Option<(&'static str, usize)> {
        entity
            .primitive
            .as_ref()
            .map(|p| (p.kind, p.parameters.len()))
    }

    #[test]
    fn component_flags_select_the_reader() {
        let implicit = record(0x100e1, 0);
        assert!(read_entity(&implicit, RANGE).primitive.is_none());
        assert_eq!(
            read_entity(&implicit, RANGE).diagnostics[0].code,
            "native.layout"
        );
        let component = read_component_entity(&implicit, RANGE);
        assert_eq!(decoded(&component), Some(("profile_extrusion", 29)));
        assert_eq!(component.source_id, Some(0x8000_0003));
        assert_eq!(component.is_mirror, Some(false));
        // An explicit component keeps its result backlink; a standalone record
        // with the same word, or without the component bit, is not an operand.
        let explicit = record(0x101e1, 0x8000_0009);
        assert_eq!(
            decoded(&read_component_entity(&explicit, RANGE)),
            Some(("profile_extrusion", 29))
        );
        assert!(read_entity(&explicit, RANGE).primitive.is_none());
        assert!(read_entity(&record(0x100c1, 1), RANGE).primitive.is_none());
        let standalone = read_component_entity(&record(0x100c1, 0), RANGE);
        assert!(standalone.primitive.is_none());
        assert_eq!(standalone.diagnostics[0].code, "native.layout");
        assert_eq!(
            decoded(&read_entity(&record(0x100c1, 0), RANGE)),
            Some(("profile_extrusion", 29))
        );
    }
}
