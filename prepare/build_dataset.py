"""
Step 1 of the pipeline: turn the raw HHGOA_IEEE files into
  (a) CSVs shaped for the TigerGraph loading job  -> data/load/
  (b) a slim parquet used by the offline backend -> data/local/

Derived entities (see docs/blog.md):
  card_id      : <customer_id>-K<n>, n = rank of the card6 value within the customer
                 (empty card6 sorts first). Reproduces every card_id in closed_cases_history.csv.
  fingerprint  : card_id + addr1 + account-start day (day - D1) + purchaser email domain.
                 A finer "who is actually using this card number" identity. Confirmed-fraud and
                 cleared fingerprints essentially never overlap in the closed history.
  device_id    : hash of DeviceInfo | OS (id_30) | browser (id_31) | screen (id_33)
"""
import argparse, hashlib, os
import pandas as pd

KEEP = ["TransactionID","TransactionDT","TransactionAmt","ProductCD","card1","card2","card3","card4","card5","card6",
        "addr1","addr2","dist1","P_emaildomain","R_emaildomain","C1","C13","C14","D1","D15","M4","M6",
        "customer_id","ts","channel","risk_score"]

def s(x):
    return x.map(lambda z: "na" if pd.isna(z) else (str(int(z)) if isinstance(z, float) and float(z).is_integer() else str(z)))

def dev_id(profile: str) -> str:
    return "D" + hashlib.md5(profile.encode()).hexdigest()[:10]

def main(raw, out):
    os.makedirs(f"{out}/load", exist_ok=True); os.makedirs(f"{out}/local", exist_ok=True)
    print("reading transactions (non-V columns)…")
    tx = pd.read_csv(f"{raw}/transactions.csv", usecols=KEEP)
    idn = pd.read_csv(f"{raw}/identity.csv", usecols=["TransactionID","id_15","id_23","id_30","id_31","id_33","id_34","DeviceType","DeviceInfo"])

    k = tx.card6.fillna("")
    ranks = (pd.DataFrame({"customer_id": tx.customer_id, "k": k}).drop_duplicates().sort_values(["customer_id","k"]))
    ranks["card_id"] = ranks.customer_id + "-K" + (ranks.groupby("customer_id").cumcount()+1).astype(str)
    tx = tx.assign(k=k).merge(ranks, on=["customer_id","k"], how="left").drop(columns="k")

    day = tx.TransactionDT // 86400
    tx["fingerprint"] = "F:" + tx.card_id + "|" + s(tx.addr1) + "|" + s(day - tx.D1) + "|" + s(tx.P_emaildomain)

    f = lambda c: idn[c].map(lambda z: "?" if pd.isna(z) else str(z))
    idn["device_profile"] = f("DeviceInfo")+" | "+f("id_30")+" | "+f("id_31")+" | "+f("id_33")
    idn["device_id"] = idn.device_profile.map(dev_id)
    tx = tx.merge(idn[["TransactionID","device_id","device_profile","id_15","id_23","id_34","DeviceType"]], on="TransactionID", how="left")
    tx["ts"] = pd.to_datetime(tx.ts)
    tx["region"] = s(tx.addr1)
    tx.to_parquet(f"{out}/local/transactions.parquet", index=False)
    print("transactions:", tx.shape)

    L = f"{out}/load"
    tx[["customer_id"]].drop_duplicates().to_csv(f"{L}/customers.csv", index=False)
    tx[["card_id","customer_id","card4","card6"]].drop_duplicates("card_id").to_csv(f"{L}/cards.csv", index=False)
    txc = tx.assign(ts=tx.ts.dt.strftime("%Y-%m-%d %H:%M:%S"))
    for c in ["dist1", "C1", "C13", "D1", "risk_score", "TransactionAmt"]:   # -1 = missing (numeric fields never negative)
        txc[c] = txc[c].fillna(-1)
    cols = ["TransactionID","card_id","fingerprint","ts","TransactionAmt","ProductCD","channel","risk_score",
            "region","addr2","dist1","P_emaildomain","R_emaildomain","C1","C13","D1","M4","id_15","id_23","device_id"]
    for i, start in enumerate(range(0, len(txc), 150_000)):
        txc.iloc[start:start+150_000][cols].to_csv(f"{L}/transactions_{i}.csv", index=False)
    tx.dropna(subset=["device_id"]).drop_duplicates("device_id")[["device_id","device_profile","DeviceType"]].to_csv(f"{L}/devices.csv", index=False)
    cc = pd.read_csv(f"{raw}/closed_cases_history.csv")
    cc.assign(exposure_usd=cc.exposure_usd.fillna(0), n_txns=cc.n_txns.fillna(0).astype(int)).to_csv(f"{L}/closed_cases.csv", index=False)
    inv = cc.assign(txn=cc.txn_ids.astype(str).str.split("|")).explode("txn")[["case_id","txn"]]
    inv["txn"] = inv.txn.astype(float).astype(int)
    inv.to_csv(f"{L}/case_involves.csv", index=False)
    cc.dropna(subset=["connected_card_ids"]).assign(c=lambda x: x.connected_card_ids.str.split("|")).explode("c")[["case_id","c"]].to_csv(f"{L}/case_connected.csv", index=False)
    cc.to_parquet(f"{out}/local/closed_cases.parquet", index=False)
    pd.read_csv(f"{raw}/case_pack.csv").to_csv(f"{out}/local/case_pack.csv", index=False)
    print("done ->", out)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="data/HHGOA_IEEE")
    ap.add_argument("--out", default="data")
    a = ap.parse_args(); main(a.raw, a.out)
