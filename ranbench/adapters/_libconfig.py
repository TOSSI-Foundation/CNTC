"""A deliberately small reader for the scalar settings in an OAI ``.conf``.

OAI configures itself with libconfig, not YAML. A full libconfig parser is not needed and would
be a liability: everything ranbench asks of an OAI config is a scalar (an address, a port, a
preference) rather than a structure.

It is a *reader*, not a parser: it does not model nesting. That matters, because a key can
appear more than once under different parents and mean different things. A DU config carries
``tr_n_preference`` twice, "f1" under MACRLCs and "local_mac" under L1s, so collapsing to one
value would silently answer the wrong question. Every key is therefore returned with **all** its
occurrences in file order, and the caller says which one it means.
"""
from __future__ import annotations

import re
from pathlib import Path

_COMMENT = re.compile(r"(^|\s)(#|//).*$", re.M)
# Deliberately not anchored to the line start: OAI writes single-element groups inline, as in
# `amf_ip_address = ({ ipv4 = "10.152.183.216"; });`, and the address is the point of the line.
_SCALAR = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)\s*=\s*("([^"]*)"|[A-Za-z0-9_.:/-]+)')


def read(path: str | Path) -> dict[str, list[str]]:
    """``{key: [value, ...]}`` for every scalar assignment, in file order.

    Comments are stripped first, so a setting that is commented out is genuinely absent rather
    than silently picked up: an OAI config carries many commented alternatives, and reading one
    as live would point ranbench at an address nothing is listening on.
    """
    try:
        text = Path(path).read_text(errors="ignore")
    except OSError:
        return {}
    text = _COMMENT.sub("", text)
    out: dict[str, list[str]] = {}
    for m in _SCALAR.finditer(text):
        key, raw, quoted = m.group(1), m.group(2), m.group(3)
        out.setdefault(key, []).append(quoted if quoted is not None else raw)
    return out


def first(d: dict[str, list[str]], key: str, default: str = "") -> str:
    """The first occurrence of a key, which is the outermost one in an OAI config."""
    vals = d.get(key) or []
    return vals[0] if vals else default


def addr(value: str) -> str:
    """An address with any CIDR suffix removed.

    OAI writes its N2 and N3 bind addresses as ``"192.168.6.90/24"``, and everything downstream
    (socket checks, capture filters, the transport-protection judgement) wants the address.
    """
    return (value or "").split("/")[0].strip()
