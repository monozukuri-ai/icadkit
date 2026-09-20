use std::fs::File;
use std::io::Read;
use std::path::Path;

use crate::compression::decode;
use crate::provenance::sha256;
use crate::record::record_info;
use crate::{
    ByteOrder, ByteRange, Diagnostic, Encoding, ErrorKind, Extraction, InspectError, Inspection,
    ReadLimits, RecordInfo, ResourceRef, SourceRef, UnparsedRange, inspect_bytes,
};

/// Immutable owned input and a structural resource index. Construction never
/// searches for signatures or inflates data. A failed extraction changes no
/// other resource, and repeated/concurrent extraction is deterministic.
#[derive(Debug)]
pub struct Document {
    data: Vec<u8>,
    limits: ReadLimits,
    inspection: Inspection,
    source_sha256: String,
    records: Vec<RecordInfo>,
    resources: Vec<ResourceRef>,
    unparsed_ranges: Vec<UnparsedRange>,
    diagnostics: Vec<Diagnostic>,
    resource_index_complete: bool,
}

fn failure(kind: ErrorKind, code: &'static str, offset: usize, message: &str) -> InspectError {
    InspectError::format(kind, code, offset as u64, message)
}

impl Document {
    pub fn inspection(&self) -> &Inspection {
        &self.inspection
    }
    pub fn source_sha256(&self) -> &str {
        &self.source_sha256
    }
    pub fn records(&self) -> &[RecordInfo] {
        &self.records
    }
    pub fn resources(&self) -> &[ResourceRef] {
        &self.resources
    }
    pub fn unparsed_ranges(&self) -> &[UnparsedRange] {
        &self.unparsed_ranges
    }
    pub fn diagnostics(&self) -> &[Diagnostic] {
        &self.diagnostics
    }
    pub fn resource_index_complete(&self) -> bool {
        self.resource_index_complete
    }
    pub fn from_bytes(data: &[u8], limits: ReadLimits) -> Result<Self, InspectError> {
        limits.validate()?;
        limits.inspection().check_file(data.len() as u64)?;
        Self::from_owned(data.to_vec(), limits)
    }

