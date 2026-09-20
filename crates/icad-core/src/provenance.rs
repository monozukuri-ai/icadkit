use crate::{ByteRange, Encoding};
use sha2::{Digest, Sha256};

pub(crate) fn sha256(data: &[u8]) -> String {
    format!("{:x}", Sha256::digest(data))
}

/// Container and decoded coordinates are independent coordinate spaces.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SourceRef {
    pub source_sha256: String,
    pub resource_id: String,
    /// Exact bytes consumed in the original ICD, excluding alignment padding.
    pub container_range: ByteRange,
    /// Range within the returned payload, never added to container_range.start.
    pub decoded_range: ByteRange,
    pub payload_sha256: String,
    pub encoding: Encoding,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Extraction {
    pub payload: Vec<u8>,
    pub source: SourceRef,
}
