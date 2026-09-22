//! Bounded V7L7/V8L3 part records and owned entity lists.

use std::collections::{BTreeMap, BTreeSet};

use crate::{ByteOrder, ByteRange, Diagnostic, Document, ErrorKind, InspectError, Status};

#[derive(Debug, Clone, Copy)]
pub struct PartLimits {
    pub max_parts: usize,
    pub max_entities: usize,
    pub max_depth: usize,
    pub max_property_bytes: usize,
}

impl Default for PartLimits {
    fn default() -> Self {
        Self {
            max_parts: 100_000,
            max_entities: 500_000,
            max_depth: 256,
            max_property_bytes: 1_048_576,
        }
    }
}

#[derive(Debug, Clone)]
pub struct PartRecord {
    pub byte_range: ByteRange,
    pub view_offset: u64,
    pub source_id: u32,
    pub flags: u32,
    pub is_root: bool,
    pub raw_name: Vec<u8>,
    pub raw_comment: Vec<u8>,
    /// Nine stored doubles; no assumption about local/world coordinates.
    pub placement_values: [f64; 9],
    /// Stored part frame. V7L7 requires inverse(saved root frame) for global placement.
    pub coordinate_values: [f64; 9],
    pub raw_reference_name: Vec<u8>,
    pub extra_fields: Vec<PartTextRecord>,
    pub entities: Vec<crate::NativeEntityRecord>,
    pub parent_source_id: u32,
    pub first_child_source_id: u32,
    pub previous_source_id: u32,
    pub next_source_id: u32,
}

#[derive(Debug, Clone)]
pub struct PartTextRecord {
    pub byte_range: ByteRange,
    pub raw_value: Vec<u8>,
}

#[derive(Debug, Clone)]
pub struct PartOpaqueRange {
    pub byte_range: ByteRange,
    pub reason: &'static str,
}

#[derive(Debug, Clone)]
pub struct PartIndex {
    pub parts: Vec<PartRecord>,
    pub opaque_ranges: Vec<PartOpaqueRange>,
    pub diagnostics: Vec<Diagnostic>,
    pub index_status: Status,
    pub hierarchy_status: Status,
}

fn word(bytes: &[u8], at: usize) -> u32 {
    u32::from_le_bytes([bytes[at], bytes[at + 1], bytes[at + 2], bytes[at + 3]])
}

