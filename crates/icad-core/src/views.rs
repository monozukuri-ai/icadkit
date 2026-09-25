//! Observed counted view headers and bounded record inventories.
//! Packed attribute bytes are not endian-swapped numeric words.

use crate::{ByteOrder, ByteRange, Diagnostic, Document, ErrorKind, InspectError, Status};

#[derive(Debug, Clone, Copy)]
pub struct ViewLimits {
    pub max_views: usize,
    pub max_records: usize,
}

impl Default for ViewLimits {
    fn default() -> Self {
        Self {
            max_views: 10_000,
            max_records: 500_000,
        }
    }
}

#[derive(Debug, Clone)]
pub struct ViewEntry {
    pub byte_range: ByteRange,
    pub kind: &'static str,
    pub tag: Option<u32>,
    pub owner_offset: Option<u64>,
}

#[derive(Debug, Clone)]
pub struct ViewRecord {
    pub byte_range: ByteRange,
    pub header_range: Option<ByteRange>,
    pub raw_name: Vec<u8>,
    pub kind: &'static str,
    pub raw_view_number: Option<u16>,
    pub entries: Vec<ViewEntry>,
    pub opaque_ranges: Vec<ByteRange>,
    pub diagnostics: Vec<Diagnostic>,
    pub status: Status,
}

#[derive(Debug, Clone)]
pub struct ViewIndex {
    pub views: Vec<ViewRecord>,
    pub document_kind: &'static str,
    pub profile_id: Option<String>,
    pub diagnostics: Vec<Diagnostic>,
    pub status: Status,
}

pub(crate) fn observed_version(order: ByteOrder, v: [u8; 4]) -> bool {
    match order {
        ByteOrder::Big => matches!(v,
            [0, 1, 0, 9] | [0, 3, 0, 1..=5 | 7..=9]
            | [0, 5, 0, 1..=4] | [0, 6, 0, 1..=2] | [0, 7, 0, 1]),
        ByteOrder::Little => matches!(v, [0, 7, 0, 2..=7] | [0, 8, 0, 1..=3]),
    }
}

fn diagnostic(kind: ErrorKind, code: &'static str, at: u64, text: &str) -> Diagnostic {
    Diagnostic {
        kind,
        code,
        byte_offset: at,
        message: text.to_owned(),
    }
}

impl ViewRecord {
    fn stop(&mut self, at: u64, kind: ErrorKind, code: &'static str, text: &str) {
        self.status = if kind == ErrorKind::Invalid {
            Status::Invalid
        } else {
            Status::Partial
        };
        self.diagnostics.push(diagnostic(kind, code, at, text));
        if at < self.byte_range.end - 4 {
            self.opaque_ranges.push(ByteRange {
                start: at,
                end: self.byte_range.end - 4,
            });
        }
    }
}

