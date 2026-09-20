use icad_core::{ByteRange, Document, ErrorKind, InspectError, ReadLimits};

fn word(data: &mut [u8], at: usize, value: u32, big: bool) {
    data[at..at + 4].copy_from_slice(&if big {
        value.to_be_bytes()
    } else {
        value.to_le_bytes()
    });
}

fn fixture(big: bool) -> Vec<u8> {
    // A hand-authored empty-node X_B envelope, not a CAD-produced shape.
    let schema = b"SCH_3000000_30000";
    let mut payload = b"PS\0\0\0\x01T".to_vec();
    payload.extend_from_slice(&(schema.len() as u32).to_be_bytes());
    payload.extend_from_slice(schema);
    payload.extend_from_slice(&[0, 0, 0, 0, 0, 1, 0, 1]);
    let entity_length = (32 + payload.len() + 7) & !7;
    let usr_length = 72 + entity_length;
    let mut data = vec![0; 512 + usr_length];
    for (tag, start, length) in [
        (b"MOD", 0, 256),
        (b"DRW", 256, 48),
        (b"RES", 304, 192),
        (b"V/W", 496, 16),
        (b"USR", 512, usr_length),
    ] {
        data[start..start + 3].copy_from_slice(tag);
        data[start + 3] = b'0';
        word(&mut data, start + 4, (length / 4) as u32, big);
        data[start + length - 4..start + length - 1].copy_from_slice(tag);
        data[start + length - 1] = b'1';
    }
    for (at, value) in [
        (268, 304),
        (272, 496),
        (276, 512),
        (280, 48),
        (284, 4),
        (288, (usr_length / 4) as u32),
    ] {
        word(&mut data, at, value, big);
    }
    data[532] = 0xe0;
    data[540..544].copy_from_slice(&[0x80, 0xff, 0, 1]);
    word(&mut data, 544, 16, big);
    data[556] = 0x84;
    word(&mut data, 560, 16, big);
    data[572] = 0x85;
    word(&mut data, 576, entity_length as u32, big);
    word(&mut data, 580, 1, big);
    word(&mut data, 588, payload.len() as u32, big);
    data[604..604 + payload.len()].copy_from_slice(&payload);
    data[572 + entity_length] = 0x8f;
    word(&mut data, 576 + entity_length, 8, big);
    data
}

#[test]
fn owned_raw_extraction_bounds_and_hashes() -> Result<(), InspectError> {
    for big in [false, true] {
        let data = fixture(big);
        let doc = Document::from_bytes(&data, ReadLimits::default())?;
        assert!(doc.resource_index_complete());
        assert_eq!(doc.resources().len(), 1);
        let extracted = doc.extract(&doc.resources()[0].resource_id)?;
        assert_eq!(extracted.source.container_range.start, 604);
        assert_eq!(
            extracted.payload,
            doc.source_bytes(extracted.source.container_range)?
        );
        assert_eq!(extracted.source.source_sha256, doc.source_sha256());
        assert_eq!(doc.extract(&doc.resources()[0].resource_id)?, extracted);
        assert!(
            doc.source_bytes(ByteRange {
                start: 1,
                end: u64::MAX
            })
            .is_err()
        );
    }
    Ok(())
}

#[test]
fn all_truncations_and_single_byte_mutations_are_bounded() {
    for big in [false, true] {
        let data = fixture(big);
        for end in 0..data.len() {
            assert!(Document::from_bytes(&data[..end], ReadLimits::default()).is_err());
        }
        for at in 0..data.len() {
            let mut changed = data.clone();
            changed[at] ^= 0xff;
            if let Ok(doc) = Document::from_owned(changed, ReadLimits::default()) {
                for resource in doc.resources() {
                    let _ = doc.extract(&resource.resource_id);
                }
            }
        }
    }
}

#[test]
fn file_policy_precedes_format_checks_and_index_is_lazy() -> Result<(), InspectError> {
    let data = fixture(false);
    let tiny_file = ReadLimits {
        max_file_bytes: 1,
        ..ReadLimits::default()
    };
    assert!(matches!(Document::from_bytes(&data,tiny_file),
        Err(InspectError::Format(d)) if d.kind == ErrorKind::LimitExceeded));
    let doc = Document::from_owned(
        data,
        ReadLimits {
            max_resource_bytes: 1,
            ..ReadLimits::default()
        },
    )?;
    assert!(doc.resource_index_complete());
    assert!(matches!(doc.extract(&doc.resources()[0].resource_id),
        Err(InspectError::Format(d)) if d.code == "limit.resource_bytes"));
    Ok(())
}

#[test]
fn geometry_scope_and_limit_diagnostics_survive_source_drop()
-> Result<(), Box<dyn std::error::Error>> {
    use icad_core::{GeometryError, GeometryLimits, Status};
    let doc = Document::from_owned(fixture(false), ReadLimits::default())?;
    let id = &doc.resources()[0].resource_id;
    let result = doc.read_geometry(id, None, GeometryLimits::default())?;
    assert_eq!(result.status().extraction, Status::Complete);
    assert_eq!(result.status().raw_geometry, Status::Complete);
    assert_eq!(result.status().brep, Status::Unsupported);
    assert_eq!(result.status().topology, Status::NotChecked);
    assert_eq!(result.schema().map(|s| s.kind), Some("builtin"));
    let limited = doc.read_geometry(
        id,
        None,
        GeometryLimits {
            max_payload_bytes: 1,
            ..GeometryLimits::default()
        },
    );
    assert!(
        matches!(limited, Err(GeometryError::Limit(d)) if d.category == ErrorKind::LimitExceeded && d.byte_offset == Some(604) && d.decoded_offset.is_none())
    );
    drop(doc);
    assert_eq!(result.raw().map(|r| r.nodes.len()), Some(0));
    assert!(result.brep().is_none());
    assert!(!result.diagnostics().is_empty());
    Ok(())
}

#[test]
fn geometry_bridge_handles_all_single_byte_mutations() -> Result<(), InspectError> {
    let data = fixture(false);
    for at in 0..data.len() {
        let mut changed = data.clone();
        changed[at] ^= 0xff;
        if let Ok(doc) = Document::from_owned(changed, ReadLimits::default()) {
            for resource in doc.resources() {
                let _ = doc.read_geometry(
                    &resource.resource_id,
                    None,
                    icad_core::GeometryLimits::default(),
                );
            }
        }
    }
    Ok(())
}
