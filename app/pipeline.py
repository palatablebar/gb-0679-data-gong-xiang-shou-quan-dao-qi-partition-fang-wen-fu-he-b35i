import time
from app.config import ROOT,MODULES
from app.errors import DomainError
from app.values import scalar
from app.manifest import digest,manifest
from app import store
from app.modules.m1_accesses.rules import evaluate as stage1
from app.modules.m2_resources.rules import evaluate as stage2
from app.modules.m3_grants.rules import evaluate as stage3,effective_grants
from app.modules.m4_conflicts.rules import evaluate as stage4
from app.modules.m5_partitions.rules import evaluate as stage5
from app.modules.m6_audit.rules import evaluate as stage6
from app.modules.m7_seals.rules import evaluate as stage7
STAGES=[stage1,stage2,stage3,stage4,stage5,stage6,stage7]
def load_sample(name):
    if name not in {"dirty.csv","clean.csv","legacy.csv"}: raise DomainError("BAD_SAMPLE","样例不存在",404)
    return (ROOT/"sample_data"/name).read_bytes()
def calculate(raw,params=None,stop=6):
    base=params or {};params={**base,"grants":effective_grants(base)};started=time.monotonic();context={"parameters":params,"raw":raw,"quarantine":[]};stages=[];rows=[]
    for index,stage in enumerate(STAGES[:stop+1]):
        result=stage(raw if index==0 else rows,context)
        result=scalar(result);rows=result["rows"]
        context["quarantine"].extend(result.get("quarantine",[]))
        if index==0: context["intake"]=result["metrics"]
        result["cumulative_quarantine"]=list(context["quarantine"])
        context[MODULES[index][0]]=result
        stages.append(result)
    return {"run_id":digest(raw,params)[:20],"stages":stages,"manifest":manifest(raw,params,started),"parameters":params}
def verdict_snapshot(payload):
    "每个访问的正式裁决快照：授权结论、命中规则、分区裁决与执行差异。"
    snap={}
    for row in payload["stages"][4]["rows"]:
        snap[row.get("event_id")]={"effect":row.get("effect"),"rules":[str(x) for x in row.get("matched_rules",[])],"partition_result":row.get("partition_result"),"violation":row.get("violation")}
    return snap
def rebase_summary(old,new):
    "同一输入在新授权下的裁决变化：谁失去了依据、谁重新获得依据。"
    before,after=verdict_snapshot(old),verdict_snapshot(new);changes=[]
    for event_id in sorted(set(before)&set(after)):
        if before[event_id]!=after[event_id]: changes.append({"event_id":event_id,"was":before[event_id],"now":after[event_id]})
    lost=sum(1 for c in changes if c["was"]["effect"]=="allow" and c["now"]["effect"]=="deny")
    gained=sum(1 for c in changes if c["was"]["effect"]=="deny" and c["now"]["effect"]=="allow")
    return {"supersedes":old["run_id"],"changed":len(changes),"lost_basis":lost,"regained_basis":gained,"changes":changes[:100]}
def execute(raw,params=None):
    result=calculate(raw,params)
    previous=store.find_predecessor(result["manifest"]["input_sha256"],result["run_id"])
    if previous: result["rebase"]=rebase_summary(previous,result)
    return store.save(result["run_id"],result)
def stage_preview(index,payload):
    if "rows" in payload and index>0:
        if not isinstance(payload["rows"],list) or any(not isinstance(row,dict) for row in payload["rows"]): raise DomainError("BAD_ROWS","记录需要对象列表")
        if not isinstance(payload.get("context",{}),dict): raise DomainError("BAD_CONTEXT","上下文需要对象")
        return scalar(STAGES[index](payload["rows"],payload.get("context",{"parameters":{},"quarantine":[]})))
    raw=payload.get("csv")
    raw=raw.encode() if isinstance(raw,str) else load_sample(payload.get("sample","dirty.csv"))
    return calculate(raw,payload.get("parameters",{}),index)["stages"][-1]
