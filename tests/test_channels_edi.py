"""EDI X12: 850 generation, parsing of partner documents, file-drop transport through the hub, API."""
from datetime import datetime, timezone

import pytest
from cryptography.fernet import Fernet

from app.channels import connector_types
from app.channels.edi import EdiError, Partner, build_850, parse, split
from app.channels.hub import ChannelHub
from app.channels.store import ChannelStore
from app.channels.vault import Vault
from tests.test_api import auth, env  # noqa: F401  (fixture reuse)

NOW = datetime(2026, 11, 27, 9, 30, tzinfo=timezone.utc)
PARTNER = Partner("ACMEBRAND", "SUPPLIER01")
PO = {"po_number": "PO-1001", "currency": "EUR",
      "ship_to": {"name": "Acme DC", "address": "Hafenstr. 1", "city": "Hamburg", "postal_code": "20457", "country": "DE"},
      "lines": [{"sku": "MAT-BLUE", "quantity": 500, "unit_price": 4.2, "supplier_sku": "S-77"},
                {"sku": "MUG", "quantity": 120}]}


def envelope(body_segments, kind="SH", control="000000007"):
    isa = ("ISA*00*          *00*          *ZZ*SUPPLIER01     *ZZ*ACMEBRAND      *261127*0930*U*00401*"
           f"{control}*0*P*>")
    segs = [isa, f"GS*{kind}*SUPPLIER01*ACMEBRAND*20261127*0930*7*X*004010", *body_segments, "GE*1*7",
            f"IEA*1*{control}"]
    return "~".join(segs) + "~\n"


def transaction(code, segs):
    full = [f"ST*{code}*0001", *segs]
    return [*full, f"SE*{len(full) + 1}*0001"]


# ---------------------------------------------------------------- outbound 850
def test_build_850_envelope_and_lines():
    text = build_850(PO, PARTNER, control=42, now=NOW)
    assert len(text.split("~")[0]) + 1 == 106  # ISA is fixed width, terminator included
    segments, element = split(text)
    tags = [s[0] for s in segments]
    assert element == "*" and tags[:3] == ["ISA", "GS", "ST"] and tags[-2:] == ["GE", "IEA"]
    isa = segments[0]
    assert isa[6] == "ACMEBRAND      " and isa[8] == "SUPPLIER01     " and isa[13] == "000000042" and isa[15] == "P"
    assert segments[2] == ["ST", "850", "0001"]
    assert ["BEG", "00", "SA", "PO-1001", "", "20261127"] in segments
    po1 = [s for s in segments if s[0] == "PO1"]
    assert po1[0] == ["PO1", "1", "500", "EA", "4.20", "", "BP", "MAT-BLUE", "VP", "S-77"]
    assert po1[1] == ["PO1", "2", "120", "EA", "", "", "BP", "MUG"]
    assert ["CTT", "2"] in segments
    se = next(s for s in segments if s[0] == "SE")
    st_index = tags.index("ST")
    assert int(se[1]) == tags.index("SE") - st_index + 1  # SE counts ST..SE
    assert parse(text)[0]["transaction"] == "850"  # round-trips through the validator


def test_build_850_rejects_bad_input():
    with pytest.raises(EdiError, match="po_number"):
        build_850({"lines": [{"sku": "A", "quantity": 1}]}, PARTNER, 1)
    with pytest.raises(EdiError, match="at least one line"):
        build_850({"po_number": "X", "lines": []}, PARTNER, 1)
    with pytest.raises(EdiError, match="positive"):
        build_850({"po_number": "X", "lines": [{"sku": "A", "quantity": 0}]}, PARTNER, 1)
    with pytest.raises(EdiError, match="15 characters"):
        Partner("THIS-ID-IS-WAY-TOO-LONG", "B")
    tricky = build_850({"po_number": "A*B~C", "lines": [{"sku": "X>Y", "quantity": 1}]}, PARTNER, 1)
    assert "A B C" in tricky and "X Y" in tricky  # separators in data cannot break the structure


