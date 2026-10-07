//! Byte-exact model of the directory-indexed container.
//!
//! A [`Container`] keeps the MOD record, the DRW word at `+8`, the saved view
//! names, every indexed record body and the trailing container verbatim. The
//! framing that depends on them (directory offsets and lengths, `RES`/`V/W`
//! length words, the MOD total-word field) is recomputed on serialization, so
//! an unmodified document reproduces its input byte for byte exactly when its
//! framing follows the observed rules. Nothing is searched for or repaired.

use crate::{ByteOrder, Diagnostic, Document, ErrorKind, InspectError};

/// Offset of the MOD word that counts the indexed records in 32-bit words.
const TOTAL_WORDS_AT: usize = 236;

/// One directory-indexed record without its opening frame and end tag.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ContainerRecord {
    /// `RES`, `V/W` or `USR`.
    pub tag: &'static str,
    /// The record's own length word at `+4`. `RES` and `V/W` write their
    /// physical size instead; `USR` writes this word back because old files
    /// store a value that is not the physical size.
    pub declared_words: u32,
    /// Bytes from `+8` (the word shared by every record of one save) up to
    /// the end tag, exclusive. The length is a multiple of four.
    pub body: Vec<u8>,
}

impl ContainerRecord {
    /// Physical size in 32-bit words, including the 12 framing bytes.
    ///
    /// # Errors
    /// Returns `container.alignment` for a body that is not word aligned and
    /// `container.size` for a record that does not fit a 32-bit word count.
    pub fn words(&self) -> Result<u32, InspectError> {
        if !self.body.len().is_multiple_of(4) {
            return Err(InspectError::format(
                ErrorKind::Invalid,
                "container.alignment",
                0,
                format!(
                    "{} body of {} bytes is not word aligned",
                    self.tag,
                    self.body.len()
                ),
            ));
        }
        u32::try_from(self.body.len() / 4 + 3).map_err(|_| {
            InspectError::format(
                ErrorKind::LimitExceeded,
                "container.size",
                0,
                format!("{} record exceeds the 32-bit word count", self.tag),
            )
        })
    }
}

/// The indexed container of one document; see the module documentation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Container {
    pub byte_order: ByteOrder,
    /// The complete 256-byte MOD record. Bytes 236..240 are replaced by the
    /// recomputed total on serialization; the other fields are copied.
    pub mod_record: [u8; 256],
    /// The DRW word at `+8`, retained verbatim.
    pub drw_word: [u8; 4],
    /// Eight-byte saved view names in directory order, retained verbatim.
    pub view_names: Vec<[u8; 8]>,
    /// `RES`, then one `V/W` per view name, then an optional `USR`.
    pub records: Vec<ContainerRecord>,
    /// Everything after the last indexed record, retained verbatim.
    pub tail: Vec<u8>,
    /// Where the source deviated from the recomputed framing.
    pub diagnostics: Vec<Diagnostic>,
}

fn invalid(code: &'static str, message: impl Into<String>) -> InspectError {
    InspectError::format(ErrorKind::Invalid, code, 0, message)
}

impl Container {
    /// Check the record order, the view-name count and the record sizes.
    fn validate(&self) -> Result<(), InspectError> {
        let mut views = 0;
        for (index, record) in self.records.iter().enumerate() {
            let expected_tag = match (index, record.tag) {
                (0, _) => "RES",
                (_, "USR") if index + 1 == self.records.len() => "USR",
                _ => "V/W",
            };
            if record.tag != expected_tag {
                return Err(invalid(
                    "container.record_order",
                    format!("record {index} is {}, expected {expected_tag}", record.tag),
                ));
            }
            if record.tag == "V/W" {
                views += 1;
            }
            record.words()?;
        }
        if self.records.first().map(|r| r.tag) != Some("RES") {
            return Err(invalid(
                "container.record_order",
                "the first record must be RES",
            ));
        }
        if views != self.view_names.len() {
            return Err(invalid(
                "container.view_names",
                format!(
                    "{views} V/W records but {} view names",
                    self.view_names.len()
                ),
            ));
        }
        Ok(())
    }

    /// Byte length of the DRW record for the current number of views.
    fn drw_bytes(&self) -> usize {
        32 + 16 * self.view_names.len()
    }

    /// The MOD total: DRW plus every indexed record, in 32-bit words.
    ///
    /// # Errors
    /// Returns the validation errors of [`Container::to_bytes`].
    pub fn total_words(&self) -> Result<u32, InspectError> {
        self.validate()?;
        let mut total = u32::try_from(self.drw_bytes() / 4)
            .map_err(|_| invalid("container.size", "too many views"))?;
        for record in &self.records {
            total = total
                .checked_add(record.words()?)
                .ok_or_else(|| invalid("container.size", "indexed records exceed 2^32 words"))?;
        }
        Ok(total)
    }

