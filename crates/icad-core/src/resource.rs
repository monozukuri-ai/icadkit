use crate::ByteRange;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Encoding {
    Raw,
    Zlib,
}

impl Encoding {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Raw => "raw",
            Self::Zlib => "zlib",
        }
    }
}

/// A structurally owned resource, not a signature-scan candidate. Its contents
/// and checksum are deliberately not validated until extraction.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ResourceRef {
    pub resource_id: String,
    pub owner_range: ByteRange,
    /// Payload storage including the owner's zero alignment padding (0..7).
    /// Extraction reports the exact encoded range separately.
    pub storage_range: ByteRange,
    pub encoding: Encoding,
    pub declared_decoded_bytes: u64,
    pub owner_type: u8,
    pub layout_version: u8,
    /// Uninterpreted source identifier. Never treated as an assembly instance.
    pub source_id: u32,
}

/// One saved record naming the resource used by a native entity. The frame is
/// origin, Z axis and X axis as stored; no placement is evaluated here.
#[derive(Debug, Clone, PartialEq)]
pub struct ResourceAssociation {
    pub byte_range: ByteRange,
    pub entity_source_id: u32,
    pub resource_source_id: u32,
    pub frame: [f64; 9],
}
