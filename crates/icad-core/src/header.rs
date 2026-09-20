use std::fs::File;
use std::io::{Cursor, Read, Seek};
use std::path::Path;

use crate::reader::Reader;
use crate::record::{check_end_tag, read_record, record_info};
use crate::{ByteOrder, ByteRange, ErrorKind, InspectError, InspectionLimits, RecordInfo};

/// Raw fields from the observed 256-byte MOD record. Version meaning is unknown.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Header {
    pub byte_order: ByteOrder,
    pub raw_version: [u8; 4],
    pub raw_name: [u8; 40],
    pub raw_mod: [u8; 256],
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Status {
    NotChecked,
    Complete,
    Partial,
    Unsupported,
    Invalid,
}

impl Status {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::NotChecked => "not_checked",
            Self::Complete => "complete",
            Self::Partial => "partial",
            Self::Unsupported => "unsupported",
            Self::Invalid => "invalid",
        }
    }
}

/// File-level scopes; complete header inspection never means a complete model.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InspectionStatus {
    pub header: Status,
    pub container: Status,
    pub extraction: Status,
    pub raw_geometry: Status,
    pub brep: Status,
    pub model: Status,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct UnparsedRange {
    pub byte_range: ByteRange,
    pub reason: &'static str,
    pub record_tag: Option<&'static str>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Inspection {
    pub file_size: u64,
    pub bytes_read: u64,
    pub header: Header,
    pub leading_records: [RecordInfo; 3],
    pub unparsed_ranges: Vec<UnparsedRange>,
    pub status: InspectionStatus,
}

/// Inspect borrowed immutable bytes without copying the complete input.
pub fn inspect_bytes(data: &[u8], limits: InspectionLimits) -> Result<Inspection, InspectError> {
    inspect_reader(Cursor::new(data), limits)
}

/// Inspect a regular file with bounded reads and seeks, without hashing it.
/// Symlinks to regular files are accepted. Callers must keep the file stable.
pub fn inspect_file(
    path: impl AsRef<Path>,
    limits: InspectionLimits,
) -> Result<Inspection, InspectError> {
    limits.validate()?;
    let path = path.as_ref();
    // Check before open as well, so a named pipe does not normally block open().
    let metadata = std::fs::metadata(path)?;
    if !metadata.is_file() {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            "inspection requires a regular file",
        )
        .into());
    }
    let file = File::open(path)?;
    if !file.metadata()?.is_file() {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            "inspection requires a regular file",
        )
        .into());
    }
    inspect_reader(file, limits)
}

/// Inspect from absolute byte zero of a seekable source. Exactly 280 bytes are
/// read on success, independent of record payload and tail sizes. The source's
/// cursor is not restored. No framing rule is applied after the RES record.
pub fn inspect_reader<R: Read + Seek>(
    source: R,
    limits: InspectionLimits,
) -> Result<Inspection, InspectError> {
    limits.validate()?;
    let mut reader = Reader::new(source)?;
    limits.check_file(reader.len)?;
    let prefix: [u8; 8] = reader.array(0, "header.truncated")?;
    if &prefix[..4] != b"MOD0" {
        return Err(InspectError::format(
            ErrorKind::Invalid,
            "header.invalid_magic",
            0,
            "expected MOD0",
        ));
    }
    let order = match prefix[4..8] {
        [0x40, 0, 0, 0] => ByteOrder::Little,
        [0, 0, 0, 0x40] => ByteOrder::Big,
        _ => {
            return Err(InspectError::format(
                ErrorKind::Unsupported,
                "header.unsupported_layout",
                4,
                format!(
                    "expected a 64-word MOD length in either byte order, found {:02x?}",
                    &prefix[4..8]
                ),
            ));
        }
    };
    let mod_record = record_info("MOD", 0, 64, reader.len, limits)?;
    let rest: [u8; 248] = reader.array(8, "header.truncated")?;
    let mut raw_mod = [0; 256];
    raw_mod[..8].copy_from_slice(&prefix);
    raw_mod[8..].copy_from_slice(&rest);
    check_end_tag(
        [raw_mod[252], raw_mod[253], raw_mod[254], raw_mod[255]],
        *b"MOD1",
        252,
    )?;
    let mut raw_name = [0; 40];
    raw_name.copy_from_slice(&raw_mod[16..56]);
    let header = Header {
        byte_order: order,
        raw_version: [raw_mod[12], raw_mod[13], raw_mod[14], raw_mod[15]],
        raw_name,
        raw_mod,
    };
    let drw = read_record(&mut reader, "DRW", (*b"DRW0", *b"DRW1"), 256, order, limits)?;
    let res = read_record(
        &mut reader,
        "RES",
        (*b"RES0", *b"RES1"),
        drw.byte_range.end,
        order,
        limits,
    )?;
    let leading_records = [mod_record, drw, res];
    let mut unparsed_ranges = vec![
        UnparsedRange {
            byte_range: ByteRange { start: 8, end: 12 },
            reason: "unknown_header_fields",
            record_tag: Some("MOD"),
        },
        UnparsedRange {
            byte_range: ByteRange {
                start: 56,
                end: 252,
            },
            reason: "unknown_header_fields",
            record_tag: Some("MOD"),
        },
    ];
    for record in &leading_records[1..] {
        if record.payload_range.start < record.payload_range.end {
            unparsed_ranges.push(UnparsedRange {
                byte_range: record.payload_range,
                reason: "record_payload",
                record_tag: Some(record.tag),
            });
        }
    }
    let tail = leading_records[2].byte_range.end;
    if tail < reader.len {
        unparsed_ranges.push(UnparsedRange {
            byte_range: ByteRange {
                start: tail,
                end: reader.len,
            },
            reason: "uninspected_tail",
            record_tag: None,
        });
    }
    Ok(Inspection {
        file_size: reader.len,
        bytes_read: reader.bytes_read,
        header,
        leading_records,
        unparsed_ranges,
        status: InspectionStatus {
            header: Status::Complete,
            container: Status::Partial,
            extraction: Status::NotChecked,
            raw_geometry: Status::NotChecked,
            brep: Status::NotChecked,
            model: Status::NotChecked,
        },
    })
}
