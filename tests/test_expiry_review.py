import csv,io
from app.pipeline import load_sample
from app import store
from app.modules.m3_grants.rules import GRANTS
def shortened_grants():
    "G-01 提前到 2026-11-15 到期，样例访问发生在 11-16，全部失去依据。"
    return [{**g,"end":"2026-11-15T00:00:00Z"} if g["id"]=="G-01" else g for g in GRANTS]
def post_grants(client,grants):
    return client.post("/api/runs",json={"sample":"dirty.csv","parameters":{"grants":grants}})
def test_rule_change_keeps_old_and_opens_new_review(client):
    "规则变更：保留旧批次，另立新复核，旧结论不被覆盖"
    base=client.post("/api/runs",json={"sample":"dirty.csv"}).get_json()
    renewed=post_grants(client,shortened_grants()).get_json()
    assert renewed["run_id"]!=base["run_id"] and len(store.history())==2
    old=store.get(base["run_id"])
    assert old["stages"][3]["rows"]==base["stages"][3]["rows"] and old["decision"]=="待复核"
def test_retry_same_expiry_review_adds_no_duplicate(client):
    "同一到期复核重试不新增重复版本，授权列表顺序不影响批次号"
    a=post_grants(client,shortened_grants()).get_json()
    b=post_grants(client,list(reversed(shortened_grants()))).get_json()
    assert a["run_id"]==b["run_id"] and len(store.history())==1 and b["version"]==1
def test_rebase_traces_lost_basis(client):
    "新批次记录承接关系，逐访问指出谁失去了授权依据"
    base=client.post("/api/runs",json={"sample":"dirty.csv"}).get_json()
    renewed=post_grants(client,shortened_grants()).get_json()
    rebase=renewed["rebase"]
    assert rebase["supersedes"]==base["run_id"] and rebase["lost_basis"]>0
    lost=[c for c in rebase["changes"] if c["was"]["effect"]=="allow" and c["now"]["effect"]=="deny"]
    assert lost and all(c["event_id"] and c["now"]["rules"]==[] for c in lost)
def test_failed_switch_preserves_old_evidence(client):
    "切换失败：非法授权不落库，旧批次与已复核状态原样可追"
    base=client.post("/api/runs",json={"sample":"dirty.csv"}).get_json()
    client.post("/api/runs/"+base["run_id"]+"/review",json={"version":1,"decision":"已复核"})
    broken=[{**GRANTS[0],"end":GRANTS[0]["start"]}]
    assert post_grants(client,broken).status_code==422
    assert len(store.history())==1
    old=store.get(base["run_id"])
    assert old["decision"]=="已复核" and old["version"]==2
    assert client.get("/api/runs/"+base["run_id"]+"/archive").status_code==200
def test_unexplained_differences_block_seal(client):
    "对账差异没讲清楚前不能封存，签核后放行"
    p=client.post("/api/runs",json={"sample":"dirty.csv"}).get_json()
    blocked=client.get("/api/runs/"+p["run_id"]+"/archive")
    assert blocked.status_code==409 and blocked.get_json()["code"]=="UNEXPLAINED_DIFFERENCES"
    client.post("/api/runs/"+p["run_id"]+"/review",json={"version":1,"decision":"已复核"})
    assert client.get("/api/runs/"+p["run_id"]+"/archive").status_code==200
def test_returned_review_reseals(client):
    "退回复核后差异重新变为未说明，再次禁止封存"
    p=client.post("/api/runs",json={"sample":"dirty.csv"}).get_json()
    client.post("/api/runs/"+p["run_id"]+"/review",json={"version":1,"decision":"已复核"})
    client.post("/api/runs/"+p["run_id"]+"/review",json={"version":2,"decision":"退回复核"})
    assert client.get("/api/runs/"+p["run_id"]+"/archive").status_code==409
