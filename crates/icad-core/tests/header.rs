//! Authored framing fixtures, not iCAD-generated models or geometry oracles.

use std::io::{self, Cursor, Read, Seek, SeekFrom};

use icad_core::{
    ByteOrder, ByteRange, ErrorKind, InspectError, InspectionLimits, Status, inspect_bytes,
    inspect_reader,
};

fn words(value: u32, order: ByteOrder) -> [u8; 4] {
    match order {
        ByteOrder::Little => value.to_le_bytes(),
        ByteOrder::Big => value.to_be_bytes(),
    }
}

fn fixture(order: ByteOrder) -> Vec<u8> {
    let mut data = Vec::new();
    for (tag, size) in [(b"MOD", 256_u32), (b"DRW", 48), (b"RES", 192)] {
        data.extend_from_slice(tag);
        data.push(b'0');
        data.extend_from_slice(&words(size / 4, order));
        data.extend(std::iter::repeat_n(0xA5, size as usize - 12));
        data.extend_from_slice(tag);
        data.push(b'1');
    }
    data[12..16].copy_from_slice(&[0, 8, 0, 3]);
    data[16..56].fill(b' ');
    data[16..20].copy_from_slice(b"test");
    data
}

fn assert_error(data: &[u8], code: &str, offset: u64) {
    let result = inspect_bytes(data, InspectionLimits::default());
    assert!(
        matches!(result, Err(InspectError::Format(ref diagnostic)) if diagnostic.code == code && diagnostic.byte_offset == offset),
        "expected {code} at {offset}, got {result:?}"
    );
}

#[test]
fn reads_both_orders_preserves_raw_and_marks_unknowns() -> Result<(), InspectError> {
    for order in [ByteOrder::Little, ByteOrder::Big] {
        let mut input = fixture(order);
        input.extend_from_slice(b"USR0not a proven record: MOD0 PS\0\0");
        let parsed = inspect_bytes(&input, InspectionLimits::default())?;
        assert_eq!(parsed.header.byte_order, order);
        assert_eq!(parsed.header.raw_version, [0, 8, 0, 3]);
        assert_eq!(&parsed.header.raw_mod, &input[..256]);
        assert_eq!(&parsed.header.raw_name, &input[16..56]);
        assert_eq!(parsed.bytes_read, 280);
        assert_eq!(
            parsed.leading_records[2].byte_range,
            ByteRange {
                start: 304,
                end: 496
            }
        );
        assert_eq!(parsed.status.header, Status::Complete);
        assert_eq!(parsed.status.container, Status::Partial);
        assert_eq!(parsed.status.model, Status::NotChecked);
        assert_eq!(parsed.unparsed_ranges.len(), 5);
        assert_eq!(parsed.unparsed_ranges[4].byte_range.start, 496);
        assert_eq!(parsed.unparsed_ranges[4].byte_range.end, input.len() as u64);
    }
    Ok(())
}

#[test]
fn every_truncation_of_the_required_prefix_is_an_error() {
    for order in [ByteOrder::Little, ByteOrder::Big] {
        let input = fixture(order);
        for end in 0..input.len() {
            assert!(
                inspect_bytes(&input[..end], InspectionLimits::default()).is_err(),
                "accepted truncation at {end}"
            );
        }
    }
}

#[test]
fn rejects_bad_magic_unknown_layout_and_mismatched_tags() {
    let input = fixture(ByteOrder::Little);
    for (offset, code, error_offset) in [
        (0, "header.invalid_magic", 0),
        (4, "header.unsupported_layout", 4),
        (252, "record.end_tag_mismatch", 252),
        (256, "record.start_tag_mismatch", 256),
        (300, "record.end_tag_mismatch", 300),
        (304, "record.start_tag_mismatch", 304),
        (492, "record.end_tag_mismatch", 492),
    ] {
        let mut corrupted = input.clone();
        corrupted[offset] ^= 0xff;
        assert_error(&corrupted, code, error_offset);
    }
}

