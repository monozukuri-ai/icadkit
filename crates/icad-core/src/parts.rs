//! Bounded V7L2 through V7L7 and V8L1 through V8L3 part records.

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
    /// Stored part frame; qualified profiles normalize by inverse(saved root frame).
    pub coordinate_values: [f64; 9],
    pub raw_reference_name: Vec<u8>,
    pub extra_fields: Vec<PartTextRecord>,
    pub opaque_attributes: Vec<PartAttributeRecord>,
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
pub struct PartAttributeRecord {
    pub byte_range: ByteRange,
    pub source_id: u32,
    pub subtype: u32,
    pub raw_bytes: Vec<u8>,
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
    /// A profile is emitted only after matching a view header and part layout.
    /// Its policies do not override per-owner diagnostics or completeness.
    pub profile: Option<PartProfileInfo>,
    pub views: Vec<PartViewRecord>,
}

#[derive(Debug, Clone)]
pub struct PartViewRecord {
    pub byte_range: ByteRange,
    pub kind: &'static str,
}

#[derive(Debug, Clone)]
pub struct PartProfileInfo {
    pub profile_id: &'static str,
    pub byte_order: ByteOrder,
    pub raw_version: [u8; 4],
    pub part_tag: u32,
    pub coordinate_convention: &'static str,
    pub entity_policy: &'static str,
    pub source_length_unit: Option<&'static str>,
    pub saved_body_layout: Option<&'static str>,
    pub csg_layout: Option<&'static str>,
    pub mirror_policy: Option<&'static str>,
    pub view_byte_range: ByteRange,
    pub part_byte_range: ByteRange,
}

fn word(bytes: &[u8], at: usize) -> u32 {
    u32::from_le_bytes([bytes[at], bytes[at + 1], bytes[at + 2], bytes[at + 3]])
}

