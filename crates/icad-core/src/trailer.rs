//! Bounded framing of the container after the directory-indexed records.
//!
//! Its records use a packed header in files of either byte order: one type
//! byte, one subtype byte, a little-endian 16-bit header size and a
//! little-endian 32-bit total size. Only the observed nesting is accepted.
//! Unknown records stop traversal; nothing is searched for.

use flate2::{Decompress, FlushDecompress, Status as StreamStatus};

use crate::provenance::sha256;
use crate::{ByteRange, Diagnostic, Document, ErrorKind, InspectError, Status};

#[derive(Debug, Clone, Copy)]
pub struct TrailerLimits {
    pub max_records: usize,
    pub max_block_bytes: u64,
}

impl Default for TrailerLimits {
    fn default() -> Self {
        Self {
            max_records: 500_000,
            max_block_bytes: 64 * 1024 * 1024,
        }
    }
}

/// One compressed block keyed by a saved entity identifier. The identifier
/// is retained as stored; it does not establish part ownership by itself.
#[derive(Debug, Clone)]
pub struct TrailerBlock {
    pub byte_range: ByteRange,
    /// Exact encoded bytes, excluding the record header and zero padding.
    pub storage_range: ByteRange,
    pub source_id: u32,
    pub declared_decoded_bytes: u64,
    pub view_index: usize,
}

#[derive(Debug, Clone)]
pub struct TrailerView {
    pub byte_range: ByteRange,
    pub raw_name: [u8; 8],
    pub block_count: usize,
}

/// A framed table whose linked payload layout is not qualified.
#[derive(Debug, Clone)]
pub struct TrailerTable {
    pub byte_range: ByteRange,
    pub payload_range: ByteRange,
}

#[derive(Debug, Clone)]
pub struct TrailerIndex {
    /// `None` when no byte follows the last directory-indexed record.
    pub byte_range: Option<ByteRange>,
    /// Stored subtype of the block set. It selects the block payload layout.
    pub revision: Option<u8>,
    pub views: Vec<TrailerView>,
    pub blocks: Vec<TrailerBlock>,
    pub tables: Vec<TrailerTable>,
    pub opaque_ranges: Vec<ByteRange>,
    pub diagnostics: Vec<Diagnostic>,
    pub status: Status,
}

/// One 36-byte face entry of a block. A saved face that is closed in a
/// parameter direction is stored as several entries with one identifier.
/// `parameter_bounds` is `(u_min, v_min, u_max, v_max)` on the face's surface;
/// it is absent when the stored values are not finite and ordered.
#[derive(Debug, Clone)]
pub struct TrailerFace {
    pub source_node_id: u32,
    pub surface_code: u8,
    pub parameter_bounds: Option<[f32; 4]>,
    pub entry_offset: u32,
    pub item_offset: u32,
}

/// One 24-byte edge entry of a block. `faces` are one-based face entry
/// numbers, zero for none. The identifier is that of a saved edge, or of the
/// saved face for an edge that only separates two entries of that face.
#[derive(Debug, Clone)]
pub struct TrailerEdge {
    pub source_node_id: u32,
    pub faces: [u16; 2],
    pub entry_offset: u32,
    pub item_offset: u32,
}

/// A decoded block. `bounds` are stored single-precision values in the owning
/// entity's saved local frame; they are exposed only for the qualified layout.
/// The box encloses the saved body but is not always the smallest such box.
/// The face and edge tables give identity and adjacency; their geometry items
/// and link fields stay in `payload`.
#[derive(Debug, Clone)]
pub struct TrailerBlockData {
    pub source_id: u32,
    pub payload: Vec<u8>,
    pub payload_sha256: String,
    pub raw_kind: Option<u32>,
    pub bounds: Option<[f32; 6]>,
    pub faces: Vec<TrailerFace>,
    pub edges: Vec<TrailerEdge>,
    pub status: Status,
    pub diagnostics: Vec<Diagnostic>,
}

#[derive(Clone, Copy)]
struct Frame {
    kind: u8,
    subtype: u8,
    start: usize,
    body: usize,
    end: usize,
}

fn word(data: &[u8], at: usize) -> u32 {
    u32::from_le_bytes([data[at], data[at + 1], data[at + 2], data[at + 3]])
}

