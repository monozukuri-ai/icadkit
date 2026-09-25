use std::io::{Read, Seek, SeekFrom};

use crate::{ErrorKind, InspectError};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ByteOrder {
    Little,
    Big,
}

impl ByteOrder {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Little => "little",
            Self::Big => "big",
        }
    }

    pub(crate) fn u32(self, bytes: [u8; 4]) -> u32 {
        match self {
            Self::Little => u32::from_le_bytes(bytes),
            Self::Big => u32::from_be_bytes(bytes),
        }
    }

    pub(crate) fn u16(self, bytes: [u8; 2]) -> u16 {
        match self {
            Self::Little => u16::from_le_bytes(bytes),
            Self::Big => u16::from_be_bytes(bytes),
        }
    }

    pub(crate) fn f64(self, bytes: [u8; 8]) -> f64 {
        match self {
            Self::Little => f64::from_le_bytes(bytes),
            Self::Big => f64::from_be_bytes(bytes),
        }
    }
}

pub(crate) struct Reader<R> {
    source: R,
    pub len: u64,
    pub bytes_read: u64,
}

impl<R: Read + Seek> Reader<R> {
    pub fn new(mut source: R) -> Result<Self, InspectError> {
        let len = source.seek(SeekFrom::End(0))?;
        Ok(Self {
            source,
            len,
            bytes_read: 0,
        })
    }

    pub fn array<const N: usize>(
        &mut self,
        offset: u64,
        truncated_code: &'static str,
    ) -> Result<[u8; N], InspectError> {
        let end = offset.checked_add(N as u64).ok_or_else(|| {
            InspectError::format(
                ErrorKind::Invalid,
                "record.range_overflow",
                offset,
                "byte range overflows u64",
            )
        })?;
        if end > self.len {
            return Err(InspectError::format(
                ErrorKind::Invalid,
                truncated_code,
                offset,
                format!("need bytes [{offset}, {end}), file size is {}", self.len),
            ));
        }
        self.source.seek(SeekFrom::Start(offset))?;
        let mut bytes = [0; N];
        match self.source.read_exact(&mut bytes) {
            Ok(()) => {}
            Err(error) if error.kind() == std::io::ErrorKind::UnexpectedEof => {
                return Err(InspectError::format(
                    ErrorKind::Invalid,
                    truncated_code,
                    offset,
                    "input ended during inspection (it may have changed)",
                ));
            }
            Err(error) => return Err(error.into()),
        }
        self.bytes_read += N as u64;
        Ok(bytes)
    }
}