#[test]
fn lengths_are_unsigned_checked_and_not_resynchronized() {
    for order in [ByteOrder::Little, ByteOrder::Big] {
        for offset in [260, 308] {
            for length in [0, 1, 2, u32::MAX, 1 << 30] {
                let mut input = fixture(order);
                input[offset..offset + 4].copy_from_slice(&words(length, order));
                assert_error(
                    &input,
                    if length < 3 {
                        "record.invalid_length"
                    } else {
                        "record.out_of_bounds"
                    },
                    offset as u64,
                );
            }
        }
    }
}

#[test]
fn minimal_frames_still_do_not_establish_model_completeness() -> Result<(), InspectError> {
    let mut input = fixture(ByteOrder::Little);
    input.truncate(256);
    for tag in [b"DRW", b"RES"] {
        input.extend_from_slice(tag);
        input.push(b'0');
        input.extend_from_slice(&3_u32.to_le_bytes());
        input.extend_from_slice(tag);
        input.push(b'1');
    }
    let parsed = inspect_bytes(&input, InspectionLimits::default())?;
    assert_eq!(parsed.unparsed_ranges.len(), 2);
    assert_eq!(parsed.status.container, Status::Partial);
    assert_eq!(parsed.status.model, Status::NotChecked);
    Ok(())
}

#[test]
fn limits_apply_before_payload_reads() {
    let input = fixture(ByteOrder::Little);
    for limits in [
        InspectionLimits {
            max_file_bytes: 495,
            ..InspectionLimits::default()
        },
        InspectionLimits {
            max_record_bytes: 255,
            ..InspectionLimits::default()
        },
    ] {
        assert!(
            matches!(inspect_bytes(&input, limits), Err(InspectError::Format(d)) if d.kind == ErrorKind::LimitExceeded)
        );
    }
    assert!(
        inspect_bytes(
            &input,
            InspectionLimits {
                max_file_bytes: 0,
                ..InspectionLimits::default()
            }
        )
        .is_err()
    );
}

struct FramingOnly {
    inner: Cursor<Vec<u8>>,
    reads: Vec<(u64, usize)>,
}

impl Read for FramingOnly {
    fn read(&mut self, bytes: &mut [u8]) -> io::Result<usize> {
        let start = self.inner.position();
        let end = start + bytes.len() as u64;
        let allowed = [(0, 256), (256, 264), (300, 304), (304, 312), (492, 496)];
        if !allowed.iter().any(|(a, b)| start >= *a && end <= *b) {
            return Err(io::Error::other("attempted to read payload or tail"));
        }
        self.reads.push((start, bytes.len()));
        self.inner.read(bytes)
    }
}

impl Seek for FramingOnly {
    fn seek(&mut self, position: SeekFrom) -> io::Result<u64> {
        self.inner.seek(position)
    }
}

#[test]
fn only_fixed_header_and_record_framing_are_read() -> Result<(), InspectError> {
    let mut bytes = fixture(ByteOrder::Little);
    bytes.resize(1024 * 1024, 0xcc);
    let mut reader = FramingOnly {
        inner: Cursor::new(bytes),
        reads: Vec::new(),
    };
    let parsed = inspect_reader(&mut reader, InspectionLimits::default())?;
    assert_eq!(parsed.bytes_read, 280);
    assert_eq!(
        reader.reads.iter().map(|(_, size)| size).sum::<usize>(),
        280
    );
    Ok(())
}

#[test]
fn early_eof_after_length_measurement_is_not_success() {
    struct Shrinking(Cursor<Vec<u8>>);
    impl Seek for Shrinking {
        fn seek(&mut self, pos: SeekFrom) -> io::Result<u64> {
            self.0.seek(pos)
        }
    }
    impl Read for Shrinking {
        fn read(&mut self, _buffer: &mut [u8]) -> io::Result<usize> {
            Ok(0)
        }
    }
    assert!(
        matches!(inspect_reader(Shrinking(Cursor::new(fixture(ByteOrder::Little))), InspectionLimits::default()), Err(InspectError::Format(d)) if d.code == "header.truncated")
    );
}