/// Two fixed-size tables follow the 52-byte header: 36-byte faces, then
/// 24-byte edges. Their geometry items, tags and link fields stay uninterpreted.
fn tables(payload: &[u8]) -> Option<(Vec<TrailerFace>, Vec<TrailerEdge>)> {
    let face_count = (word(payload, 44) & 0xffff) as usize;
    let edge_count = (word(payload, 44) >> 16) as usize;
    let edges_at = 52 + 36 * face_count;
    let items_at = edges_at + 24 * edge_count;
    if word(payload, 40) as usize != edges_at
        || word(payload, 48) as usize != items_at
        || items_at > payload.len()
    {
        return None;
    }
    let in_items = |at: u32| (items_at..=payload.len()).contains(&(at as usize));
    let mut faces = Vec::with_capacity(face_count);
    for at in (52..edges_at).step_by(36) {
        let mut values = [0f32; 4];
        for (i, value) in values.iter_mut().enumerate() {
            *value = f32::from_bits(word(payload, at + 20 + 4 * i));
        }
        let ordered = values.iter().all(|v| v.is_finite())
            && values[0] <= values[2]
            && values[1] <= values[3];
        let item_offset = word(payload, at + 12);
        if !in_items(item_offset) {
            return None;
        }
        faces.push(TrailerFace {
            source_node_id: word(payload, at + 4),
            surface_code: payload[at + 10],
            parameter_bounds: ordered.then_some(values),
            entry_offset: at as u32,
            item_offset,
        });
    }
    let mut edges = Vec::with_capacity(edge_count);
    for at in (edges_at..items_at).step_by(24) {
        let pair = word(payload, at + 8);
        let adjacent = [(pair & 0xffff) as u16, (pair >> 16) as u16];
        let item_offset = word(payload, at + 20);
        if !in_items(item_offset) || adjacent.iter().any(|f| *f as usize > face_count) {
            return None;
        }
        edges.push(TrailerEdge {
            source_node_id: word(payload, at + 4),
            faces: adjacent,
            entry_offset: at as u32,
            item_offset,
        });
    }
    Some((faces, edges))
}

/// Read one frame that must end at or before `limit`.
fn frame(data: &[u8], at: usize, limit: usize) -> Option<Frame> {
    if limit > data.len() || at > limit || limit - at < 8 {
        return None;
    }
    let header = usize::from(u16::from_le_bytes([data[at + 2], data[at + 3]]));
    let size = word(data, at + 4) as usize;
    if header < 8 || header > size || size > limit - at {
        return None;
    }
    Some(Frame {
        kind: data[at],
        subtype: data[at + 1],
        start: at,
        body: at + header,
        end: at + size,
    })
}

struct Walk<'a> {
    data: &'a [u8],
    end: usize,
    limits: TrailerLimits,
    count: usize,
    index: TrailerIndex,
}

