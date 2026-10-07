//! Authored framing fixtures for byte-exact container serialization.

use icad_core::{ByteOrder, Document, ErrorKind, InspectError, ReadLimits};

fn word(value: u32, order: ByteOrder) -> [u8; 4] {
    match order {
        ByteOrder::Little => value.to_le_bytes(),
        ByteOrder::Big => value.to_be_bytes(),
    }
}

/// Assemble MOD, DRW, the given record bodies (bytes from +8 to the end tag)
/// and a tail. `usr_declared` overrides the USR length word; `total`
/// overrides the MOD total-word field.
fn assemble(
    order: ByteOrder,
    bodies: &[(&str, Vec<u8>)],
    names: &[[u8; 8]],
    tail: &[u8],
    usr_declared: Option<u32>,
    total: Option<u32>,
) -> Vec<u8> {
    let drw_len = 32 + 16 * names.len();
    let mut records = Vec::new();
    let mut starts = Vec::new();
    let mut lengths = Vec::new();
    let mut cursor = 256 + drw_len;
    for (tag, body) in bodies {
        let words = (body.len() + 12) / 4;
        starts.push(cursor as u32);
        lengths.push(words as u32);
        cursor += words * 4;
        let declared = match (*tag, usr_declared) {
            ("USR", Some(value)) => value,
            _ => words as u32,
        };
        records.extend_from_slice(tag.as_bytes());
        records.push(b'0');
        records.extend_from_slice(&word(declared, order));
        records.extend_from_slice(body);
        records.extend_from_slice(tag.as_bytes());
        records.push(b'1');
    }
    starts.resize(names.len() + 2, 0);
    lengths.resize(names.len() + 2, 0);
    let mut data = Vec::new();
    data.extend_from_slice(b"MOD0");
    data.extend_from_slice(&word(64, order));
    data.extend_from_slice(&[0x11, 0x22, 0x33, 0x44]);
    data.extend_from_slice(&[0, 8, 0, 3]);
    data.extend_from_slice(b"container test                          ");
    data.extend(std::iter::repeat_n(0x5a, 180));
    let total = total.unwrap_or(((cursor - 256) / 4) as u32);
    data.extend_from_slice(&word(total, order));
    data.extend_from_slice(&[
        0xaa, 0xbb, 0xcc, 0xdd, 0xee, 0xff, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06,
    ]);
    data.extend_from_slice(b"MOD1");
    assert_eq!(data.len(), 256);
    data.extend_from_slice(b"DRW0");
    data.extend_from_slice(&word((drw_len / 4) as u32, order));
    data.extend_from_slice(&[0x11, 0x22, 0x33, 0x44]);
    for value in starts.iter().chain(&lengths) {
        data.extend_from_slice(&word(*value, order));
    }
    for name in names {
        data.extend_from_slice(name);
    }
    data.extend_from_slice(b"DRW1");
    data.extend_from_slice(&records);
    data.extend_from_slice(tail);
    data
}

fn bodies(with_usr: bool) -> Vec<(&'static str, Vec<u8>)> {
    let mut out = vec![
        ("RES", [0x11, 0x22, 0x33, 0x44].repeat(45)),
        ("V/W", vec![0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88]),
        ("V/W", [0x11, 0x22, 0x33, 0x44].repeat(7)),
    ];
    if with_usr {
        out.push(("USR", [0x11, 0x22, 0x33, 0x44].repeat(13)));
    }
    out
}

const NAMES: [[u8; 8]; 2] = [*b"!!GLOBAL", *b"3DGLOBAL"];

#[test]
fn round_trip_reproduces_input_in_both_orders() -> Result<(), InspectError> {
    for order in [ByteOrder::Little, ByteOrder::Big] {
        for with_usr in [true, false] {
            let data = assemble(
                order,
                &bodies(with_usr),
                &NAMES,
                b"tail\0\0\0\0",
                None,
                None,
            );
            let doc = Document::from_bytes(&data, ReadLimits::default())?;
            let container = doc.container()?;
            assert_eq!(container.byte_order, order);
            assert_eq!(container.view_names, NAMES.to_vec());
            assert_eq!(container.records.len(), if with_usr { 4 } else { 3 });
            assert_eq!(container.tail, b"tail\0\0\0\0");
            assert!(container.diagnostics.is_empty());
            assert_eq!(container.to_bytes()?, data);
        }
    }
    Ok(())
}

