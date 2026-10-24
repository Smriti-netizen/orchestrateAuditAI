"""Step 2: ranked account resolution -- threshold sweep, LOO, and 518 coverage."""
import csv, json, sys
from collections import Counter, defaultdict
sys.path.insert(0,"code")
import main as agent
from audit import loaders
from audit.identity import build_resolver
from audit.link import Linker
from audit.account_link import analyse_candidates, resolve_account_rows

data=loaders.load_all(); res=build_resolver(data.identities,data.audit_log)
linker=Linker(res,agent.build_index()["index"].facts,data.activity)
S={r.case_id:r for r in loaders.load_deletion_requests("dataset/sample_deletion_requests.csv")}
A={r.case_id:r for r in loaders.load_deletion_requests("dataset/deletion_requests.csv")}
gold={r["case_id"]:json.loads(r["steps_json"]) for r in csv.DictReader(open("dataset/sample_output.csv",encoding="utf-8-sig"))}
G={c:next((s["resource_id"] for s in gold[c] if s["system_id"]=="account_service"),None) for c in S}
link=defaultdict(list)
for c in S:
    uid=res.resolve(S[c].email)
    link[c]=sorted(rid for (_s,rid) in (linker.resources(uid) if uid else {}) if rid.startswith("account_"))
need=[c for c in sorted(S) if not link[c]]

def evaluate(requests, floor, cases):
    picks,_=resolve_account_rows(list(requests.values()),res,linker,data.activity,floor=floor)
    tp=fp=fn=0
    for c in cases:
        got=picks.get(c)
        want=G[c]
        if want is None: continue
        if got is None: fn+=1
        elif got==want: tp+=1
        else: fp+=1
    return picks,tp,fp,fn

print("="*104)
print("A. STEP 2 -- candidate-count tiers on the 17 sample cases that need an account")
print("="*104)
cand,linked=analyse_candidates(list(S.values()),res,linker,data.activity)
print(f"{'k candidates':<14} {'cases':<6} {'tier picks':<10} {'correct':<8} {'wrong':<6} precision")
for k in (1,2,3,4,5):
    cs=[c for c in need if len(cand.get(c,[]))==k]
    if not cs: continue
    got=sum(1 for c in cs if cand[c][0][2]==G[c])
    print(f"{k:<14} {len(cs):<6} {len(cs):<10} {got:<8} {len(cs)-got:<6} {got/len(cs) if cs else 0:.2f}")
cs=[c for c in need if not cand.get(c)]
print(f"{'0 (no row)':<14} {len(cs):<6} {'0':<10} {'0':<8} {'0':<6} --    {cs}")

print()
print("="*104)
print("B. threshold sweep: confidence floor -> sample recall/precision and STEP F1 (account steps)")
print("="*104)
print(f"{'floor':<7} {'fire':<5} {'tp':<3} {'fp':<3} {'miss':<5} {'P':<6} {'R':<6} {'stepF1':<7} notes")
for floor in (1.0,0.5,0.34,0.25,0.2,0.0):
    picks,tp,fp,fn=evaluate(S,floor,need)
    linked_tp=sum(1 for c in S if link[c] and link[c][0]==G[c])
    TP=linked_tp+tp; FP=fp; FN=fn
    f1=2*TP/(2*TP+FP+FN)
    print(f"{floor:<7} {len(picks):<5} {tp:<3} {fp:<3} {fn:<5} {tp/len(picks) if picks else 0:<6.2f} {tp/len(need):<6.2f} {f1:<7.3f} linked={linked_tp}")

print()
print("C. leave-one-out over the 17 cases: pick the floor on 16, score the held-out one")
floors=(1.0,0.5,0.34,0.25,0.2,0.0)
tot=Counter()
for held in need:
    train=[c for c in need if c!=held]
    # choose floor maximising training step F1
    best=None
    for floor in floors:
        picks,tp,fp,fn=evaluate(S,floor,train)
        lt=sum(1 for c in train if link[c] and link[c][0]==G[c])
        f1=2*(lt+tp)/(2*(lt+tp)+fp+fn)
        if best is None or f1>best[0]+1e-12: best=(f1,floor)
    floor=best[1]
    picks,_amb=resolve_account_rows(list(S.values()),res,linker,data.activity,floor=floor)
    got=picks.get(held)
    tot["tp" if got==G[held] else ("fn" if got is None else "fp")]+=1
    tot[f"floor{floor}"]+=1
print("  held-out outcomes:",dict((k,v) for k,v in tot.items() if not k.startswith("floor")))
print("  floor chosen per fold:",dict((k,v) for k,v in tot.items() if k.startswith("floor")))

print()
print("="*104)
print("D. 518 coverage at each floor")
print("="*104)
for floor in (1.0,0.5,0.34,0.25,0.2,0.0):
    picks,amb=resolve_account_rows(list(A.values()),res,linker,data.activity,floor=floor)
    rows=Counter(picks.values())
    print(f"  floor={floor:<5} cases_with_account={len(picks):<4} distinct_rows={len(rows):<4} "
          f"reused_rows={sum(1 for v in rows.values() if v>1):<2} omitted={len(A)-len(picks)} "
          f"top_reason={Counter(r for _c,r in amb).most_common(1)}")
