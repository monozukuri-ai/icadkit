use crate::{Encoding, ErrorKind, InspectError, ReadLimits, ResourceRef};
use flate2::{Decompress, FlushDecompress, Status};

fn invalid(code: &'static str, resource: &ResourceRef, message: &str) -> InspectError {
    InspectError::format(
        ErrorKind::Invalid,
        code,
        resource.storage_range.start,
        message,
    )
}

/// Bounded zlib with an explicit EOF/checksum and consumed-input boundary.
/// A fixed scratch buffer prevents an attacker-controlled size declaration
/// from causing an unbounded allocation. No concatenated streams are accepted.
pub(crate) fn decode(
    storage: &[u8],
    resource: &ResourceRef,
    limits: ReadLimits,
) -> Result<(Vec<u8>, usize), InspectError> {
    limits.resource(
        resource.declared_decoded_bytes,
        resource.storage_range.start,
    )?;
    limits.resource(storage.len() as u64, resource.storage_range.start)?;
    let (output, consumed) = match resource.encoding {
        Encoding::Raw => {
            let length = usize::try_from(resource.declared_decoded_bytes).map_err(|_| {
                invalid(
                    "resource.invalid_length",
                    resource,
                    "decoded length cannot fit in memory",
                )
            })?;
            let data = storage.get(..length).ok_or_else(|| {
                invalid(
                    "resource.truncated",
                    resource,
                    "declared payload exceeds its owner",
                )
            })?;
            (data.to_vec(), length)
        }
        Encoding::Zlib => {
            let mut decoder = Decompress::new(true);
            let mut output = Vec::new();
            let mut buffer = [0u8; 64 * 1024];
            loop {
                let before_in = decoder.total_in();
                let before_out = decoder.total_out();
                let input = storage.get(before_in as usize..).ok_or_else(|| {
                    invalid(
                        "compression.invalid",
                        resource,
                        "decoder consumed outside resource",
                    )
                })?;
                let status = decoder
                    .decompress(input, &mut buffer, FlushDecompress::None)
                    .map_err(|_| {
                        invalid(
                            "compression.invalid",
                            resource,
                            "invalid zlib stream or checksum",
                        )
                    })?;
                let produced = (decoder.total_out() - before_out) as usize;
                limits.resource(decoder.total_out(), resource.storage_range.start)?;
                if decoder.total_out() > resource.declared_decoded_bytes {
                    return Err(invalid(
                        "resource.decoded_size_mismatch",
                        resource,
                        "zlib output exceeds the owner's decoded size",
                    ));
                }
                output.extend_from_slice(&buffer[..produced]);
                if status == Status::StreamEnd {
                    break;
                }
                if before_in == decoder.total_in() && before_out == decoder.total_out() {
                    return Err(invalid(
                        "compression.truncated",
                        resource,
                        "zlib stream ended without a checksum-complete EOF",
                    ));
                }
            }
            if decoder.total_out() != resource.declared_decoded_bytes {
                return Err(invalid(
                    "resource.decoded_size_mismatch",
                    resource,
                    "zlib output differs from the owner's decoded size",
                ));
            }
            (output, decoder.total_in() as usize)
        }
    };
    let padding = &storage[consumed..];
    if padding.len() > 7 || padding.iter().any(|&b| b != 0) {
        return Err(invalid(
            "resource.invalid_padding",
            resource,
            "expected at most seven zero alignment bytes after the payload",
        ));
    }
    // This is only the bounded neutral-binary envelope, not a schema-dependent
    // node walk. The suffix is the observed type=1,index=0 encoding; full node
    // Node termination is validated by read_geometry; raw_geometry stays not_checked.
    if !output.starts_with(b"PS\0\0") || !output.ends_with(b"\0\x01\0\x01") {
        return Err(invalid(
            "resource.invalid_envelope",
            resource,
            "expected a neutral X_B signature and terminal byte suffix",
        ));
    }
    let header = parasolid_core::inspect_xb(
        &output,
        parasolid_core::InspectionLimits {
            max_file_size: output.len(),
            max_string_bytes: output.len(),
        },
    )
    .map_err(|error| {
        InspectError::format(
            ErrorKind::Invalid,
            "resource.invalid_header",
            resource.storage_range.start,
            format!("X_B header in decoded coordinates: {error}"),
        )
    })?;
    if header.header_range.end > output.len() - 4 {
        return Err(invalid(
            "resource.invalid_envelope",
            resource,
            "terminal suffix overlaps the X_B header",
        ));
    }
    Ok((output, consumed))
}
