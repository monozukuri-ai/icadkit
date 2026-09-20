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