impl Walk<'_> {
    fn stop(&mut self, at: usize, kind: ErrorKind, code: &'static str, text: &str) {
        if kind == ErrorKind::Invalid {
            self.index.status = Status::Invalid;
        } else if self.index.status != Status::Invalid {
            self.index.status = Status::Partial;
        }
        self.index.diagnostics.push(Diagnostic {
            kind,
            code,
            byte_offset: at as u64,
            message: text.to_owned(),
        });
        if at < self.end {
            self.index.opaque_ranges.push(ByteRange {
                start: at as u64,
                end: self.end as u64,
            });
        }
    }

    fn counted(&mut self, at: usize) -> Result<(), InspectError> {
        self.count += 1;
        if self.count > self.limits.max_records {
            return Err(InspectError::format(
                ErrorKind::LimitExceeded,
                "limit.trailer_records",
                at as u64,
                "trailer record count exceeds max_records",
            ));
        }
        Ok(())
    }

    /// A child frame inside `parent`; a malformed length is invalid.
    fn child(&mut self, at: usize, parent: Frame) -> Option<Frame> {
        let value = frame(self.data, at, parent.end);
        if value.is_none() {
            self.stop(
                at,
                ErrorKind::Invalid,
                "trailer.length",
                "Trailer record length is inconsistent with its parent",
            );
        }
        value
    }

    fn tables(&mut self, parent: Frame) -> Result<bool, InspectError> {
        let mut at = parent.body;
        while at < parent.end {
            let Some(table) = self.child(at, parent) else {
                return Ok(false);
            };
            self.counted(at)?;
            if table.kind != 0x03 || table.subtype != 0 || table.body - table.start != 16 {
                self.stop(
                    at,
                    ErrorKind::Unsupported,
                    "trailer.record",
                    "Unqualified trailer table record",
                );
                return Ok(false);
            }
            self.index.tables.push(TrailerTable {
                byte_range: ByteRange {
                    start: table.start as u64,
                    end: table.end as u64,
                },
                payload_range: ByteRange {
                    start: table.body as u64,
                    end: table.end as u64,
                },
            });
            at = table.end;
        }
        Ok(true)
    }

    fn blocks(&mut self, view: Frame, view_index: usize) -> Result<Option<u64>, InspectError> {
        let declared = word(self.data, view.start + 16) as usize;
        let mut at = view.body;
        let mut largest = 0u64;
        let mut found = 0usize;
        while at < view.end {
            let Some(block) = self.child(at, view) else {
                return Ok(None);
            };
            self.counted(at)?;
            if block.kind != 0x23 || block.subtype != 8 || block.body - block.start != 24 {
                self.stop(
                    at,
                    ErrorKind::Unsupported,
                    "trailer.record",
                    "Unqualified trailer block record",
                );
                return Ok(None);
            }
            let source_id = word(self.data, at + 8);
            let encoded = word(self.data, at + 12) as usize;
            let decoded = u64::from(word(self.data, at + 16));
            let size = block.end - block.start;
            // The record is padded with zero bytes to a 16-byte boundary.
            let expected = encoded
                .checked_add(24 + 15)
                .map(|value| value & !15)
                .filter(|value| *value == size);
            if expected.is_none()
                || encoded == 0
                || decoded == 0
                || word(self.data, at + 20) != 0
                || source_id >> 28 != 8
                || self.data[block.body + encoded..block.end]
                    .iter()
                    .any(|byte| *byte != 0)
            {
                self.stop(
                    at,
                    ErrorKind::Invalid,
                    "trailer.block",
                    "Trailer block header, size or padding is inconsistent",
                );
                return Ok(None);
            }
            largest = largest.max(decoded);
            found += 1;
            self.index.blocks.push(TrailerBlock {
                byte_range: ByteRange {
                    start: block.start as u64,
                    end: block.end as u64,
                },
                storage_range: ByteRange {
                    start: block.body as u64,
                    end: (block.body + encoded) as u64,
                },
                source_id,
                declared_decoded_bytes: decoded,
                view_index,
            });
            at = block.end;
        }
        if found != declared {
            self.stop(
                view.start + 16,
                ErrorKind::Invalid,
                "trailer.count",
                "Trailer view block count differs from its records",
            );
            return Ok(None);
        }
        Ok(Some(largest))
    }

    fn views(&mut self, set: Frame) -> Result<bool, InspectError> {
        let declared = word(self.data, set.start + 8) as usize;
        if word(self.data, set.start + 12) != 0 {
            self.stop(
                set.start + 12,
                ErrorKind::Unsupported,
                "trailer.record",
                "Unqualified trailer block-set header",
            );
            return Ok(false);
        }
        let mut at = set.body;
        while at < set.end {
            let Some(view) = self.child(at, set) else {
                return Ok(false);
            };
            self.counted(at)?;
            if view.kind != 0x22 || view.subtype != 0 || view.body - view.start != 24 {
                self.stop(
                    at,
                    ErrorKind::Unsupported,
                    "trailer.record",
                    "Unqualified trailer view record",
                );
                return Ok(false);
            }
            let view_index = self.index.views.len();
            let first = self.index.blocks.len();
            let mut raw_name = [0; 8];
            raw_name.copy_from_slice(&self.data[at + 8..at + 16]);
            self.index.views.push(TrailerView {
                byte_range: ByteRange {
                    start: view.start as u64,
                    end: view.end as u64,
                },
                raw_name,
                block_count: 0,
            });
            let Some(largest) = self.blocks(view, view_index)? else {
                return Ok(false);
            };
            self.index.views[view_index].block_count = self.index.blocks.len() - first;
            if largest != u64::from(word(self.data, at + 20)) {
                self.stop(
                    at + 20,
                    ErrorKind::Invalid,
                    "trailer.count",
                    "Trailer view size bound differs from its blocks",
                );
                return Ok(false);
            }
            at = view.end;
        }
        if self.index.views.len() != declared {
            self.stop(
                set.start + 8,
                ErrorKind::Invalid,
                "trailer.count",
                "Trailer view count differs from its records",
            );
            return Ok(false);
        }
        Ok(true)
    }

    fn run(&mut self, start: usize) -> Result<(), InspectError> {
        let root = frame(self.data, start, self.end)
            .filter(|f| f.kind == 0x10 && f.subtype == 0 && f.body - f.start == 8)
            .filter(|f| f.end == self.end);
        let Some(root) = root else {
            self.index.status = Status::Unsupported;
            self.index.diagnostics.push(Diagnostic {
                kind: ErrorKind::Unsupported,
                code: "trailer.root",
                byte_offset: start as u64,
                message: "Unqualified trailing container; bytes retained".to_owned(),
            });
            self.index.opaque_ranges.push(ByteRange {
                start: start as u64,
                end: self.end as u64,
            });
            return Ok(());
        };
        self.counted(start)?;
        self.index.status = Status::Complete;
        let mut at = root.body;
        let mut sets = 0;
        let mut table_groups = 0;
        while at < root.end {
            let Some(child) = self.child(at, root) else {
                return Ok(());
            };
            self.counted(at)?;
            let header = child.body - child.start;
            match (child.kind, child.subtype, header) {
                // The named-table group precedes the block group when present.
                (0x40, 0, 8) if sets == 0 && table_groups == 0 => {
                    table_groups = 1;
                    if !self.tables(child)? {
                        return Ok(());
                    }
                }
                (0x20, 0, 8) if sets == 0 => {
                    sets = 1;
                    let Some(set) = self.child(child.body, child) else {
                        return Ok(());
                    };
                    self.counted(set.start)?;
                    if set.kind != 0x21
                        || !matches!(set.subtype, 1..=4 | 6..=8)
                        || set.body - set.start != 16
                        || set.end != child.end
                    {
                        self.stop(
                            set.start,
                            ErrorKind::Unsupported,
                            "trailer.record",
                            "Unqualified trailer block set",
                        );
                        return Ok(());
                    }
                    self.index.revision = Some(set.subtype);
                    if !self.views(set)? {
                        return Ok(());
                    }
                }
                _ => {
                    self.stop(
                        at,
                        ErrorKind::Unsupported,
                        "trailer.record",
                        "Unknown trailer record; remaining bytes are opaque",
                    );
                    return Ok(());
                }
            }
            at = child.end;
        }
        if sets == 0 {
            self.stop(
                root.end,
                ErrorKind::Unsupported,
                "trailer.record",
                "Trailing container has no block group",
            );
        }
        Ok(())
    }
}

