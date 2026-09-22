//! Qualified native primitive records, separate from evaluated resource B-Rep.
use crate::{ByteRange, Diagnostic, ErrorKind, Status};

#[derive(Debug, Clone)]
pub struct NativePrimitiveRecord {
    pub kind: &'static str,
    /// Global frame: origin, Z axis, X axis. Values are in millimetres.
    pub frame: [f64; 9],
    /// Box: height, xmin, ymin, xmax, ymax. Cylinder: radius, height.
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

pub(crate) fn read_entity(bytes: &[u8], byte_range: ByteRange) -> NativeEntityRecord {
    let mut result = NativeEntityRecord {
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
    };
    // Layer, saved visibility and palette index are qualified only for these
    // exact native layouts. A Parasolid body has a different attribute layout.
    let shape = match result.raw_type {
        Some(75) => Some((160, 0x56440088, "box")),
        Some(71) => Some((136, 0x50440070, "cylinder")),
        Some(72) => Some((144, 0x53440078, "sphere")),
        _ => None,
    };
    let layout = shape.is_some_and(|(length, marker, _)| {
        bytes.len() == length
            && word(bytes, 4) > 0
            && word(bytes, 8) == 0
            && matches!(word(bytes, 12) & 0x00ffefff, 0x000100c1 | 0x00010081)
            && word(bytes, 16) & 0xf0000000 == 0x80000000
            && word(bytes, 20) == 0
            && word(bytes, 24) == marker
            && word(bytes, 32) == 0
            // The upper word is retained as opaque producer metadata.
            && word(bytes, 40) & 0xffff == 0x180
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
    if kind == "sphere" || result.is_mirror == Some(true) {
        result.issue(
            ErrorKind::Unsupported,
            "native.primitive",
            12,
            "Primitive parameters for this shape or mirror are not qualified",
        );
        return result;
    }
    let mut values = Vec::new();
    for raw in bytes[48..].chunks_exact(8) {
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
    let dimensions = if kind == "box" {
        values[9] > 0.0
            && values[12] > values[10]
            && values[13] > values[11]
            && (values[12] - values[10]).is_finite()
            && (values[13] - values[11]).is_finite()
    } else {
        values[9] > 0.0 && values[10] > 0.0
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
