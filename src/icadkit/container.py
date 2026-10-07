"""Byte-exact container model: retained records with recomputed framing.

A :class:`Container` holds what the directory-indexed container stores
(the MOD record, the DRW word at ``+8``, the saved view names, every record
body and the tail) and derives the rest when it serializes: directory offsets
and lengths, ``RES``/``V/W`` length words and the MOD total-word field. It is a
model for byte-exact editing, not an interpreter: no record is understood,
invented or repaired here, and no serialized output has been opened by iCAD.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal, cast

from . import _core
from .models import Diagnostic

if TYPE_CHECKING:
    from .document import Document

RecordTag = Literal["RES", "V/W", "USR"]
_TAGS = ("RES", "V/W", "USR")


@dataclass(frozen=True)
class ContainerRecord:
    """One indexed record: its tag, its own length word and its body.

    ``body`` holds the bytes from ``+8`` (the word shared by every record of
    one save) up to the end tag, exclusive, so its length is a multiple of
    four. ``declared_words`` is the record's ``+4`` word: it is written back
    for ``USR`` (old files store a value that is not the physical size) and
    recomputed for ``RES`` and ``V/W``.
    """

    tag: RecordTag
    declared_words: int
    body: bytes

    def __post_init__(self) -> None:
        if self.tag not in _TAGS:
            raise ValueError(f"tag must be one of {_TAGS}, not {self.tag!r}")
        if not isinstance(self.body, bytes):
            raise TypeError("body must be bytes")
        if not isinstance(self.declared_words, int) or isinstance(
            self.declared_words, bool
        ):
            raise TypeError("declared_words must be an integer")
        if not 0 <= self.declared_words < 1 << 32:
            raise ValueError("declared_words must fit an unsigned 32-bit word")

    @property
    def words(self) -> int:
        """Physical size in 32-bit words, including the 12 framing bytes."""
        return len(self.body) // 4 + 3

    def with_body(self, body: bytes) -> ContainerRecord:
        """The same record with another body; ``USR`` keeps its length word."""
        return replace(self, body=body)


@dataclass(frozen=True)
class Container:
    """The indexed container of one document; see the module docstring.

    ``records`` are ``RES``, one ``V/W`` per saved view name and an optional
    ``USR``, in directory order: ``records[i]`` is ``Document.records[i + 2]``
    of the document it came from. ``diagnostics`` report where the source
    deviated from the recomputed framing (``container.total_words``).
    """

    byte_order: Literal["little", "big"]
    mod_record: bytes
    drw_word: bytes
    view_names: tuple[bytes, ...]
    records: tuple[ContainerRecord, ...]
    tail: bytes
    diagnostics: tuple[Diagnostic, ...] = ()

    def __post_init__(self) -> None:
        if self.byte_order not in ("little", "big"):
            raise ValueError("byte_order must be 'little' or 'big'")
        if len(self.mod_record) != 256:
            raise ValueError("mod_record must be exactly 256 bytes")
        if len(self.drw_word) != 4:
            raise ValueError("drw_word must be exactly 4 bytes")
        if any(len(name) != 8 for name in self.view_names):
            raise ValueError("each view name must be exactly 8 bytes")

    @property
    def view_records(self) -> tuple[int, ...]:
        """Indexes into ``records`` of the ``V/W`` records, in view order."""
        return tuple(i for i, r in enumerate(self.records) if r.tag == "V/W")

    def with_record(self, index: int, record: ContainerRecord) -> Container:
        """A container whose record ``index`` is replaced by ``record``."""
        records = list(self.records)
        records[index] = record
        return replace(self, records=tuple(records), diagnostics=())

    def to_bytes(self) -> bytes:
        """Serialize with recomputed framing; see :meth:`Document.to_bytes`."""
        from .document import _raise_native

        try:
            return _core.serialize_container(
                self.byte_order,
                self.mod_record,
                self.drw_word,
                list(self.view_names),
                [(r.tag, r.declared_words, r.body) for r in self.records],
                self.tail,
            )
        except _core.InspectionError as exc:
            _raise_native(exc)


def _read_container(document: Document) -> Container:
    from .document import _raise_native

    try:
        raw = document._handle.container_parts()
    except _core.InspectionError as exc:
        _raise_native(exc)
    return Container(
        raw["byte_order"],
        raw["mod_record"],
        raw["drw_word"],
        tuple(raw["view_names"]),
        tuple(
            ContainerRecord(cast(RecordTag, tag), words, body)
            for tag, words, body in raw["records"]
        ),
        raw["tail"],
        tuple(Diagnostic(**d) for d in raw["diagnostics"]),
    )
