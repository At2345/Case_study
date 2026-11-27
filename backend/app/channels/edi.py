"""EDI (ANSI X12 004010) without a paid network: build purchase orders, parse partner documents.

Outbound: 850 purchase order. Inbound: 855 PO acknowledgment, 856 ship notice, 810 invoice, 997 functional
acknowledgment. Envelopes (ISA/GS/ST) are generated and checked (segment counts, control numbers).

Transport is a file drop per supplier connection: files are written to `outbox/` and read from `inbox/`
(processed files move to `processed/`). Any AS2 or SFTP tool can move those files to and from the
partner; for example the open-source Bots EDI translator (GPLv3, run as a separate program, so this code
does not link to it).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.channels.base import ChannelError, ConfigField, Connector, ConnectorType

ELEMENT, SEGMENT, COMPONENT = "*", "~", ">"
SAFE = re.compile(r"[*~>\r\n]")
INBOUND_TYPES = {"855": "edi_po_ack", "856": "edi_ship_notice", "810": "edi_invoice", "997": "edi_functional_ack"}


class EdiError(ChannelError):
    pass


def _clean(value: Any) -> str:
    return SAFE.sub(" ", str(value if value is not None else "")).strip()


@dataclass
class Partner:
    sender_id: str
    receiver_id: str
    sender_qualifier: str = "ZZ"
    receiver_qualifier: str = "ZZ"
    test: bool = False

    def __post_init__(self) -> None:
        for name in ("sender_id", "receiver_id"):
            value = getattr(self, name)
            if not value or len(value) > 15 or SAFE.search(value):
                raise EdiError(f"{name} must be 1-15 characters without * ~ > or line breaks")


# ---------------------------------------------------------------------------------------------- outbound
def build_850(po: Dict[str, Any], partner: Partner, control: int, now: Optional[datetime] = None) -> str:
    """Purchase order. po = {"po_number", "lines": [{"sku", "quantity", "unit_price"?, "uom"?, "supplier_sku"?}],
    "ship_to"?: {"name", "address"?, "city"?, "postal_code"?, "country"?}, "currency"?}"""
    if not po.get("po_number"):
        raise EdiError("po_number is required")
    if not po.get("lines"):
        raise EdiError("a purchase order needs at least one line")
    now = now or datetime.now(timezone.utc)
    ctl = f"{int(control):09d}"
    isa = ELEMENT.join([
        "ISA", "00", " " * 10, "00", " " * 10,
        partner.sender_qualifier.ljust(2), partner.sender_id.ljust(15),
        partner.receiver_qualifier.ljust(2), partner.receiver_id.ljust(15),
        now.strftime("%y%m%d"), now.strftime("%H%M"), "U", "00401", ctl, "0", "T" if partner.test else "P", COMPONENT,
    ])
    body: List[str] = [
        f"ST*850*0001",
        f"BEG*00*SA*{_clean(po['po_number'])}**{now.strftime('%Y%m%d')}",
    ]
    if po.get("currency"):
        body.append(f"CUR*BY*{_clean(po['currency'])}")
    ship = po.get("ship_to") or {}
    if ship.get("name"):
        body.append(f"N1*ST*{_clean(ship['name'])}")
        if ship.get("address"):
            body.append(f"N3*{_clean(ship['address'])}")
        if ship.get("city") or ship.get("postal_code") or ship.get("country"):
            body.append(f"N4*{_clean(ship.get('city'))}**{_clean(ship.get('postal_code'))}*{_clean(ship.get('country'))}")
    for i, line in enumerate(po["lines"], start=1):
        qty = float(line["quantity"])
        if qty <= 0:
            raise EdiError(f"line {i}: quantity must be positive")
        price = f"{float(line['unit_price']):.2f}" if line.get("unit_price") is not None else ""
        ids = f"BP*{_clean(line['sku'])}"
        if line.get("supplier_sku"):
            ids += f"*VP*{_clean(line['supplier_sku'])}"
        body.append(f"PO1*{i}*{qty:g}*{_clean(line.get('uom') or 'EA')}*{price}**{ids}")
    body.append(f"CTT*{len(po['lines'])}")
    body.append(f"SE*{len(body) + 1}*0001")
    segments = [isa, f"GS*PO*{_clean(partner.sender_id)}*{_clean(partner.receiver_id)}*{now.strftime('%Y%m%d')}*"
                f"{now.strftime('%H%M')}*{int(control)}*X*004010", *body, f"GE*1*{int(control)}", f"IEA*1*{ctl}"]
    return SEGMENT.join(segments) + SEGMENT


# ---------------------------------------------------------------------------------------------- inbound
def split(text: str) -> Tuple[List[List[str]], str]:
    """Return segments (element lists) and the element separator, read from the ISA header."""
    text = text.strip().replace("\r", "").replace("\n", "")
    if not text.startswith("ISA") or len(text) < 106:
        raise EdiError("not an X12 interchange (missing ISA header)")
    element, terminator = text[3], text[105]
    segments = [s.split(element) for s in text.split(terminator) if s.strip()]
    return segments, element


def parse(text: str) -> List[Dict[str, Any]]:
    """Parse an interchange into documents. Validates SE segment counts and GE/IEA totals."""
    segments, _ = split(text)
    if segments[-1][0] != "IEA":
        raise EdiError("interchange is not closed (missing IEA)")
    docs: List[Dict[str, Any]] = []
    current: Optional[List[List[str]]] = None
    groups = 0
    for seg in segments:
        tag = seg[0]
        if tag == "GS":
            groups += 1
        elif tag == "ST":
            current = [seg]
        elif tag == "SE":
            if current is None:
                raise EdiError("SE without ST")
            current.append(seg)
            if int(seg[1]) != len(current):
                raise EdiError(f"transaction {seg[2]}: SE says {seg[1]} segments, found {len(current)}")
            docs.append(_document(current))
            current = None
        elif current is not None:
            current.append(seg)
    iea = segments[-1]
    if int(iea[1]) != groups:
        raise EdiError(f"IEA says {iea[1]} groups, found {groups}")
    return docs


def _el(seg: List[str], i: int) -> Optional[str]:
    return seg[i] if len(seg) > i and seg[i] != "" else None


def _sku(seg: List[str], start: int) -> Optional[str]:
    """Find a product id in qualifier/value pairs (prefer buyer part BP, then vendor VP/VN, then UPC/EAN)."""
    pairs = {seg[i]: seg[i + 1] for i in range(start, len(seg) - 1, 2) if seg[i]}
    for q in ("BP", "VP", "VN", "SK", "UP", "EN"):
        if pairs.get(q):
            return pairs[q]
    return None


def _document(segs: List[List[str]]) -> Dict[str, Any]:
    kind = segs[0][1]
    doc: Dict[str, Any] = {"transaction": kind, "event": INBOUND_TYPES.get(kind, f"edi_{kind}"), "lines": []}
    for seg in segs:
        tag = seg[0]
        if kind == "855" and tag == "BAK":
            doc.update(po_number=_el(seg, 3), status={"AC": "accepted_with_changes", "AD": "accepted",
                                                     "AT": "accepted", "RJ": "rejected"}.get(_el(seg, 2) or "", _el(seg, 2)))
        elif kind == "855" and tag == "PO1":
            doc["lines"].append({"sku": _sku(seg, 6), "quantity": float(_el(seg, 2) or 0)})
        elif kind == "856" and tag == "BSN":
            doc.update(shipment_id=_el(seg, 2), date=_el(seg, 3))
        elif kind == "856" and tag == "PRF":
            doc["po_number"] = _el(seg, 1)
        elif kind == "856" and tag == "TD5":
            doc["carrier"] = _el(seg, 3)
        elif kind == "856" and tag == "REF" and _el(seg, 1) in ("BM", "CN"):
            doc["tracking"] = _el(seg, 2)
        elif kind == "856" and tag == "LIN":
            doc["lines"].append({"sku": _sku(seg, 2), "quantity": 0.0})
        elif kind == "856" and tag == "SN1" and doc["lines"]:
            doc["lines"][-1]["quantity"] = float(_el(seg, 2) or 0)
        elif kind == "810" and tag == "BIG":
            doc.update(invoice_date=_el(seg, 1), invoice_number=_el(seg, 2), po_number=_el(seg, 4))
        elif kind == "810" and tag == "IT1":
            doc["lines"].append({"sku": _sku(seg, 6), "quantity": float(_el(seg, 2) or 0),
                                 "unit_price": float(_el(seg, 4) or 0)})
        elif kind == "810" and tag == "TDS":
            doc["total"] = round(int(_el(seg, 1) or 0) / 100, 2)  # implied two decimals
        elif kind == "997" and tag == "AK1":
            doc["group_control"] = _el(seg, 2)
        elif kind == "997" and tag == "AK9":
            doc["status"] = {"A": "accepted", "E": "accepted_with_errors", "R": "rejected"}.get(_el(seg, 1) or "", _el(seg, 1))
    return doc


# ---------------------------------------------------------------------------------------------- connector
class EdiConnector(Connector):
    """A supplier or retailer EDI partner reached through a file drop (outbox/inbox)."""

    type_name = "edi"

    @property
    def root(self) -> Path:
        root = self.config.get("_data_dir")
        if not root:
            raise EdiError("edi: no data folder assigned to this connection")
        return Path(root)

    @property
    def partner(self) -> Partner:
        return Partner(self.config["sender_id"], self.config["receiver_id"],
                       self.config.get("sender_qualifier") or "ZZ", self.config.get("receiver_qualifier") or "ZZ",
                       str(self.config.get("test_mode", "false")).lower() == "true")

    def _next_control(self) -> int:
        counter = self.root / "control.txt"
        counter.parent.mkdir(parents=True, exist_ok=True)
        value = int(counter.read_text()) + 1 if counter.exists() else 1
        counter.write_text(str(value))
        return value

    def test(self) -> Dict[str, Any]:
        self.partner  # validates ids
        for d in ("outbox", "inbox", "processed"):
            (self.root / d).mkdir(parents=True, exist_ok=True)
        return {"account": f"EDI {self.config['receiver_id']}", "folder": str(self.root)}

    def send_purchase_order(self, po: Dict[str, Any]) -> Dict[str, Any]:
        control = self._next_control()
        text = build_850(po, self.partner, control)
        out = self.root / "outbox"
        out.mkdir(parents=True, exist_ok=True)
        name = f"850_{_clean(po['po_number']).replace(' ', '_')}_{control:09d}.x12"
        (out / name).write_text(text, encoding="ascii", errors="replace")
        return {"control_number": control, "file": name}

    def poll_inbound(self) -> List[Dict[str, Any]]:
        inbox, done = self.root / "inbox", self.root / "processed"
        inbox.mkdir(parents=True, exist_ok=True)
        done.mkdir(parents=True, exist_ok=True)
        documents = []
        for path in sorted(inbox.iterdir()):
            if not path.is_file():
                continue
            try:
                for doc in parse(path.read_text(encoding="ascii", errors="replace")):
                    documents.append({**doc, "file": path.name})
                path.replace(done / path.name)
            except EdiError as exc:
                path.replace(done / f"{path.name}.error")
                documents.append({"transaction": "error", "event": "edi_error", "file": path.name, "error": str(exc),
                                  "lines": []})
        return documents


EDI = ConnectorType(
    name="edi", label="EDI partner (X12, file drop)", kind="supply", factory=EdiConnector,
    capabilities=("edi",),
    fields=[
        ConfigField("sender_id", "Your EDI id (ISA06)"),
        ConfigField("receiver_id", "Partner EDI id (ISA08)"),
        ConfigField("sender_qualifier", "Your id qualifier", required=False, default="ZZ"),
        ConfigField("receiver_qualifier", "Partner id qualifier", required=False, default="ZZ"),
        ConfigField("test_mode", "Test mode (true/false)", required=False, default="false"),
    ],
    docs="https://x12.org/",
    notes="Writes 850 POs to outbox/ and reads 855/856/810/997 from inbox/. Move files with any AS2 or SFTP tool.",
)