    /// Serialize with recomputed framing; see the module documentation.
    ///
    /// # Errors
    /// Returns `container.record_order` for records out of order,
    /// `container.view_names` when the view names do not match the `V/W`
    /// records, `container.alignment` for an unaligned body and
    /// `container.size` when a count does not fit its field.
    pub fn to_bytes(&self) -> Result<Vec<u8>, InspectError> {
        let total = self.total_words()?;
        let order = self.byte_order;
        let entries = self.view_names.len() + 2;
        let mut out = Vec::with_capacity(256 + total as usize * 4 + self.tail.len());
        let mut mod_record = self.mod_record;
        mod_record[TOTAL_WORDS_AT..TOTAL_WORDS_AT + 4].copy_from_slice(&order.bytes(total));
        out.extend_from_slice(&mod_record);
        out.extend_from_slice(b"DRW0");
        let drw_words = u32::try_from(self.drw_bytes() / 4)
            .map_err(|_| invalid("container.size", "too many views"))?;
        out.extend_from_slice(&order.bytes(drw_words));
        out.extend_from_slice(&self.drw_word);
        let mut starts = Vec::with_capacity(entries);
        let mut lengths = Vec::with_capacity(entries);
        let mut cursor = u32::try_from(256 + self.drw_bytes())
            .map_err(|_| invalid("container.size", "too many views"))?;
        for record in &self.records {
            let words = record.words()?;
            // Directory starts are byte offsets; lengths are 32-bit words.
            starts.push(cursor);
            lengths.push(words);
            cursor = words
                .checked_mul(4)
                .and_then(|bytes| cursor.checked_add(bytes))
                .ok_or_else(|| invalid("container.size", "indexed records exceed 4 GiB"))?;
        }
        // A missing USR is a zero entry; the directory still reserves it.
        starts.resize(entries, 0);
        lengths.resize(entries, 0);
        for value in starts.iter().chain(&lengths) {
            out.extend_from_slice(&order.bytes(*value));
        }
        for name in &self.view_names {
            out.extend_from_slice(name);
        }
        out.extend_from_slice(b"DRW1");
        for record in &self.records {
            out.extend_from_slice(record.tag.as_bytes());
            out.push(b'0');
            let length = if record.tag == "USR" {
                record.declared_words
            } else {
                record.words()?
            };
            out.extend_from_slice(&order.bytes(length));
            out.extend_from_slice(&record.body);
            out.extend_from_slice(record.tag.as_bytes());
            out.push(b'1');
        }
        out.extend_from_slice(&self.tail);
        Ok(out)
    }
}

impl Document {
    /// Model the indexed container for byte-exact serialization.
    ///
    /// The directory, record ranges and tags were validated when the
    /// document was indexed. A stored MOD total that differs from the
    /// recomputed one is reported as `container.total_words` and is not
    /// copied; the recomputed value is written instead.
    ///
    /// # Errors
    /// Returns `container.range` when an indexed range does not lie inside
    /// the input and `container.view_names` when the directory's view names
    /// do not match its `V/W` records.
    pub fn container(&self) -> Result<Container, InspectError> {
        let data = self.all_bytes();
        let order = self.inspection().header.byte_order;
        let slice = |start: usize, end: usize| {
            data.get(start..end).ok_or_else(|| {
                InspectError::format(
                    ErrorKind::Invalid,
                    "container.range",
                    start as u64,
                    format!("indexed range [{start}, {end}) exceeds the input"),
                )
            })
        };
        let indexed = self.records();
        let drw = indexed
            .get(1)
            .filter(|record| record.tag == "DRW")
            .ok_or_else(|| invalid("container.range", "the DRW record is not indexed"))?;
        let drw_len = (drw.byte_range.end - drw.byte_range.start) as usize;
        let views = drw_len.saturating_sub(32) / 16;
        let names_at = 268 + 8 * (views + 2);
        let mut view_names = Vec::with_capacity(views);
        for index in 0..views {
            let mut name = [0; 8];
            name.copy_from_slice(slice(names_at + 8 * index, names_at + 8 * index + 8)?);
            view_names.push(name);
        }
        let mut records = Vec::with_capacity(indexed.len().saturating_sub(2));
        let mut last_end = drw.byte_range.end as usize;
        for record in indexed.iter().skip(2) {
            let start = record.byte_range.start as usize;
            let end = record.byte_range.end as usize;
            let length = slice(start + 4, start + 8)?;
            records.push(ContainerRecord {
                tag: record.tag,
                declared_words: order.u32([length[0], length[1], length[2], length[3]]),
                body: slice(start + 8, end - 4)?.to_vec(),
            });
            last_end = end;
        }
        let mut drw_word = [0; 4];
        drw_word.copy_from_slice(slice(264, 268)?);
        let mut container = Container::new(
            order,
            self.inspection().header.raw_mod,
            drw_word,
            view_names,
            records,
            slice(last_end, data.len())?.to_vec(),
        )?;
        let stored = order.u32([
            container.mod_record[TOTAL_WORDS_AT],
            container.mod_record[TOTAL_WORDS_AT + 1],
            container.mod_record[TOTAL_WORDS_AT + 2],
            container.mod_record[TOTAL_WORDS_AT + 3],
        ]);
        let total = container.total_words()?;
        if stored != total {
            container.diagnostics.push(Diagnostic {
                kind: ErrorKind::Unsupported,
                code: "container.total_words",
                byte_offset: TOTAL_WORDS_AT as u64,
                message: format!("MOD stores {stored} words but the indexed records span {total}"),
            });
        }
        Ok(container)
    }
}

impl Container {
    /// Assemble a container from its retained parts and validate the record
    /// order, the view-name count and the record alignment.
    ///
    /// # Errors
    /// Returns the validation errors of [`Container::to_bytes`].
    pub fn new(
        byte_order: ByteOrder,
        mod_record: [u8; 256],
        drw_word: [u8; 4],
        view_names: Vec<[u8; 8]>,
        records: Vec<ContainerRecord>,
        tail: Vec<u8>,
    ) -> Result<Self, InspectError> {
        let container = Self {
            byte_order,
            mod_record,
            drw_word,
            view_names,
            records,
            tail,
            diagnostics: Vec::new(),
        };
        container.validate()?;
        Ok(container)
    }
}
