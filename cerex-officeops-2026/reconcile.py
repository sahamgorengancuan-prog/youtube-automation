#!/usr/bin/env python3
"""CereX OfficeOps: strictly fixture-only purchase order reconciliation.
Three verified stages for independent agents. No external writes or real account access.
"""
from __future__ import annotations
import argparse,csv,hashlib,io,json,os
from collections import Counter
from decimal import Decimal,InvalidOperation
from pathlib import Path

ROOT=Path(__file__).resolve().parent
FIXTURES=ROOT/"fixtures"
ORDER_HEADERS=["po","sku","quantity","unit_price"]
INVOICE_HEADERS=["invoice","po","sku","quantity","unit_price"]

def dec(x: str) -> Decimal:
    try:
        d=Decimal(str(x))
        if not d.is_finite() or d < 0:raise ValueError("negative/nonfinite amount")
        return d
    except (InvalidOperation,ValueError) as e:
        raise ValueError("Invalid positive decimal quantity or price") from e

def load_csv(name,headers):
    f=FIXTURES/name
    with f.open(encoding="utf-8",newline="") as stream:
        reader=csv.DictReader(stream)
        if reader.fieldnames!=headers:raise ValueError("invalid CSV columns")
        rows=list(reader)
    for r in rows:
        if any(not str(r[k]).strip() for k in headers):raise ValueError("empty field")
        if int(r["quantity"])<=0 or int(r["quantity"])>100000:raise ValueError("invalid quantity")
        dec(r["unit_price"])
    return rows

def digest(p:Path)->str:return hashlib.sha256(p.read_bytes()).hexdigest()
def atomic_json(p:Path,obj:dict):
    p.parent.mkdir(parents=True,exist_ok=True)
    temp=p.with_name(p.name+".tmp")
    temp.write_text(json.dumps(obj,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    temp.replace(p)

def validate(shared:Path):
    orders=load_csv("orders.csv",ORDER_HEADERS)
    invoices=load_csv("invoices.csv",INVOICE_HEADERS)
    if len({o["po"] for o in orders})!=len(orders):raise ValueError("duplicate order IDs")
    result={"status":"VALIDATED","orders":orders,"invoices":invoices,
       "source_sha256":{f:digest(FIXTURES/f) for f in ("orders.csv","invoices.csv")}}
    atomic_json(shared/"validated.json",result)
    return {"status":"VALIDATED","orders":len(orders),"invoices":len(invoices)}

def reconcile(shared:Path):
    original=json.loads((shared/"validated.json").read_text(encoding="utf-8"))
    if original["status"]!="VALIDATED":raise ValueError("missing validation")
    for filename,expected in original["source_sha256"].items():
        if digest(FIXTURES/filename)!=expected:raise ValueError("fixture changed after validation")
    orders={o["po"]:o for o in original["orders"]}
    counts=Counter(i["po"] for i in original["invoices"])
    findings=[]
    for i in original["invoices"]:
        po=i["po"];order=orders.get(po)
        if not order: code="MISSING_PURCHASE_ORDER"
        elif counts[po]>1: code="DUPLICATE_INVOICE_PO"
        elif order["sku"]!=i["sku"]: code="SKU_MISMATCH"
        elif int(order["quantity"])!=int(i["quantity"]):code="QUANTITY_MISMATCH"
        elif dec(order["unit_price"])!=dec(i["unit_price"]):code="PRICE_MISMATCH"
        else:code="MATCHED"
        expected_total=dec(order["unit_price"])*int(order["quantity"]) if order else None
        invoiced_total=dec(i["unit_price"])*int(i["quantity"])
        findings.append({"po":po,"invoice":i["invoice"],"issue":code,
            "expected_total":str(expected_total) if expected_total is not None else "",
            "invoiced_total":str(invoiced_total)})
    for po in sorted(set(orders)-set(counts)):
        o=orders[po]
        findings.append({"po":po,"invoice":"","issue":"MISSING_INVOICE",
            "expected_total":str(dec(o["unit_price"])*int(o["quantity"])),"invoiced_total":""})
    findings.sort(key=lambda x:(x["po"],x["invoice"]))
    with (shared/"findings.csv").open("w",encoding="utf-8",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=["po","invoice","issue","expected_total","invoiced_total"])
        writer.writeheader();writer.writerows(findings)
    counts=Counter(f["issue"] for f in findings)
    result={"status":"RECONCILED","total_findings":len(findings),
            "matched_rows":counts.pop("MATCHED",0),
            "exceptions":dict(sorted(counts.items())),
            "sha256_findings":digest(shared/"findings.csv")}
    atomic_json(shared/"summary.json",result)
    return result

def audit(shared:Path):
    summary=json.loads((shared/"summary.json").read_text(encoding="utf-8"))
    if summary["status"]!="RECONCILED":raise ValueError("not reconciled")
    if digest(shared/"findings.csv")!=summary["sha256_findings"]:
        raise ValueError("audit mismatch, evidence modified")
    with (shared/"findings.csv").open(encoding="utf8",newline="") as f:
        rows=list(csv.DictReader(f))
    if len(rows)!=summary["total_findings"]:raise ValueError("missing findings")
    if summary["matched_rows"]+sum(summary["exceptions"].values())!=len(rows):
        raise ValueError("inconsistent totals")
    # This human approval gate cannot modify an external PO, invoice or approval status.
    verdict={"status":"REVIEW_REQUIRED","evidence_valid":True,
             "exception_rows":sum(summary["exceptions"].values()),
             "approval_status":"NOT_APPROVED","external_system_changes":0,
             "reason":"Owner must approve any external write; current task generated only local evidence.",
             "evidence_sha256":digest(shared/"findings.csv")}
    atomic_json(shared/"audit.json",verdict)
    return verdict

def main():
    p=argparse.ArgumentParser();p.add_argument("--stage",choices=["validate","reconcile","audit","run"],required=True)
    p.add_argument("--shared",default=None)
    a=p.parse_args()
    shared=Path(a.shared or os.environ.get("CEREX_SHARED") or ROOT/"output").resolve()
    if not str(shared).startswith(str(ROOT.resolve())):
        raise SystemExit("Fixture-only demo: output must remain inside isolated competition workspace")
    shared.mkdir(parents=True,exist_ok=True)
    stages={"validate":validate,"reconcile":reconcile,"audit":audit}
    if a.stage=="run":
        for stage in stages: result=stages[stage](shared)
    else:result=stages[a.stage](shared)
    print(json.dumps(result,ensure_ascii=False,sort_keys=True))
if __name__=="__main__":main()