#[test]
fn legacy_usr_length_word_is_written_back() -> Result<(), InspectError> {
    let data = assemble(
        ByteOrder::Big,
        &bodies(true),
        &NAMES,
        b"",
        Some(27_952),
        None,
    );
    let doc = Document::from_bytes(&data, ReadLimits::default())?;
    let container = doc.container()?;
    assert_eq!(container.records[3].declared_words, 27_952);
    assert_eq!(container.records[3].words()?, 16);
    assert_eq!(container.to_bytes()?, data);
    Ok(())
}

#[test]
fn edits_recompute_directory_lengths_and_total() -> Result<(), InspectError> {
    let order = ByteOrder::Little;
    let data = assemble(order, &bodies(true), &NAMES, b"tail", None, None);
    let doc = Document::from_bytes(&data, ReadLimits::default())?;
    let mut container = doc.container()?;
    container.records[1]
        .body
        .extend_from_slice(&[9, 9, 9, 9, 9, 9, 9, 9]);
    let out = container.to_bytes()?;
    assert_eq!(out.len(), data.len() + 8);
    let again = Document::from_bytes(&out, ReadLimits::default())?;
    let ranges: Vec<_> = again
        .records()
        .iter()
        .map(|r| (r.tag, r.byte_range.start, r.byte_range.end))
        .collect();
    assert_eq!(
        ranges,
        vec![
            ("MOD", 0, 256),
            ("DRW", 256, 320),
            ("RES", 320, 512),
            ("V/W", 512, 540),
            ("V/W", 540, 580),
            ("USR", 580, 644),
        ]
    );
    assert_eq!(&out[236..240], &((644 - 256) / 4_u32).to_le_bytes());
    assert_eq!(&out[644..], b"tail");
    assert_eq!(again.container()?.to_bytes()?, out);
    Ok(())
}

#[test]
fn stored_total_mismatch_is_reported_and_recomputed() -> Result<(), InspectError> {
    let order = ByteOrder::Little;
    let consistent = assemble(order, &bodies(true), &NAMES, b"", None, None);
    let data = assemble(order, &bodies(true), &NAMES, b"", None, Some(7));
    let doc = Document::from_bytes(&data, ReadLimits::default())?;
    let container = doc.container()?;
    assert_eq!(container.diagnostics.len(), 1);
    assert_eq!(container.diagnostics[0].code, "container.total_words");
    assert_eq!(container.diagnostics[0].kind, ErrorKind::Unsupported);
    assert_eq!(container.diagnostics[0].byte_offset, 236);
    assert_eq!(container.to_bytes()?, consistent);
    Ok(())
}

#[test]
fn inconsistent_models_are_rejected() -> Result<(), InspectError> {
    let data = assemble(ByteOrder::Little, &bodies(true), &NAMES, b"", None, None);
    let doc = Document::from_bytes(&data, ReadLimits::default())?;
    let container = doc.container()?;
    let code = |result: Result<Vec<u8>, InspectError>| match result {
        Err(InspectError::Format(diagnostic)) => diagnostic.code,
        _ => "ok",
    };
    let mut unaligned = container.clone();
    unaligned.records[2].body.push(0);
    assert_eq!(code(unaligned.to_bytes()), "container.alignment");
    let mut unnamed = container.clone();
    unnamed.view_names.pop();
    assert_eq!(code(unnamed.to_bytes()), "container.view_names");
    let mut reordered = container.clone();
    reordered.records.swap(0, 1);
    assert_eq!(code(reordered.to_bytes()), "container.record_order");
    let mut usr_first = container;
    usr_first.records.rotate_right(1);
    assert_eq!(code(usr_first.to_bytes()), "container.record_order");
    Ok(())
}