def test_clean_reconciliation_seals_without_review(client):
    "无差异批次无需说明即可封存"
    text="event_id,principal,resource,partition,access_time,observed,reported,note\n00001,analyst,orders,2026-09,2026-11-16T00:00:00Z,allowed,1,合规\n00002,guest,orders,2026-10,2026-11-16T00:00:00Z,blocked,0,合规\n"
    p=client.post("/api/runs",json={"csv":text}).get_json()
    assert p["stages"][5]["metrics"]["differences"]==0 and p["stages"][6]["metrics"]["ready"] is True
    assert client.get("/api/runs/"+p["run_id"]+"/archive").status_code==200
def test_reviewed_basis_survives_rerun(client):
    "同源重跑复用批次，已复核依据与版本不被重置"
    p=client.post("/api/runs",json={"sample":"dirty.csv"}).get_json()
    client.post("/api/runs/"+p["run_id"]+"/review",json={"version":1,"decision":"已复核"})
    again=client.post("/api/runs",json={"sample":"dirty.csv"}).get_json()
    assert again["run_id"]==p["run_id"] and again["decision"]=="已复核" and again["version"]==2
def test_reviewed_old_run_still_traceable_after_rebase(client):
    "另立新复核后，旧批次的已复核依据仍可追回"
    base=client.post("/api/runs",json={"sample":"dirty.csv"}).get_json()
    client.post("/api/runs/"+base["run_id"]+"/review",json={"version":1,"decision":"已复核"})
    post_grants(client,shortened_grants())
    old=client.get("/api/runs/"+base["run_id"]).get_json()
    assert old["decision"]=="已复核"
    evidence=client.get("/api/runs/"+base["run_id"]+"/archive")
    assert evidence.status_code==200 and base["run_id"] in evidence.data.decode("utf-8-sig")
def test_manifest_records_grant_basis(client):
    "批次清单记录实际授权集，归档可追复核依据"
    p=client.post("/api/runs",json={"sample":"dirty.csv"}).get_json()
    grants=p["manifest"]["parameters"]["grants"]
    assert [g["id"] for g in grants]==sorted(g["id"] for g in grants)
def test_archive_records_supersedes(client):
    "证据导出携带承接批次号"
    client.post("/api/runs",json={"sample":"dirty.csv"})
    renewed=post_grants(client,shortened_grants()).get_json()
    client.post("/api/runs/"+renewed["run_id"]+"/review",json={"version":1,"decision":"已复核"})
    r=client.get("/api/runs/"+renewed["run_id"]+"/archive")
    rows=list(csv.reader(io.StringIO(r.data.decode("utf-8-sig"))))
    assert rows[0][-1]=="supersedes" and rows[1][-1]==renewed["rebase"]["supersedes"]
def test_page_and_archive_follow_same_conclusion(client):
    "页面与归档服从同一存储结果"
    p=client.post("/api/runs",json={"sample":"dirty.csv"}).get_json()
    assert p["run_id"] in client.get("/").data.decode()
    renewed=post_grants(client,shortened_grants()).get_json()
    page=client.get("/grants").data.decode()
    assert renewed["run_id"] in page and renewed["rebase"]["supersedes"] in page
    history=client.get("/api/runs").get_json()["runs"]
    assert [r["run_id"] for r in history]==[renewed["run_id"],p["run_id"]]
def test_access_rejudged_at_occurrence_time(client):
    "每个访问按其发生时刻重新取得授权裁决：到期点之后立即失效"
    text="event_id,principal,resource,partition,access_time,observed,reported,note\n00001,analyst,orders,2026-10,2026-11-30T23:59:59Z,allowed,1,到期前\n00002,analyst,orders,2026-10,2026-12-01T00:00:00Z,allowed,1,到期点\n"
    p=client.post("/api/runs",json={"csv":text}).get_json()
    rows={r["event_id"]:r for r in p["stages"][3]["rows"]}
    assert rows["00001"]["effect"]=="allow" and rows["00002"]["effect"]=="deny"
    assert rows["00002"]["rule_evidence"]=="NO_GRANT"