fn limit(at: u64, code: &'static str, message: &str) -> InspectError {
    InspectError::format(ErrorKind::LimitExceeded, code, at, message)
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum PartProfile {
    V7L7,
    V8L3,
}

impl PartProfile {
    fn from_header(order: ByteOrder, version: [u8; 4]) -> Option<Self> {
        if order != ByteOrder::Little {
            return None;
        }
        match version {
            [0, 7, 0, 7] => Some(Self::V7L7),
            [0, 8, 0, 3] => Some(Self::V8L3),
            _ => None,
        }
    }

    fn part_tag(self) -> u32 {
        match self {
            Self::V7L7 => 0x61000002,
            Self::V8L3 => 0x61000003,
        }
    }

    fn metadata_layout(self, length: u32, count: u32) -> bool {
        // Exact observed framing pairs, not a general metadata decoder.
        // Payloads stay opaque; a nearby length or count is not compatible.
        match self {
            Self::V7L7 => matches!(
                (count, length),
                (
                    1,
                    176 | 184 | 192 | 200 | 216 | 240 | 248 | 256 | 272 | 304 | 336 | 352 | 360
                ) | (2, 392 | 400 | 416 | 440 | 704)
            ),
            Self::V8L3 => count == 2 && matches!(length, 440 | 504),
        }
    }
}

impl PartIndex {
    fn issue(&mut self, kind: ErrorKind, code: &'static str, at: u64, message: &str) {
        self.diagnostics.push(Diagnostic {
            kind,
            code,
            byte_offset: at,
            message: message.to_owned(),
        });
    }

    fn opaque(&mut self, start: u64, end: u64, reason: &'static str) {
        if start < end {
            self.opaque_ranges.push(PartOpaqueRange {
                byte_range: ByteRange { start, end },
                reason,
            });
        }
    }

    fn hierarchy(&mut self, limits: PartLimits) -> Result<(), InspectError> {
        let mut by_id = BTreeMap::new();
        let mut problem = None;
        for (i, p) in self.parts.iter().enumerate() {
            if by_id.insert((p.view_offset, p.source_id), i).is_some() {
                problem = Some((
                    p.byte_range.start + 16,
                    "parts.duplicate_id",
                    "duplicate part source ID",
                ));
                break;
            }
        }
        if problem.is_none() {
            let mut linked = BTreeSet::new();
            let mut roots = 0;
            for p in &self.parts {
                if p.is_root {
                    roots += 1;
                }
                if (p.is_root
                    && (p.parent_source_id != 0
                        || p.previous_source_id != 0
                        || p.next_source_id != 0))
                    || (!p.is_root
                        && (p.parent_source_id == 0
                            || !by_id.contains_key(&(p.view_offset, p.parent_source_id))))
                {
                    problem = Some((
                        p.byte_range.start + 260,
                        "parts.parent",
                        "missing or inconsistent parent reference",
                    ));
                    break;
                }
                let mut child = p.first_child_source_id;
                let mut previous = 0;
                while child != 0 {
                    let key = (p.view_offset, child);
                    let Some(&i) = by_id.get(&key) else {
                        problem = Some((
                            p.byte_range.start + 264,
                            "parts.child",
                            "missing child reference",
                        ));
                        break;
                    };
                    let c = &self.parts[i];
                    if !linked.insert(key)
                        || c.is_root
                        || c.parent_source_id != p.source_id
                        || c.previous_source_id != previous
                    {
                        problem = Some((
                            c.byte_range.start + 260,
                            "parts.child_chain",
                            "inconsistent or cyclic child/sibling chain",
                        ));
                        break;
                    }
                    previous = child;
                    child = c.next_source_id;
                }
                if problem.is_some() {
                    break;
                }
            }
            if problem.is_none() && (roots != 1 || linked.len() + roots != self.parts.len()) {
                problem = Some((
                    0,
                    "parts.roots",
                    "expected one root and a child link for every non-root part",
                ));
            }
            // Memoized parent depths bound both cycles and deep inputs without recursion.
            if problem.is_none() {
                let mut depths = vec![None; self.parts.len()];
                for start in 0..self.parts.len() {
                    let mut path = Vec::new();
                    let mut seen = BTreeSet::new();
                    let mut i = start;
                    let depth = loop {
                        if let Some(depth) = depths[i] {
                            break depth;
                        }
                        if !seen.insert(i) {
                            problem = Some((
                                self.parts[i].byte_range.start,
                                "parts.cycle",
                                "cyclic parent references",
                            ));
                            break 0;
                        }
                        if path.len() >= limits.max_depth {
                            return Err(limit(
                                self.parts[i].byte_range.start,
                                "limit.part_depth",
                                "part hierarchy exceeds max_depth",
                            ));
                        }
                        path.push(i);
                        let p = &self.parts[i];
                        if p.is_root {
                            break 0;
                        }
                        let Some(&parent) = by_id.get(&(p.view_offset, p.parent_source_id)) else {
                            break 0;
                        };
                        i = parent;
                    };
                    if problem.is_some() {
                        break;
                    }
                    let mut depth = depth;
                    for i in path.into_iter().rev() {
                        depth += 1;
                        if depth > limits.max_depth {
                            return Err(limit(
                                self.parts[i].byte_range.start,
                                "limit.part_depth",
                                "part hierarchy exceeds max_depth",
                            ));
                        }
                        depths[i] = Some(depth);
                    }
                }
            }
        }
        if let Some((at, code, message)) = problem {
            self.issue(ErrorKind::Invalid, code, at, message);
            self.hierarchy_status = Status::Invalid;
        } else {
            self.hierarchy_status = self.index_status;
        }
        Ok(())
    }
}

impl Document {
    /// Read observed little-endian V7L7/V8L3 3DGLOBAL part-record layouts.
    /// Unknown entities stop the view; their bytes are never scanned for parts.
    pub fn read_parts(&self, limits: PartLimits) -> Result<PartIndex, InspectError> {
        if limits.max_parts == 0
            || limits.max_entities == 0
            || limits.max_depth == 0
            || limits.max_property_bytes == 0
        {
            return Err(limit(0, "limit.invalid", "part limits must be positive"));
        }
        let mut result = PartIndex {
            parts: Vec::new(),
            opaque_ranges: Vec::new(),
            diagnostics: Vec::new(),
            index_status: Status::Unsupported,
            hierarchy_status: Status::Unsupported,
        };
        let h = &self.inspection().header;
        let Some(profile) = PartProfile::from_header(h.byte_order, h.raw_version) else {
            result.issue(
                ErrorKind::Unsupported,
                "parts.profile",
                12,
                "part records require an observed little-endian V7L7 or V8L3 profile",
            );
            for r in self.records().iter().filter(|r| r.tag == "V/W") {
                result.opaque(
                    r.payload_range.start,
                    r.payload_range.end,
                    "unsupported_view",
                );
            }
            return Ok(result);
        };
        let mut views = 0;
        let mut entities = 0;
        for view in self.records().iter().filter(|r| r.tag == "V/W") {
            let bytes = self.source_bytes(view.byte_range)?;
            let base = view.byte_range.start;
            if bytes.len() < 804 || bytes.get(564..572) != Some(b"3DGLOBAL") {
                result.opaque(
                    view.payload_range.start,
                    view.payload_range.end,
                    "unsupported_view",
                );
                continue;
            }
            views += 1;
            if views > 1 {
                if result.index_status != Status::Invalid {
                    result.index_status = Status::Partial;
                }
                result.issue(
                    ErrorKind::Unsupported,
                    "parts.multiple_views",
                    base,
                    "multiple 3DGLOBAL views are not qualified",
                );
                result.opaque(base + 8, view.payload_range.end, "unsupported_view");
                continue;
            }
            // These markers anchor the fixed view header, rather than a signature search.
            if word(bytes, 16) != 132
                || word(bytes, 20) != 60
                || word(bytes, 28) != 0xff12
                || word(bytes, 44) != 0x02000110
                || word(bytes, 556) != 0x00800011
            {
                result.issue(
                    ErrorKind::Unsupported,
                    "parts.view_layout",
                    base,
                    "unqualified 3D view header",
                );
                result.opaque(base + 8, view.payload_range.end, "unsupported_view");
                continue;
            }
            result.index_status = Status::Complete;
            result.opaque(base + 8, base + 796, "view_metadata");
            let mut at = 796;
            let mut in_entities = false;
            let end = bytes.len() - 4;
            loop {
                if at + 4 > end {
                    result.index_status = Status::Invalid;
                    result.issue(
                        ErrorKind::Invalid,
                        "parts.missing_end",
                        base + at as u64,
                        "missing view entity terminator",
                    );
                    break;
                }
                let tag = word(bytes, at);
                if tag == 0xfe000000 {
                    if at + 4 != end {
                        result.index_status = Status::Partial;
                        result.issue(
                            ErrorKind::Unsupported,
                            "parts.after_end",
                            base + at as u64 + 4,
                            "bytes after view entity terminator",
                        );
                        result.opaque(base + at as u64 + 4, base + end as u64, "unparsed_entities");
                    }
                    break;
                }
                if matches!(tag, 0x61000002 | 0x61000003) && tag != profile.part_tag() {
                    result.index_status = if result.parts.is_empty() {
                        Status::Unsupported
                    } else {
                        Status::Partial
                    };
                    result.issue(
                        ErrorKind::Unsupported,
                        "parts.record_profile",
                        base + at as u64,
                        "part record tag does not match the saved V7L7/V8L3 profile",
                    );
                    result.opaque(base + at as u64, base + end as u64, "unparsed_entities");
                    break;
                }
                // Metadata has its own framing, outside the length-prefixed
                // entity group. Never scan its payload for part signatures.
                if tag == 0x21000000 && !in_entities && !result.parts.is_empty() {
                    if end - at < 20 {
                        result.index_status = Status::Invalid;
                        result.issue(
                            ErrorKind::Invalid,
                            "parts.metadata_length",
                            base + at as u64,
                            "truncated metadata header",
                        );
                        result.opaque(base + at as u64, base + end as u64, "unparsed_entities");
                        break;
                    }
                    let length = word(bytes, at + 4);
                    if length < 16 || !length.is_multiple_of(4) || length as usize > end - at - 4 {
                        result.index_status = Status::Invalid;
                        result.issue(
                            ErrorKind::Invalid,
                            "parts.metadata_length",
                            base + at as u64 + 4,
                            "invalid metadata byte length",
                        );
                        result.opaque(base + at as u64, base + end as u64, "unparsed_entities");
                        break;
                    }
                    if !profile.metadata_layout(length, word(bytes, at + 8))
                        || word(bytes, at + 12) != 0
                        || !(word(bytes, at + 16) == 0xc9500088
                            || (profile == PartProfile::V7L7 && word(bytes, at + 16) == 0xc9580088))
                    {
                        result.index_status = Status::Partial;
                        result.issue(
                            ErrorKind::Unsupported,
                            "parts.metadata_layout",
                            base + at as u64,
                            "unqualified metadata layout; subsequent bytes remain opaque",
                        );
                        result.opaque(base + at as u64, base + end as u64, "unparsed_entities");
                        break;
                    }
                    if entities >= limits.max_entities {
                        return Err(limit(
                            base + at as u64,
                            "limit.part_entities",
                            "view entity count exceeds max_entities",
                        ));
                    }
                    entities += 1;
                    let next = at + length as usize + 4;
                    result.opaque(base + at as u64, base + next as u64, "entity_metadata");
                    at = next;
                    continue;
                }
                // A list marker is followed by length-prefixed entities. Only the
                // first entity has this marker; subsequent entities begin with
                // their own byte length, not another marker.
                if tag == 0x30010000 && !in_entities && !result.parts.is_empty() {
                    if end - at >= 8
                        && matches!(word(bytes, at + 4), 0xfe000000 | 0x61000002 | 0x61000003)
                    {
                        result.index_status = Status::Partial;
                        result.issue(
                            ErrorKind::Unsupported,
                            "parts.empty_entity_group",
                            base + at as u64,
                            "Empty native entity group is not qualified",
                        );
                        result.opaque(base + at as u64, base + end as u64, "unparsed_entities");
                        break;
                    }
                    in_entities = true;
                    at += 4;
                    continue;
                }
                if entities >= limits.max_entities {
                    return Err(limit(
                        base + at as u64,
                        "limit.part_entities",
                        "view entity count exceeds max_entities",
                    ));
                }
                entities += 1;
                if tag != profile.part_tag() && !in_entities {
                    result.index_status = Status::Partial;
                    result.issue(
                        ErrorKind::Unsupported,
                        "parts.entity",
                        base + at as u64,
                        "unknown view entity; subsequent bytes remain opaque",
                    );
                    result.opaque(base + at as u64, base + end as u64, "unparsed_entities");
                    break;
                }
                if at + 8 > end {
                    result.index_status = Status::Invalid;
                    result.issue(
                        ErrorKind::Invalid,
                        "parts.truncated",
                        base + at as u64,
                        "truncated entity header",
                    );
                    result.opaque(base + at as u64, base + end as u64, "invalid_entity");
                    break;
                }
                let is_part = tag == profile.part_tag();
                let prefix = if is_part { 4 } else { 0 };
                let length = word(bytes, at + prefix) as usize;
                if length < 4 || !length.is_multiple_of(4) || length > end - at - prefix {
                    result.index_status = Status::Invalid;
                    result.issue(
                        ErrorKind::Invalid,
                        "parts.length",
                        base + (at + prefix) as u64,
                        "invalid entity byte length",
                    );
                    result.opaque(base + at as u64, base + end as u64, "invalid_entity");
                    break;
                }
                let next = at + length + prefix;
                if is_part {
                    in_entities = false;
                    if length != 352 {
                        result.index_status = Status::Partial;
                        result.issue(
                            ErrorKind::Unsupported,
                            "parts.record_layout",
                            base + at as u64,
                            "unqualified part record length",
                        );
                        result.opaque(base + at as u64, base + end as u64, "unparsed_entities");
                        break;
                    }
                    if result.parts.len() >= limits.max_parts {
                        return Err(limit(
                            base + at as u64,
                            "limit.parts",
                            "part count exceeds max_parts",
                        ));
                    }
                    let flags = word(bytes, at + 8);
                    let source_id = word(bytes, at + 16);
                    // Saving a model used as an external reference can change
                    // its root source ID from E... to A.... Root flags and the
                    // parent/child graph, not the ID prefix alone, identify it.
                    let is_root =
                        flags == 64 && matches!(source_id & 0xf0000000, 0xe0000000 | 0xa0000000);
                    if !is_root
                        && (!matches!(flags, 0 | 8 | 0x50 | 0x58)
                            || source_id & 0xf0000000 != 0xa0000000)
                    {
                        result.index_status = Status::Partial;
                        result.issue(
                            ErrorKind::Unsupported,
                            "parts.kind",
                            base + at as u64,
                            "part kind is outside the qualified internal/external layouts",
                        );
                    }
                    let mut values = [0.0; 9];
                    let mut coordinates = [0.0; 9];
                    for (i, v) in values.iter_mut().enumerate() {
                        let p = at + 108 + i * 8;
                        let mut raw = [0; 8];
                        raw.copy_from_slice(&bytes[p..p + 8]);
                        *v = f64::from_le_bytes(raw);
                        raw.copy_from_slice(&bytes[p + 72..p + 80]);
                        coordinates[i] = f64::from_le_bytes(raw);
                    }
                    result.parts.push(PartRecord {
                        byte_range: ByteRange {
                            start: base + at as u64,
                            end: base + next as u64,
                        },
                        view_offset: base,
                        source_id,
                        flags,
                        is_root,
                        raw_name: bytes[at + 20..at + 60].to_vec(),
                        raw_comment: bytes[at + 60..at + 108].to_vec(),
                        placement_values: values,
                        coordinate_values: coordinates,
                        raw_reference_name: bytes[at + 276..at + 316].to_vec(),
                        extra_fields: Vec::new(),
                        entities: Vec::new(),
                        parent_source_id: word(bytes, at + 260),
                        first_child_source_id: word(bytes, at + 264),
                        previous_source_id: word(bytes, at + 268),
                        next_source_id: word(bytes, at + 272),
                    });
                    for (start, stop) in [(12, 16), (252, 260), (316, 356)] {
                        result.opaque(
                            base + (at + start) as u64,
                            base + (at + stop) as u64,
                            "part_metadata",
                        );
                    }
                    if !matches!(flags, 0x50 | 0x58) {
                        result.opaque(
                            base + at as u64 + 276,
                            base + at as u64 + 316,
                            "part_metadata",
                        );
                    }
                } else if length >= 24 && word(bytes, at + 12) == 0xcf010081 {
                    if length >= 32
                        && word(bytes, at + 4) == 1
                        && word(bytes, at + 8) == 0
                        && word(bytes, at + 20) == 0
                        && word(bytes, at + 24) & 0xff000000 == 0xfd000000
                        && (word(bytes, at + 24) & 0x00ffffff) as usize == length - 24
                        && word(bytes, at + 28) == 0x10000000
                    {
                        if length - 32 > limits.max_property_bytes {
                            return Err(limit(
                                base + at as u64,
                                "limit.part_property_bytes",
                                "part property exceeds max_property_bytes",
                            ));
                        }
                        if let Some(part) = result.parts.last_mut() {
                            part.extra_fields.push(PartTextRecord {
                                byte_range: ByteRange {
                                    start: base + at as u64 + 32,
                                    end: base + next as u64,
                                },
                                raw_value: bytes[at + 32..next].to_vec(),
                            });
                        }
                        result.opaque(
                            base + at as u64,
                            base + at as u64 + 32,
                            "attribute_metadata",
                        );
                    } else {
                        result.issue(
                            ErrorKind::Unsupported,
                            "parts.attribute_layout",
                            base + at as u64,
                            "unqualified extended part information layout",
                        );
                        result.index_status = Status::Partial;
                        result.opaque(base + at as u64, base + next as u64, "unknown_attribute");
                    }
                } else {
                    if let Some(part) = result.parts.last_mut() {
                        let range = ByteRange {
                            start: base + at as u64,
                            end: base + next as u64,
                        };
                        part.entities.push(if profile == PartProfile::V8L3 {
                            crate::native::read_entity(&bytes[at..next], range)
                        } else {
                            crate::native::read_opaque_entity(&bytes[at..next], range)
                        });
                    }
                    result.opaque(base + at as u64, base + next as u64, "geometry_entity");
                }
                at = next;
            }
        }
        if views == 0 {
            result.issue(
                ErrorKind::Unsupported,
                "parts.no_3d_view",
                0,
                "no qualified 3DGLOBAL view",
            );
        }
        if !result.parts.is_empty() {
            result.hierarchy(limits)?;
        } else {
            if result.index_status == Status::Complete {
                result.index_status = Status::Invalid;
                result.issue(
                    ErrorKind::Invalid,
                    "parts.no_root",
                    0,
                    "qualified 3D view has no document root part",
                );
            }
            result.hierarchy_status = result.index_status;
        }
        // V7L7 CSG operands can have primitive-shaped records. Only promote a
        // complete owner's list when every entity has a qualified standalone
        // layout. An unknown sibling, mirror, external owner, or unparsed tail
        // keeps the entire owner's list opaque, never a guessed final shape.
        if profile == PartProfile::V7L7
            && result.index_status == Status::Complete
            && result.hierarchy_status == Status::Complete
        {
            for part in &mut result.parts {
                if !matches!(part.flags, 0 | 64) {
                    continue;
                }
                let candidates = part
                    .entities
                    .iter()
                    .map(|e| {
                        self.source_bytes(e.byte_range)
                            .map(|bytes| crate::native::read_entity(bytes, e.byte_range))
                    })
                    .collect::<Result<Vec<_>, _>>()?;
                if candidates
                    .iter()
                    .all(|e| e.source_id.is_some() && e.is_mirror == Some(false))
                {
                    part.entities = candidates;
                }
            }
        }
        Ok(result)
    }
}
