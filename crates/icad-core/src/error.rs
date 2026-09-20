use std::fmt;
use std::io;

/// Stable classification of inspection failures (I/O remains an OS error).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ErrorKind {
    Invalid,
    Unsupported,
    LimitExceeded,
}

impl ErrorKind {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Invalid => "invalid",
            Self::Unsupported => "unsupported",
            Self::LimitExceeded => "limit_exceeded",
        }
    }
}

/// A failure in original-file byte coordinates.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Diagnostic {
    pub kind: ErrorKind,
    pub code: &'static str,
    pub byte_offset: u64,
    pub message: String,
}

#[derive(Debug)]
pub enum InspectError {
    Format(Diagnostic),
    Io(io::Error),
}

impl InspectError {
    pub(crate) fn format(
        kind: ErrorKind,
        code: &'static str,
        byte_offset: u64,
        message: impl Into<String>,
    ) -> Self {
        Self::Format(Diagnostic {
            kind,
            code,
            byte_offset,
            message: message.into(),
        })
    }
}

impl fmt::Display for InspectError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Format(diagnostic) => write!(
                formatter,
                "{} at byte {}: {}",
                diagnostic.code, diagnostic.byte_offset, diagnostic.message
            ),
            Self::Io(error) => error.fmt(formatter),
        }
    }
}

impl std::error::Error for InspectError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Io(error) => Some(error),
            Self::Format(_) => None,
        }
    }
}

impl From<io::Error> for InspectError {
    fn from(error: io::Error) -> Self {
        Self::Io(error)
    }
}
