use std::io::{Read, Seek};

use crate::reader::Reader;
use crate::{ByteOrder, ErrorKind, InspectError, InspectionLimits};

/// Half-open byte range in the original ICD input.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ByteRange {
    pub start: u64,
    pub end: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RecordInfo {
    pub tag: &'static str,
    pub byte_range: ByteRange,
    pub payload_range: ByteRange,
}

pub(crate) fn record_info(
    tag: &'static str,
    offset: u64,
    words: u32,
    file_size: u64,
    limits: InspectionLimits,
) -> Result<RecordInfo, InspectError> {
    let size = u64::from(words).checked_mul(4).ok_or_else(|| {
        InspectError::format(
            ErrorKind::Invalid,
            "record.range_overflow",
            offset,
            "length overflow",
        )
    })?;
    if size < 12 {
        return Err(InspectError::format(
            ErrorKind::Invalid,
            "record.invalid_length",
            offset + 4,
            format!("{tag} length {size} is smaller than its 12-byte framing"),
        ));
    }
    let end = offset.checked_add(size).ok_or_else(|| {
        InspectError::format(
            ErrorKind::Invalid,
            "record.range_overflow",
            offset,
            "record end overflow",
        )
    })?;
    if end > file_size {
        return Err(InspectError::format(
            ErrorKind::Invalid,
            "record.out_of_bounds",
            offset + 4,
            format!("{tag} end {end} exceeds file size {file_size}"),
        ));
    }
    if size > limits.max_record_bytes {
        return Err(InspectError::format(
            ErrorKind::LimitExceeded,
            "limit.record_bytes",
            offset + 4,
            format!(
                "{tag} length {size} exceeds {} bytes",
                limits.max_record_bytes
            ),
        ));
    }
    Ok(RecordInfo {
        tag,
        byte_range: ByteRange { start: offset, end },
        payload_range: ByteRange {
            start: offset + 8,
            end: end - 4,
        },
    })
}

pub(crate) fn check_end_tag(
    actual: [u8; 4],
    expected: [u8; 4],
    offset: u64,
) -> Result<(), InspectError> {
    if actual != expected {
        return Err(InspectError::format(
            ErrorKind::Invalid,
            "record.end_tag_mismatch",
            offset,
            format!("expected {expected:02x?}, found {actual:02x?}"),
        ));
    }
    Ok(())
}

pub(crate) fn read_record<R: Read + Seek>(
    reader: &mut Reader<R>,
    tag: &'static str,
    tags: ([u8; 4], [u8; 4]),
    offset: u64,
    order: ByteOrder,
    limits: InspectionLimits,
) -> Result<RecordInfo, InspectError> {
    let prefix: [u8; 8] = reader.array(offset, "record.truncated")?;
    if prefix[..4] != tags.0 {
        return Err(InspectError::format(
            ErrorKind::Invalid,
            "record.start_tag_mismatch",
            offset,
            format!("expected {:02x?}, found {:02x?}", tags.0, &prefix[..4]),
        ));
    }
    let words = order.u32([prefix[4], prefix[5], prefix[6], prefix[7]]);
    let record = record_info(tag, offset, words, reader.len, limits)?;
    let footer = record.byte_range.end - 4;
    check_end_tag(reader.array(footer, "record.truncated")?, tags.1, footer)?;
    Ok(record)
}