fn limit(at: u64, code: &'static str, message: &str) -> InspectError {
    InspectError::format(ErrorKind::LimitExceeded, code, at, message)
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum PartProfile {
    V7L2,
    V7L3,
    V7L4,
    V7L5,
    V7L6,
    V7L7,
    V8L1,
    V8L2,
    V8L3,
}

impl PartProfile {
    fn describe(
        self,
        raw_version: [u8; 4],
        view_byte_range: ByteRange,
        part_byte_range: ByteRange,
    ) -> PartProfileInfo {
        let (profile_id, coordinate_convention, entity_policy, unit, saved, csg) = match self {
            Self::V7L2 => (
                "icad-v7l2-parts-r1",
                "root_relative",
                "opaque_entities",
                Some("mm"),
                None,
                None,
            ),
            Self::V7L3 => (
                "icad-v7l3-parts-r1",
                "root_relative",
                "opaque_entities",
                Some("mm"),
                None,
                None,
            ),
            Self::V7L4 => (
                "icad-v7l4-parts-r1",
                "root_relative",
                "opaque_entities",
                Some("mm"),
                None,
                None,
            ),
            Self::V7L5 => (
                "icad-v7l5-parts-r1",
                "root_relative",
                "opaque_entities",
                Some("mm"),
                None,
                None,
            ),
            Self::V7L6 => (
                "icad-v7l6-parts-r2",
                "root_relative",
                "standalone_owner",
                Some("mm"),
                Some("v7l6_source_id"),
                None,
            ),
            Self::V7L7 => (
                "icad-v7l7-parts-r2",
                "root_relative",
                "standalone_owner",
                Some("mm"),
                Some("v7_source_id"),
                Some("v7_postfix"),
            ),
            Self::V8L1 => (
                "icad-v8l1-parts-r3",
                "root_relative",
                "standalone_owner",
                Some("mm"),
                Some("v8l1_saved_body"),
                None,
            ),
            Self::V8L2 => (
                "icad-v8l2-parts-r3",
                "root_relative",
                "standalone_owner",
                Some("mm"),
                Some("v8_resource_key"),
                None,
            ),
            Self::V8L3 => (
                "icad-v8l3-parts-r3",
                "root_relative",
                "saved_entities",
                Some("mm"),
                Some("v8_resource_key"),
                None,
            ),
        };
        PartProfileInfo {
            profile_id,
            byte_order: ByteOrder::Little,
            raw_version,
            part_tag: self.part_tag(),
            coordinate_convention,
            entity_policy,
            source_length_unit: unit,
            saved_body_layout: saved,
            csg_layout: csg,
            mirror_policy: self.mirrors().then_some("stored_parity"),
            view_byte_range,
            part_byte_range,
        }
    }

    fn from_header(order: ByteOrder, version: [u8; 4]) -> Option<Self> {
        if order != ByteOrder::Little {
            return None;
        }
        match version {
            [0, 7, 0, 2] => Some(Self::V7L2),
            [0, 7, 0, 3] => Some(Self::V7L3),
            [0, 7, 0, 4] => Some(Self::V7L4),
            [0, 7, 0, 5] => Some(Self::V7L5),
            [0, 7, 0, 6] => Some(Self::V7L6),
            [0, 7, 0, 7] => Some(Self::V7L7),
            [0, 8, 0, 1] => Some(Self::V8L1),
            [0, 8, 0, 2] => Some(Self::V8L2),
            [0, 8, 0, 3] => Some(Self::V8L3),
            _ => None,
        }
    }

    fn part_tag(self) -> u32 {
        match self {
            Self::V7L2 | Self::V7L3 | Self::V7L4 | Self::V7L5 => 0x61000001,
            Self::V7L6 | Self::V7L7 => 0x61000002,
            Self::V8L1 | Self::V8L2 | Self::V8L3 => 0x61000003,
        }
    }

    fn mirrors(self) -> bool {
        matches!(
            self,
            Self::V7L6 | Self::V7L7 | Self::V8L1 | Self::V8L2 | Self::V8L3
        )
    }

    fn legacy_v7(self) -> bool {
        matches!(
            self,
            Self::V7L2 | Self::V7L3 | Self::V7L4 | Self::V7L5 | Self::V7L6
        )
    }

    fn v8(self) -> bool {
        matches!(self, Self::V8L1 | Self::V8L2 | Self::V8L3)
    }

    fn metadata_layout(self, bytes: &[u8]) -> bool {
        // Qualified profiles use a fixed header followed by counted,
        // length-framed records. Validate every boundary without interpreting
        // the payload or searching it for entity signatures.
        if bytes.len() < 164
            || word(bytes, 12) != 0
            || !matches!(word(bytes, 16), 0xc9500088 | 0xc9510088 | 0xc9580088)
        {
            return false;
        }
        let count = word(bytes, 8) as usize;
        if count == 0 || count > (bytes.len() - 160) / 4 {
            return false;
        }
        let mut at = 160;
        for _ in 0..count {
            if bytes.len() - at < 4 {
                return false;
            }
            let tag = word(bytes, at);
            let length = (tag & 0x00ffffff) as usize;
            if length < 4 || !length.is_multiple_of(4) || length > bytes.len() - at {
                return false;
            }
            // Old V7 metadata has an additional fixed-size envelope. Its
            // payload stays opaque; only its framing has been qualified.
            let legacy_envelope = self.legacy_v7()
                && tag == 0x81000050
                && word(bytes, at + 4) == 0
                && word(bytes, at + 8) == 1;
            if !matches!(tag >> 24, 0x79..=0x7c) && !legacy_envelope {
                return false;
            }
            at += length;
        }
        at == bytes.len()
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
    /// Read observed little-endian V7L2–V7L7/V8L1–V8L3 3DGLOBAL part records.
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
            profile: None,
            views: self
                .records()
                .iter()
                .filter(|r| r.tag == "V/W")
                .map(|r| PartViewRecord {
                    byte_range: r.byte_range,
                    kind: "unknown",
                })
                .collect(),
        };
        let h = &self.inspection().header;
        if h.byte_order == ByteOrder::Big
            && matches!(h.raw_version, [0, 6, 0, 1..=2] | [0, 7, 0, 1])
        {
            return self.read_big_parts(result, limits);
        }
        let Some(profile) = PartProfile::from_header(h.byte_order, h.raw_version) else {
            result.issue(
                ErrorKind::Unsupported,
                "parts.profile",
                12,
                "part records require an observed little-endian V7L2 through V7L7 or V8L1 through V8L3 profile",
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
        for (view_index, view) in self.records().iter().filter(|r| r.tag == "V/W").enumerate() {
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
            result.views[view_index].kind = "3d_global";
            result.opaque(base + 8, base + 796, "view_metadata");
            let mut at = 796;
            let mut in_entities = false;
            let mut parametric_owner = false;
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
                // The observed template preamble is a counted metadata record,
                // not a part or a geometry entity. Its single nested record
                // ends exactly where the real root part begins.
                if tag == 0x20000000 && at == 796 && (profile.legacy_v7() || profile.v8()) {
                    if end - at < 240
                        || word(bytes, at + 4) != 236
                        || word(bytes, at + 8) != 1
                        || word(bytes, at + 12) != 0
                        || word(bytes, at + 16) != 0xc9000088
                        || bytes.get(at + 80..at + 104) != Some(b"#-PARAMETRIC-CONDITION-#")
                        || word(bytes, at + 160) != 0x81000050
                    {
                        result.index_status = Status::Partial;
                        result.issue(
                            ErrorKind::Unsupported,
                            "parts.parametric_preamble",
                            base + at as u64,
                            "unqualified parametric preamble; subsequent bytes remain opaque",
                        );
                        result.opaque(base + at as u64, base + end as u64, "unparsed_entities");
                        break;
                    }
                    if limits.max_entities.saturating_sub(entities) < 2 {
                        return Err(limit(
                            base + at as u64,
                            "limit.part_entities",
                            "view entity count exceeds max_entities",
                        ));
                    }
                    entities += 2;
                    result.opaque(
                        base + at as u64,
                        base + at as u64 + 240,
                        "parametric_preamble",
                    );
                    at += 240;
                    continue;
                }
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
                if matches!(tag, 0x61000001..=0x61000003) && tag != profile.part_tag() {
                    result.index_status = if result.parts.is_empty() {
                        Status::Unsupported
                    } else {
                        Status::Partial
                    };
                    result.issue(
                        ErrorKind::Unsupported,
                        "parts.record_profile",
                        base + at as u64,
                        "part record tag does not match the saved native profile",
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
                    let next = at + length as usize + 4;
                    if !profile.metadata_layout(&bytes[at..next]) {
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
                    let metadata_entities = word(bytes, at + 8) as usize + 1;
                    if metadata_entities > limits.max_entities.saturating_sub(entities) {
                        return Err(limit(
                            base + at as u64,
                            "limit.part_entities",
                            "view entity count exceeds max_entities",
                        ));
                    }
                    entities += metadata_entities;
                    result.opaque(base + at as u64, base + next as u64, "entity_metadata");
                    at = next;
                    continue;
                }
                // A list marker is followed by length-prefixed entities. Only the
                // first entity has this marker; subsequent entities begin with
                // their own byte length, not another marker.
                if tag == 0x30010000 && !in_entities && !result.parts.is_empty() {
                    if end - at >= 8
                        && matches!(word(bytes, at + 4), 0xfe000000 | 0x61000001..=0x61000003)
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
                    let extended_part = ((length == 700 && profile.v8())
                        || (matches!(profile, PartProfile::V8L1 | PartProfile::V8L2)
                            && length == 22364))
                        && word(bytes, at + 12) == 1
                        && word(bytes, at + 356) as usize == length - 352
                        && word(bytes, at + 360) == 1
                        && word(bytes, at + 364) == 1;
                    if length != 352 && !extended_part {
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
                    if extended_part {
                        if length - 352 > limits.max_property_bytes {
                            return Err(limit(
                                base + at as u64 + 356,
                                "limit.part_property_bytes",
                                "part metadata extension exceeds max_property_bytes",
                            ));
                        }
                        result.opaque(
                            base + at as u64 + 356,
                            base + next as u64,
                            "part_metadata_extension",
                        );
                    }
                    parametric_owner = extended_part
                        && length == 700
                        && matches!(
                            profile,
                            PartProfile::V8L1 | PartProfile::V8L2 | PartProfile::V8L3
                        );
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
                    // The observed V7L5 originals contain only an unlinked root.
                    // Do not infer a child-part layout from another version.
                    if profile == PartProfile::V7L5
                        && (!is_root || (260..=272).step_by(4).any(|i| word(bytes, at + i) != 0))
                    {
                        result.index_status = Status::Partial;
                        result.issue(
                            ErrorKind::Unsupported,
                            "parts.legacy_root_only",
                            base + at as u64,
                            "V7L5 is qualified only for an unlinked document root",
                        );
                        result.opaque(base + at as u64, base + end as u64, "unparsed_entities");
                        break;
                    }
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
                    let part_range = ByteRange {
                        start: base + at as u64,
                        end: base + next as u64,
                    };
                    if result.profile.is_none() {
                        result.profile =
                            Some(profile.describe(h.raw_version, view.byte_range, part_range));
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
                        opaque_attributes: Vec::new(),
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
                } else if length >= 24
                    && (word(bytes, at + 12) == 0xcf010081
                        || (profile.mirrors() && word(bytes, at + 12) == 0xcf011081))
                {
                    if length >= 32
                        && word(bytes, at + 4) == 1
                        && word(bytes, at + 8) == 0
                        && word(bytes, at + 20) == 0
                        && word(bytes, at + 24) & 0xff000000 == 0xfd000000
                        && (word(bytes, at + 24) & 0x00ffffff) as usize == length - 24
                    {
                        if length - 32 > limits.max_property_bytes {
                            return Err(limit(
                                base + at as u64,
                                "limit.part_property_bytes",
                                "part property exceeds max_property_bytes",
                            ));
                        }
                        let subtype = word(bytes, at + 28);
                        if subtype == 0x10000000 {
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
                        } else if length >= 36
                            && word(bytes, at + 16) & 0xf0000000 == 0x80000000
                            && (bytes[at + 32] == 1
                                || (subtype == 0x19000000 && bytes[at + 32] == 2))
                            && matches!(
                                (subtype, length),
                                (0x02000000, 408 | 472 | 616)
                                    | (0x03000000 | 0x04000000, 56)
                                    | (0x06000000, 48)
                                    | (0x19000000, 88)
                            )
                        {
                            // Qualified framing only. Do not interpret binary attributes
                            // as text or claim their material/BOM/constraint semantics.
                            if let Some(part) = result.parts.last_mut() {
                                part.opaque_attributes.push(PartAttributeRecord {
                                    byte_range: ByteRange {
                                        start: base + at as u64,
                                        end: base + next as u64,
                                    },
                                    source_id: word(bytes, at + 16),
                                    subtype,
                                    raw_bytes: bytes[at..next].to_vec(),
                                });
                            }
                            result.opaque(
                                base + at as u64,
                                base + next as u64,
                                "attribute_payload",
                            );
                        } else {
                            result.issue(
                                ErrorKind::Unsupported,
                                "parts.attribute_layout",
                                base + at as u64,
                                "unqualified extended part information layout",
                            );
                            result.index_status = Status::Partial;
                            result.opaque(
                                base + at as u64,
                                base + next as u64,
                                "unknown_attribute",
                            );
                        }
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
                        part.entities.push(if parametric_owner {
                            crate::native::read_parametric_entity(&bytes[at..next], range)
                        } else if profile == PartProfile::V8L3 {
                            crate::native::read_entity(&bytes[at..next], range)
                        } else if profile == PartProfile::V7L7 {
                            crate::native::read_opaque_entity(&bytes[at..next], range)
                        } else if profile.legacy_v7() {
                            crate::native::read_legacy_entity(&bytes[at..next], range)
                        } else {
                            crate::native::read_unqualified_owner_entity(&bytes[at..next], range)
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
        // Older CSG operands can have primitive-shaped records. Only promote a
        // complete owner's list when every entity has a qualified standalone
        // layout. An unknown sibling, external owner, or unparsed tail
        // keeps the entire owner's list opaque, never a guessed final shape.
        if matches!(
            profile,
            PartProfile::V7L6 | PartProfile::V7L7 | PartProfile::V8L1 | PartProfile::V8L2
        ) && result.index_status == Status::Complete
            && result.hierarchy_status == Status::Complete
        {
            for part in &mut result.parts {
                if !matches!(part.flags, 0 | 8 | 64) {
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
                    .all(|e| e.source_id.is_some() && e.is_mirror.is_some())
                {
                    part.entities = candidates;
                }
            }
        }
        Ok(result)
    }

    fn read_big_parts(
        &self,
        mut result: PartIndex,
        limits: PartLimits,
    ) -> Result<PartIndex, InspectError> {
        let index = self.read_views(crate::ViewLimits {
            max_views: limits.max_entities,
            max_records: limits.max_parts.saturating_add(limits.max_entities),
        })?;
        let header = &self.inspection().header;
        let mut entities = 0usize;
        let mut qualified_views = 0;
        for (vi, view) in index.views.iter().enumerate() {
            if view.kind != "3d_global" {
                result.opaque(
                    view.byte_range.start + 8,
                    view.byte_range.end - 4,
                    "unsupported_view",
                );
                continue;
            }
            qualified_views += 1;
            if qualified_views > 1 {
                result.index_status = Status::Partial;
                result.issue(
                    ErrorKind::Unsupported,
                    "parts.multiple_views",
                    view.byte_range.start,
                    "Multiple 3D views are not qualified",
                );
                result.opaque(
                    view.byte_range.start + 8,
                    view.byte_range.end - 4,
                    "unsupported_view",
                );
                continue;
            }
            result.views[vi].kind = "3d_global";
            result.index_status = view.status;
            result.diagnostics.extend(view.diagnostics.clone());
            for r in &view.opaque_ranges {
                result.opaque(r.start, r.end, "view_metadata");
            }
            for entry in &view.entries {
                let b = self.source_bytes(entry.byte_range)?;
                let at = entry.byte_range.start;
                let u = |i: usize| ByteOrder::Big.u32(std::array::from_fn(|j| b[i + j]));
                if entry.kind == "part" {
                    if entry.tag != Some(0x61000001) || b.len() != 356 || b[10..12] != [0, 0] {
                        result.index_status = Status::Partial;
                        result.issue(
                            ErrorKind::Unsupported,
                            "parts.record_profile",
                            at,
                            "Unqualified big-endian part layout; remainder retained",
                        );
                        result.opaque(at, view.byte_range.end - 4, "unparsed_entities");
                        break;
                    }
                    if result.parts.len() >= limits.max_parts {
                        return Err(limit(at, "limit.parts", "part count exceeds max_parts"));
                    }
                    let flags = ByteOrder::Big.u16([b[8], b[9]]) as u32;
                    let source_id = u(16);
                    let is_root = flags == 64 && source_id >> 28 == 0xe;
                    if !(is_root || flags == 0 && source_id >> 28 == 0xa) {
                        result.index_status = Status::Partial;
                        result.issue(
                            ErrorKind::Unsupported,
                            "parts.kind",
                            at + 8,
                            "Only observed internal big-endian parts are qualified",
                        );
                        result.opaque(at, view.byte_range.end - 4, "unparsed_entities");
                        break;
                    }
                    let doubles = |start: usize| {
                        std::array::from_fn(|i| {
                            ByteOrder::Big.f64(std::array::from_fn(|j| b[start + 8 * i + j]))
                        })
                    };
                    if result.profile.is_none() {
                        result.profile = Some(PartProfileInfo {
                            profile_id: match header.raw_version {
                                [0, 6, 0, 1] => "icad-v6l1-big-parts-r1",
                                [0, 6, 0, 2] => "icad-v6l2-big-parts-r1",
                                _ => "icad-v7l1-big-parts-r1",
                            },
                            byte_order: ByteOrder::Big,
                            raw_version: header.raw_version,
                            part_tag: 0x61000001,
                            coordinate_convention: "unqualified",
                            entity_policy: "inventory_only",
                            source_length_unit: None,
                            saved_body_layout: None,
                            csg_layout: None,
                            mirror_policy: None,
                            view_byte_range: view.byte_range,
                            part_byte_range: entry.byte_range,
                        });
                    }
                    result.parts.push(PartRecord {
                        byte_range: entry.byte_range,
                        view_offset: view.byte_range.start,
                        source_id,
                        flags,
                        is_root,
                        raw_name: b[20..60].to_vec(),
                        raw_comment: b[60..108].to_vec(),
                        placement_values: doubles(108),
                        coordinate_values: doubles(180),
                        raw_reference_name: b[276..316].to_vec(),
                        extra_fields: Vec::new(),
                        opaque_attributes: Vec::new(),
                        entities: Vec::new(),
                        parent_source_id: u(260),
                        first_child_source_id: u(264),
                        previous_source_id: u(268),
                        next_source_id: u(272),
                    });
                    for (a, z) in [(12, 16), (252, 260), (276, 356)] {
                        result.opaque(at + a, at + z, "part_metadata");
                    }
                } else {
                    if entities >= limits.max_entities {
                        return Err(limit(
                            at,
                            "limit.part_entities",
                            "view record count exceeds max_entities",
                        ));
                    }
                    entities += 1;
                    if entry.kind == "entity" {
                        if let Some(part) = result
                            .parts
                            .last_mut()
                            .filter(|p| entry.owner_offset == Some(p.byte_range.start))
                        {
                            part.entities
                                .push(crate::native::read_legacy_entity(b, entry.byte_range));
                        } else {
                            result.index_status = Status::Partial;
                        }
                    } else {
                        result.index_status = Status::Partial;
                        result.issue(
                            ErrorKind::Unsupported,
                            "parts.legacy_metadata",
                            at,
                            "Old owner or metadata semantics remain opaque",
                        );
                    }
                    result.opaque(at, entry.byte_range.end, "unparsed_entities");
                }
            }
        }
        if result.parts.is_empty() {
            if result.index_status != Status::Invalid {
                result.index_status = Status::Unsupported;
            }
            result.hierarchy_status = result.index_status;
            result.issue(
                ErrorKind::Unsupported,
                "parts.no_legacy_root",
                0,
                "No qualified big-endian 61000001 root; use read_views for inventory",
            );
        } else {
            result.hierarchy(limits)?;
        }
        Ok(result)
    }
}