    pub fn from_file(path: impl AsRef<Path>, limits: ReadLimits) -> Result<Self, InspectError> {
        limits.validate()?;
        let path = path.as_ref();
        let metadata = std::fs::metadata(path)?;
        if !metadata.is_file() {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidInput,
                "reading requires a regular file",
            )
            .into());
        }
        limits.inspection().check_file(metadata.len())?;
        let file = File::open(path)?;
        let metadata = file.metadata()?;
        if !metadata.is_file() {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidInput,
                "reading requires a regular file",
            )
            .into());
        }
        limits.inspection().check_file(metadata.len())?;
        let size = usize::try_from(metadata.len()).map_err(|_| {
            failure(
                ErrorKind::LimitExceeded,
                "limit.address_space",
                0,
                "file cannot fit in memory",
            )
        })?;
        let mut data = Vec::new();
        data.try_reserve_exact(size).map_err(|_| {
            failure(
                ErrorKind::LimitExceeded,
                "limit.allocation",
                0,
                "cannot allocate input buffer",
            )
        })?;
        // One extra byte detects growth without reading an unbounded live file.
        file.take(metadata.len().saturating_add(1))
            .read_to_end(&mut data)?;
        if data.len() != size {
            return Err(failure(
                ErrorKind::Invalid,
                "source.size_changed",
                0,
                "file size changed during reading",
            ));
        }
        Self::from_owned(data, limits)
    }

    pub fn from_owned(data: Vec<u8>, limits: ReadLimits) -> Result<Self, InspectError> {
        limits.validate()?;
        let inspection = inspect_bytes(&data, limits.inspection())?;
        let source_sha256 = sha256(&data);
        let mut doc = Self {
            data,
            limits,
            inspection,
            source_sha256,
            records: Vec::new(),
            resources: Vec::new(),
            unparsed_ranges: Vec::new(),
            diagnostics: Vec::new(),
            resource_index_complete: true,
        };
        doc.index()?;
        Ok(doc)
    }

    fn word(&self, offset: usize) -> Result<u32, InspectError> {
        let bytes = self
            .data
            .get(offset..offset.saturating_add(4))
            .ok_or_else(|| {
                failure(
                    ErrorKind::Invalid,
                    "record.truncated",
                    offset,
                    "truncated integer",
                )
            })?;
        Ok(self
            .inspection
            .header
            .byte_order
            .u32([bytes[0], bytes[1], bytes[2], bytes[3]]))
    }

    fn opaque(&mut self, start: usize, end: usize, reason: &'static str, tag: &'static str) {
        if start < end {
            self.unparsed_ranges.push(UnparsedRange {
                byte_range: ByteRange {
                    start: start as u64,
                    end: end as u64,
                },
                reason,
                record_tag: Some(tag),
            });
        }
    }

    fn local_error(&mut self, error: InspectError) -> Result<(), InspectError> {
        match error {
            InspectError::Format(diagnostic) if diagnostic.kind != ErrorKind::LimitExceeded => {
                self.resource_index_complete = false;
                self.diagnostics.push(diagnostic);
                Ok(())
            }
            other => Err(other),
        }
    }

    fn index(&mut self) -> Result<(), InspectError> {
        let order = self.inspection.header.byte_order;
        let drw_size = (self.inspection.leading_records[1].byte_range.end - 256) as usize;
        if drw_size < 48 || !(drw_size - 32).is_multiple_of(16) {
            return Err(failure(
                ErrorKind::Unsupported,
                "directory.unsupported_layout",
                260,
                "expected DRW length 32 + 16 * view_count with at least one view",
            ));
        }
        let views = (drw_size - 32) / 16;
        let entries = views + 2;
        if entries as u64 + 2 > self.limits.max_records {
            return Err(failure(
                ErrorKind::LimitExceeded,
                "limit.records",
                256,
                "too many indexed records",
            ));
        }
        self.records
            .extend_from_slice(&self.inspection.leading_records[..2]);
        self.opaque(8, 12, "unknown_header_fields", "MOD");
        self.opaque(56, 252, "unknown_header_fields", "MOD");
        self.opaque(264, 268, "unknown_header_fields", "DRW");
        // Preserve the eight-byte source view names without guessing encoding.
        self.opaque(268 + 8 * entries, 256 + drw_size - 4, "view_names", "DRW");
        let mut previous_end = 256 + drw_size;
        let mut usr = None;
        for index in 0..entries {
            let at = 268 + index * 4;
            let start = self.word(at)? as usize;
            let words = self.word(at + entries * 4)?;
            let is_usr = index == entries - 1;
            if is_usr && start == 0 && words == 0 {
                continue;
            }
            if start < previous_end || start == 0 || words == 0 {
                return Err(failure(
                    ErrorKind::Invalid,
                    "directory.overlap_or_missing",
                    at,
                    "indexed record overlaps an earlier record or has a missing range",
                ));
            }
            if start != previous_end {
                return Err(failure(
                    ErrorKind::Unsupported,
                    "directory.noncontiguous",
                    at,
                    "unobserved gap between indexed records",
                ));
            }
            let tag = if index == 0 {
                "RES"
            } else if is_usr {
                "USR"
            } else {
                "V/W"
            };
            let record = record_info(
                tag,
                start as u64,
                words,
                self.data.len() as u64,
                self.limits.inspection(),
            )?;
            let end = record.byte_range.end as usize;
            if &self.data[start..start + 3] != tag.as_bytes()
                || self.data[start + 3] != b'0'
                || &self.data[end - 4..end - 1] != tag.as_bytes()
                || self.data[end - 1] != b'1'
            {
                return Err(failure(
                    ErrorKind::Invalid,
                    "directory.tag_mismatch",
                    start,
                    "indexed record's start/end tags do not match",
                ));
            }
            // Older USR's word at +4 is NOT its physical size. The DRW index
            // and USR footer establish its range; preserve +4 as opaque data.
            if !is_usr && self.word(start + 4)? != words {
                return Err(failure(
                    ErrorKind::Invalid,
                    "directory.length_mismatch",
                    start + 4,
                    "directory and record lengths disagree",
                ));
            }
            if is_usr {
                usr = Some((start, end));
            } else {
                self.opaque(start + 8, end - 4, "record_payload", tag);
            }
            self.records.push(record);
            previous_end = end;
        }
        if let Some((start, end)) = usr {
            self.index_usr(start, end, order)?;
        }
        self.opaque(previous_end, self.data.len(), "uninspected_tail", "tail");
        Ok(())
    }

    fn index_usr(
        &mut self,
        start: usize,
        end: usize,
        order: ByteOrder,
    ) -> Result<(), InspectError> {
        let body_end = end - 4;
        // Only the observed USR prefix is supported. Never scan for a restart.
        if body_end < start + 44
            || self.data[start + 12..start + 28]
                != [0, 0, 0, 0, 0, 0, 0, 0, 0xe0, 0, 0, 0, 0, 0, 0, 0]
            || self.data[start + 28..start + 31] != [0x80, 0xff, 0]
            || !(1..=5).contains(&self.data[start + 31])
            || self.word(start + 32)? != 16
        {
            self.opaque(start + 4, body_end, "unsupported_usr_layout", "USR");
            return self.local_error(failure(
                ErrorKind::Unsupported,
                "usr.unsupported_layout",
                start,
                "USR prefix does not match a supported entity layout",
            ));
        }
        self.opaque(start + 4, start + 44, "unknown_usr_fields", "USR");
        let mut position = start + 44;
        let mut count = self.records.len() as u64 + 1;
        let mut terminated = false;
        let mut saw_end = false;
        while position < body_end {
            count += 1;
            if count > self.limits.max_records {
                return Err(failure(
                    ErrorKind::LimitExceeded,
                    "limit.records",
                    position,
                    "too many USR entities",
                ));
            }
            if body_end - position < 8 {
                self.local_error(failure(
                    ErrorKind::Invalid,
                    "entity.truncated",
                    position,
                    "entity header crosses the USR footer",
                ))?;
                break;
            }
            let kind = self.data[position];
            let version = self.data[position + 1];
            let bytes = [self.data[position + 2], self.data[position + 3]];
            let header_size = match order {
                ByteOrder::Little => u16::from_le_bytes(bytes),
                ByteOrder::Big => u16::from_be_bytes(bytes),
            } as usize;
            // Type 88 has a fixed size; its +4 word is an ID, NOT a length.
            let length = match (kind, version, header_size) {
                (0x88, 6, 96) => 96,
                (0x84 | 0x85 | 0x8f, 0, 0) | (0x86 | 0x87, _, _) => {
                    self.word(position + 4)? as usize
                }
                _ => {
                    self.local_error(failure(
                        ErrorKind::Unsupported,
                        "entity.unsupported_layout",
                        position,
                        "unknown entity framing; remaining USR bytes were not searched",
                    ))?;
                    break;
                }
            };
            if length < 8 || length % 8 != 0 || length > body_end - position {
                self.local_error(failure(
                    ErrorKind::Invalid,
                    "entity.invalid_length",
                    position + 4,
                    "entity length is unaligned, too small, or crosses the USR footer",
                ))?;
                break;
            }
            if length as u64 > self.limits.max_record_bytes {
                return Err(failure(
                    ErrorKind::LimitExceeded,
                    "limit.record_bytes",
                    position,
                    "USR entity exceeds the record byte limit",
                ));
            }
            let next = position + length;
            if kind == 0x8f {
                saw_end = true;
                if next != body_end
                    || ![8, 16].contains(&length)
                    || self.data[position + 8..next].iter().any(|&b| b != 0)
                {
                    self.local_error(failure(
                        ErrorKind::Invalid,
                        "usr.invalid_end",
                        position,
                        "USR end entity has an unexpected size, data, or following bytes",
                    ))?;
                } else {
                    terminated = true;
                }
                self.opaque(position, next, "entity_metadata", "USR");
                position = next;
                break;
            }
            if matches!(kind, 0x85..=0x87) {
                match self.resource(position, next, kind, version, header_size) {
                    Ok(resource) => {
                        if self.resources.len() as u64 >= self.limits.max_resources {
                            return Err(failure(
                                ErrorKind::LimitExceeded,
                                "limit.resources",
                                position,
                                "too many resource owners",
                            ));
                        }
                        self.opaque(
                            position,
                            resource.storage_range.start as usize,
                            "entity_metadata",
                            "USR",
                        );
                        self.resources.push(resource);
                    }
                    Err(error) => {
                        self.local_error(error)?;
                        self.opaque(position, next, "unsupported_resource", "USR");
                    }
                }
            } else {
                if kind == 0x84 && length != 16 {
                    self.local_error(failure(
                        ErrorKind::Unsupported,
                        "entity.unsupported_layout",
                        position,
                        "view entity has an unobserved length",
                    ))?;
                }
                self.opaque(position, next, "entity_metadata", "USR");
            }
            position = next;
        }
        if !terminated && !saw_end && position == body_end {
            self.local_error(failure(
                ErrorKind::Invalid,
                "usr.missing_end",
                position,
                "missing final USR end entity",
            ))?;
        }
        self.opaque(position, body_end, "unparsed_entities", "USR");
        Ok(())
    }

    fn resource(
        &self,
        start: usize,
        end: usize,
        kind: u8,
        version: u8,
        header: usize,
    ) -> Result<ResourceRef, InspectError> {
        let (stride, count_offset, encoding) = match (kind, version, header) {
            (0x85, 0, 0) => (16u64, 28, Encoding::Raw),
            (0x86, 2 | 3, 104) => (16u64, 28, Encoding::Raw),
            (0x86, 4, 104) => (16, 28, Encoding::Zlib),
            (0x86, 5, 104) => (20, 28, Encoding::Zlib),
            (0x87, 6, 32) => (20, 24, Encoding::Zlib),
            _ => {
                return Err(failure(
                    ErrorKind::Unsupported,
                    "resource.unsupported_layout",
                    start,
                    "unrecognized resource type/version/header length",
                ));
            }
        };
        let header = if kind == 0x85 { 32 } else { header };
        if end - start < header {
            return Err(failure(
                ErrorKind::Invalid,
                "resource.truncated_header",
                start,
                "resource header crosses the owning entity",
            ));
        }
        let count = u64::from(self.word(start + count_offset)?);
        if kind == 0x85 && (count != 0 || self.word(start + 24)? != 0) {
            return Err(failure(
                ErrorKind::Unsupported,
                "resource.unsupported_layout",
                start + 24,
                "legacy type 85 is supported only with zero extension words",
            ));
        }
        let table_size = (count * stride + 7) & !7;
        let payload = start as u64 + header as u64 + table_size;
        if payload >= end as u64 {
            return Err(failure(
                ErrorKind::Invalid,
                "resource.table_out_of_bounds",
                start + count_offset,
                "attribute table leaves no payload within its owner",
            ));
        }
        let declared = u64::from(self.word(start + 16)?);
        // Size policies are enforced at extraction so an oversized or corrupt
        // resource does not hide the other indexed resources.
        if declared == 0 {
            return Err(failure(
                ErrorKind::Invalid,
                "resource.invalid_length",
                start + 16,
                "zero decoded length is not a supported geometry resource",
            ));
        }
        Ok(ResourceRef {
            resource_id: format!("usr:{start:016x}"),
            owner_range: ByteRange {
                start: start as u64,
                end: end as u64,
            },
            storage_range: ByteRange {
                start: payload,
                end: end as u64,
            },
            encoding,
            declared_decoded_bytes: declared,
            owner_type: kind,
            layout_version: version,
            source_id: self.word(start + 8)?,
        })
    }

    pub fn extract(&self, resource_id: &str) -> Result<Extraction, InspectError> {
        let resource = self
            .resources
            .iter()
            .find(|r| r.resource_id == resource_id)
            .ok_or_else(|| {
                failure(
                    ErrorKind::Invalid,
                    "resource.not_found",
                    0,
                    "resource ID is not in this document",
                )
            })?;
        let storage =
            &self.data[resource.storage_range.start as usize..resource.storage_range.end as usize];
        let (payload, consumed) = decode(storage, resource, self.limits)?;
        let source = SourceRef {
            source_sha256: self.source_sha256.clone(),
            resource_id: resource.resource_id.clone(),
            container_range: ByteRange {
                start: resource.storage_range.start,
                end: resource.storage_range.start + consumed as u64,
            },
            decoded_range: ByteRange {
                start: 0,
                end: payload.len() as u64,
            },
            payload_sha256: sha256(&payload),
            encoding: resource.encoding,
        };
        Ok(Extraction { payload, source })
    }

    /// Read preserved source bytes without assigning meaning to them.
    pub fn source_bytes(&self, range: ByteRange) -> Result<&[u8], InspectError> {
        if range.start > range.end || range.end > self.data.len() as u64 {
            return Err(InspectError::format(
                ErrorKind::Invalid,
                "source.invalid_range",
                range.start,
                "requested source range lies outside the document",
            ));
        }
        self.limits.resource(range.end - range.start, range.start)?;
        Ok(&self.data[range.start as usize..range.end as usize])
    }
}
