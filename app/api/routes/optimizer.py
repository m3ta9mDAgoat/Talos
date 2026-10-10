from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.analysis import AnalysisRunRecord, SignalBacktestRecord
from app.services.trade_metrics import summarize_returns

router=APIRouter(prefix="/optimizer",tags=["optimizer"])

@router.get("/runs/{run_id}")
def optimize_run(run_id:int, db:Session=Depends(get_db)):
    run=db.get(AnalysisRunRecord,run_id)
    if not run or run.status!="complete": raise HTTPException(400,"Completed analysis run required")
    vals=[float(x.return_pct) for x in db.query(SignalBacktestRecord).filter(SignalBacktestRecord.analysis_run_id==run_id,SignalBacktestRecord.return_pct.isnot(None)).order_by(SignalBacktestRecord.timestamp.asc()).all()]
    if not vals: raise HTTPException(400,"No tested returns in this run")
    trials=[]
    for i in range(1,31):
        f=i/100
        m=summarize_returns(vals,f)
        ret=float(m.get("model_equity_return_pct") or 0)
        dd=abs(float(m.get("model_max_drawdown_pct") or 0))
        score=ret/(dd+1e-9) if dd>0 else ret
        trials.append({"portfolio_fraction":f,"score":round(score,6),**m})
    trials.sort(key=lambda x:x["score"],reverse=True)
    return {"run_id":run_id,"objective":"maximize equity_return / abs(max_drawdown)","best":trials[0],"trials":trials}