/// Bounded zlib decoding with exact input and output sizes.
fn inflate(storage: &[u8], declared: usize) -> Result<Vec<u8>, &'static str> {
    let mut decoder = Decompress::new(true);
    let mut output = Vec::new();
    output
        .try_reserve_exact(declared)
        .map_err(|_| "cannot allocate the declared block size")?;
    let mut buffer = [0u8; 64 * 1024];
    loop {
        let before_in = decoder.total_in();
        let before_out = decoder.total_out();
        let input = storage
            .get(before_in as usize..)
            .ok_or("decoder consumed outside the block")?;
        let status = decoder
            .decompress(input, &mut buffer, FlushDecompress::None)
            .map_err(|_| "invalid zlib stream or checksum")?;
        let produced = (decoder.total_out() - before_out) as usize;
        if decoder.total_out() > declared as u64 {
            return Err("zlib output exceeds the declared block size");
        }
        output.extend_from_slice(&buffer[..produced]);
        if status == StreamStatus::StreamEnd {
            break;
        }
        if before_in == decoder.total_in() && before_out == decoder.total_out() {
            return Err("zlib stream ended without a checksum-complete EOF");
        }
    }
    if decoder.total_out() != declared as u64 || decoder.total_in() != storage.len() as u64 {
        return Err("zlib stream differs from the declared block sizes");
    }
    Ok(output)
}

impl Document {
    fn trailer_start(&self) -> usize {
        self.records()
            .iter()
            .map(|record| record.byte_range.end)
            .max()
            .unwrap_or(0) as usize
    }