impl Document {
    pub fn read_views(&self, limits: ViewLimits) -> Result<ViewIndex, InspectError> {
        if limits.max_views == 0 || limits.max_records == 0 {
            return Err(InspectError::format(
                ErrorKind::LimitExceeded,
                "limit.invalid",
                0,
                "view limits must be positive",
            ));
        }
        let header = &self.inspection().header;
        let order = header.byte_order;
        let qualified = observed_version(order, header.raw_version);
        let mut out = ViewIndex {
            views: Vec::new(),
            document_kind: "unknown",
            profile_id: None,
            diagnostics: Vec::new(),
            status: if qualified {
                Status::Complete
            } else {
                Status::Unsupported
            },
        };
        if !qualified {
            out.diagnostics.push(diagnostic(
                ErrorKind::Unsupported,
                "views.profile",
                12,
                "No observed view profile for this version and byte order",
            ));
        }
        let mut count = 0;
        for record in self.records().iter().filter(|r| r.tag == "V/W") {
            if out.views.len() >= limits.max_views {
                return Err(InspectError::format(
                    ErrorKind::LimitExceeded,
                    "limit.views",
                    record.byte_range.start,
                    "view count exceeds max_views",
                ));
            }
            let base = record.byte_range.start;
            let mut view = ViewRecord {
                byte_range: record.byte_range,
                header_range: None,
                raw_name: Vec::new(),
                kind: "unknown",
                raw_view_number: None,
                entries: Vec::new(),
                opaque_ranges: Vec::new(),
                diagnostics: Vec::new(),
                status: Status::Unsupported,
            };
            if !qualified {
                view.opaque_ranges.push(record.payload_range);
                out.views.push(view);
                continue;
            }
            let b = self.source_bytes(record.byte_range)?;
            let u = |at: usize| order.u32(std::array::from_fn(|i| b[at + i]));
            // The directory owns the view. Its extension word count determines
            // the header position; names and signatures are never searched for.
            if b.len() < 32 || u(20) != 60 {
                view.stop(
                    base + 8,
                    ErrorKind::Unsupported,
                    "views.header",
                    "Unqualified view envelope",
                );
                out.views.push(view);
                continue;
            }
            let position = 28u64 + 4u64 * u(16) as u64;
            if position + 240 > b.len() as u64 - 4 {
                view.stop(
                    base + 16,
                    ErrorKind::Invalid,
                    "views.header_length",
                    "Counted view header exceeds its record",
                );
                out.views.push(view);
                continue;
            }
            let at = position as usize;
            let global_count = order.u16([b[at + 2], b[at + 3]]);
            let global_header = b[at..at + 2] == [0x10, 0] && (1..=5).contains(&global_count);
            if (at != 28 && b[28..32] != [0x12, 0xff, 0, 0])
                || !(global_header
                    || matches!(
                        &b[at..at + 4],
                        [0x10, 0, 0, 0] | [0x11, 0, 0x80, 0] | [0x11, 0, 0, 0]
                    ))
            {
                view.stop(
                    base + 28,
                    ErrorKind::Unsupported,
                    "views.header_layout",
                    "Unqualified counted view header markers",
                );
                out.views.push(view);
                continue;
            }
            let vtype = order.u16([b[at + 26], b[at + 27]]);
            let name = &b[at + 8..at + 16];
            let is_3d = b[at] == 0x11;
            view.kind = if is_3d && name == b"3DGLOBAL" && matches!(vtype, 1..=3) {
                "3d_global"
            } else if global_header && name == b"!!GLOBAL" && vtype == 1 {
                "2d_global"
            } else if b[at..at + 4] == [0x10, 0, 0, 0]
                && name == b"@BUHIN  "
                && matches!(vtype, 3..=8)
            {
                "registered_part"
            } else if b[at..at + 4] == [0x10, 0, 0, 0] && matches!(vtype, 2..=6) {
                "2d_view"
            } else {
                "unknown"
            };
            view.raw_name = name.to_vec();
            view.raw_view_number = Some(vtype);
            view.header_range = Some(ByteRange {
                start: base + position,
                end: base + position + 240,
            });
            view.opaque_ranges.push(ByteRange {
                start: base + 8,
                end: base + position + 240,
            });
            if view.kind == "unknown" {
                view.stop(
                    base + position + 240,
                    ErrorKind::Unsupported,
                    "views.kind",
                    "View kind is not qualified",
                );
                out.views.push(view);
                continue;
            }
            view.status = Status::Complete;
            let end = b.len() - 4;
            let mut cursor = at + 240;
            let mut group = false;
            let mut owner = None;
            loop {
                if end - cursor < 4 {
                    view.stop(
                        base + cursor as u64,
                        ErrorKind::Invalid,
                        "views.missing_end",
                        "Missing view terminator",
                    );
                    break;
                }
                let tag = u(cursor);
                if tag == 0xfe000000 {
                    if cursor + 4 != end {
                        view.stop(
                            base + cursor as u64 + 4,
                            ErrorKind::Unsupported,
                            "views.after_end",
                            "Unparsed bytes follow the view terminator",
                        );
                    }
                    break;
                }
                if tag == 0x30010000 {
                    if group {
                        view.stop(
                            base + cursor as u64,
                            ErrorKind::Unsupported,
                            "views.entity_group",
                            "Repeated entity list marker",
                        );
                        break;
                    }
                    group = true;
                    cursor += 4;
                    continue;
                }
                let (prefix, kind) = match tag {
                    0x61000001..=0x61000003 if is_3d => (4, "part"),
                    0x60000001 | 0x60000002 if is_3d => (4, "legacy_owner"),
                    0x20000000 | 0x21000000 | 0x40000000 => (4, "metadata"),
                    _ if group => (0, "entity"),
                    _ => {
                        view.stop(
                            base + cursor as u64,
                            ErrorKind::Unsupported,
                            "views.record",
                            "Unknown view record; remaining bytes are opaque",
                        );
                        break;
                    }
                };
                if end - cursor < prefix + 4 {
                    view.stop(
                        base + cursor as u64,
                        ErrorKind::Invalid,
                        "views.truncated",
                        "Truncated view record length",
                    );
                    break;
                }
                let len = u(cursor + prefix) as usize;
                if len < 4 || !len.is_multiple_of(4) || len > end - cursor - prefix {
                    view.stop(
                        base + cursor as u64,
                        ErrorKind::Invalid,
                        "views.record_length",
                        "Invalid view record length",
                    );
                    break;
                }
                if count >= limits.max_records {
                    return Err(InspectError::format(
                        ErrorKind::LimitExceeded,
                        "limit.view_records",
                        base + cursor as u64,
                        "view record count exceeds max_records",
                    ));
                }
                count += 1;
                if prefix != 0 {
                    group = false;
                }
                if kind == "part" || kind == "legacy_owner" {
                    owner = Some(base + cursor as u64);
                }
                view.entries.push(ViewEntry {
                    byte_range: ByteRange {
                        start: base + cursor as u64,
                        end: base + (cursor + prefix + len) as u64,
                    },
                    kind,
                    tag: (prefix != 0).then_some(tag),
                    owner_offset: if kind == "entity" { owner } else { None },
                });
                cursor += prefix + len;
            }
            out.views.push(view);
        }
        if qualified && !out.views.is_empty() {
            let known = out.views.iter().all(|v| v.kind != "unknown");
            let registered = out.views.iter().any(|v| v.kind == "registered_part");
            if known && registered && out.views.len() == 1 {
                out.document_kind = "registered_part";
            } else if known
                && !registered
                && out
                    .views
                    .iter()
                    .any(|v| matches!(v.kind, "2d_global" | "3d_global"))
            {
                out.document_kind = "document";
            }
            if out.views.iter().any(|v| v.status == Status::Invalid) {
                out.status = Status::Invalid;
            } else if out.views.iter().any(|v| v.status != Status::Complete) {
                out.status = Status::Partial;
            }
            if out.views.iter().any(|v| v.header_range.is_some()) {
                let v = header.raw_version;
                out.profile_id = Some(format!(
                    "icad-{:02x}{:02x}{:02x}{:02x}-{}-views-r1",
                    v[0],
                    v[1],
                    v[2],
                    v[3],
                    order.as_str()
                ));
            }
        } else if qualified {
            out.status = Status::Unsupported;
            out.diagnostics.push(diagnostic(
                ErrorKind::Unsupported,
                "views.none",
                0,
                "No indexed views; this is not an empty drawing",
            ));
        }
        Ok(out)
    }
}