# ---------------------------------------------------------------- inbound
def test_parse_partner_documents():
    asn = transaction("856", ["BSN*00*SHIP-9*20261127*0930", "TD5**2*DHL", "REF*BM*TRACK123", "PRF*PO-1001",
                              "LIN**BP*MAT-BLUE", "SN1**480*EA", "LIN**VP*S-88*BP*MUG", "SN1**120*EA"])
    inv = transaction("810", ["BIG*20261128*INV-55**PO-1001", "IT1*1*480*EA*4.20**BP*MAT-BLUE", "TDS*201600"])
    ack = transaction("855", ["BAK*00*AC*PO-1001*20261127", "PO1*1*480*EA*4.20**BP*MAT-BLUE"])
    fa = transaction("997", ["AK1*PO*42", "AK9*A*1*1*1"])
    docs = parse(envelope(asn + inv + ack + fa))
    assert [d["event"] for d in docs] == ["edi_ship_notice", "edi_invoice", "edi_po_ack", "edi_functional_ack"]
    shipment, invoice, po_ack, functional = docs
    assert shipment["po_number"] == "PO-1001" and shipment["carrier"] == "DHL" and shipment["tracking"] == "TRACK123"
    assert shipment["lines"] == [{"sku": "MAT-BLUE", "quantity": 480.0}, {"sku": "MUG", "quantity": 120.0}]
    assert invoice["total"] == 2016.0 and invoice["lines"][0] == {"sku": "MAT-BLUE", "quantity": 480.0, "unit_price": 4.2}
    assert po_ack["status"] == "accepted_with_changes" and po_ack["lines"][0]["quantity"] == 480.0
    assert functional == {"transaction": "997", "event": "edi_functional_ack", "lines": [], "group_control": "42",
                          "status": "accepted"}


def test_parse_validates_structure():
    good = transaction("856", ["BSN*00*S*20261127*0930"])
    with pytest.raises(EdiError, match="SE says"):
        parse(envelope(good[:-1] + ["SE*9*0001"]))
    with pytest.raises(EdiError, match="ISA"):
        parse("ST*856*0001~SE*2*0001~")
    with pytest.raises(EdiError, match="IEA"):
        parse(envelope(good).replace("IEA*1*000000007~", ""))


# ---------------------------------------------------------------- file drop through the hub
def test_edi_partner_file_drop(tmp_path):
    hub = ChannelHub(ChannelStore(tmp_path / "channels" / "c.db"), Vault(Fernet.generate_key()), connector_types())
    conn = hub.add("acme", "edi", "Supplier 01", {"sender_id": "ACMEBRAND", "receiver_id": "SUPPLIER01"})
    assert hub.test("acme", conn["id"])["ok"] is True
    first = hub.send_purchase_order("acme", conn["id"], PO)
    second = hub.send_purchase_order("acme", conn["id"], {**PO, "po_number": "PO-1002"})
    assert (first["control_number"], second["control_number"]) == (1, 2)
    root = tmp_path / "channels" / "data" / "acme" / conn["id"]
    assert sorted(p.name for p in (root / "outbox").iterdir()) == [first["file"], second["file"]]

    (root / "inbox" / "asn.x12").write_text(envelope(transaction("856", ["BSN*00*SHIP-9*20261127*0930", "PRF*PO-1001",
                                                                          "LIN**BP*MAT-BLUE", "SN1**480*EA"])))
    (root / "inbox" / "broken.x12").write_text("not edi at all")
    summary = hub.sync("acme", conn["id"])
    assert summary["documents"] == 2 and summary["events"] == 2
    types = [e["type"] for e in hub.store.events("acme")]
    assert "edi_ship_notice" in types and "edi_error" in types and types.count("edi_po_sent") == 2
    assert sorted(p.name for p in (root / "processed").iterdir()) == ["asn.x12", "broken.x12.error"]
    assert list((root / "inbox").iterdir()) == []
    assert hub.sync("acme", conn["id"])["documents"] == 0  # processed files are not read twice


def test_purchase_order_api(env):  # noqa: F811
    client, _, _ = env
    admin, planner = auth(client, "root"), auth(client)
    conn = client.post("/api/v1/channels/connections", headers=admin, json={
        "type": "edi", "name": "Supplier 01", "config": {"sender_id": "ACMEBRAND", "receiver_id": "SUPPLIER01"}}).json()
    sent = client.post(f"/api/v1/channels/connections/{conn['id']}/purchase-orders", headers=planner, json=PO)
    assert sent.status_code == 201 and sent.json()["control_number"] == 1
    assert client.post(f"/api/v1/channels/connections/{conn['id']}/purchase-orders", headers=planner,
                       json={"po_number": "X", "lines": []}).status_code == 422