    /// Frame the container after the last directory-indexed record. No block
    /// is decompressed. A missing container is complete and has no range.
    pub fn read_trailer(&self, limits: TrailerLimits) -> Result<TrailerIndex, InspectError> {
        if limits.max_records == 0 || limits.max_block_bytes == 0 {
            return Err(InspectError::format(
                ErrorKind::LimitExceeded,
                "limit.invalid",
                0,
                "trailer limits must be positive",
            ));
        }
        let data = self.all_bytes();
        let start = self.trailer_start();
        let mut walk = Walk {
            data,
            end: data.len(),
            limits,
            count: 0,
            index: TrailerIndex {
                byte_range: None,
                revision: None,
                views: Vec::new(),
                blocks: Vec::new(),
                tables: Vec::new(),
                opaque_ranges: Vec::new(),
                diagnostics: Vec::new(),
                status: Status::Complete,
            },
        };
        if start >= data.len() {
            return Ok(walk.index);
        }
        walk.index.byte_range = Some(ByteRange {
            start: start as u64,
            end: data.len() as u64,
        });
        walk.run(start)?;
        Ok(walk.index)
    }

    /// Decode one indexed block. `block_start` must be the start of a block
    /// record in this document's trailer index; no other offset is accepted.
    pub fn read_trailer_block(
        &self,
        block_start: u64,
        limits: TrailerLimits,
    ) -> Result<TrailerBlockData, InspectError> {
        let index = self.read_trailer(limits)?;
        let block = index
            .blocks
            .iter()
            .find(|block| block.byte_range.start == block_start)
            .ok_or_else(|| {
                InspectError::format(
                    ErrorKind::Invalid,
                    "trailer.block_not_found",
                    block_start,
                    "block is not in this document's trailer index",
                )
            })?;
        if block.declared_decoded_bytes > limits.max_block_bytes
            || block.storage_range.end - block.storage_range.start > limits.max_block_bytes
        {
            return Err(InspectError::format(
                ErrorKind::LimitExceeded,
                "limit.trailer_block_bytes",
                block.byte_range.start,
                "trailer block exceeds max_block_bytes",
            ));
        }
        let data = self.all_bytes();
        let storage = &data[block.storage_range.start as usize..block.storage_range.end as usize];
        let payload = inflate(storage, block.declared_decoded_bytes as usize).map_err(|text| {
            InspectError::format(
                ErrorKind::Invalid,
                "trailer.compression",
                block.storage_range.start,
                text,
            )
        })?;
        let mut result = TrailerBlockData {
            source_id: block.source_id,
            payload_sha256: sha256(&payload),
            raw_kind: None,
            bounds: None,
            faces: Vec::new(),
            edges: Vec::new(),
            status: Status::Unsupported,
            diagnostics: Vec::new(),
            payload,
        };
        let payload = &result.payload;
        let issue = |kind, code, text: &str| Diagnostic {
            kind,
            code,
            byte_offset: block.byte_range.start,
            message: text.to_owned(),
        };
        if payload.len() < 8 || word(payload, 0) as usize != payload.len() {
            result.status = Status::Invalid;
            result.diagnostics.push(issue(
                ErrorKind::Invalid,
                "trailer.block_length",
                "Decoded block length differs from its own header",
            ));
            return Ok(result);
        }
        let kind = word(payload, 4);
        result.raw_kind = Some(kind);
        // Bounds are qualified for the 52-byte header of revisions 7 and 8.
        // Other revisions and block kinds keep their decoded bytes only.
        if !matches!(index.revision, Some(7 | 8))
            || payload.len() < 52
            || kind >> 4 != 9
            || word(payload, 32) != 0
            || word(payload, 36) != 52
        {
            result.diagnostics.push(issue(
                ErrorKind::Unsupported,
                "trailer.block_layout",
                "Unqualified block payload layout; decoded bytes retained",
            ));
            return Ok(result);
        }
        let mut bounds = [0f32; 6];
        for (i, value) in bounds.iter_mut().enumerate() {
            *value = f32::from_bits(word(payload, 8 + 4 * i));
        }
        if bounds.iter().any(|v| !v.is_finite()) || (0..3).any(|i| bounds[i] > bounds[i + 3]) {
            result.status = Status::Invalid;
            result.diagnostics.push(issue(
                ErrorKind::Invalid,
                "trailer.block_bounds",
                "Nonfinite or reversed block bounds",
            ));
            return Ok(result);
        }
        result.bounds = Some(bounds);
        let Some((faces, edges)) = tables(payload) else {
            result.bounds = None;
            result.status = Status::Invalid;
            result.diagnostics.push(issue(
                ErrorKind::Invalid,
                "trailer.block_tables",
                "Face or edge table disagrees with its counts and offsets",
            ));
            return Ok(result);
        };
        result.faces = faces;
        result.edges = edges;
        result.status = Status::Partial;
        Ok(result)
    }
}
