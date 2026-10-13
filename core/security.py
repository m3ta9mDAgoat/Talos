"""Explicit authentication; no anonymous research/worker endpoints."""
from __future__ import annotations
import hmac, os, secrets
from fastapi import HTTPException, Request

def token_for(role: str)->str:
    key='TALOS_LAB_ADMIN_TOKEN' if role=='admin' else 'TALOS_LAB_WORKER_TOKEN'
    value=os.getenv(key,'')
    if len(value)<24:
        raise RuntimeError(f'{key} must be set to a random >=24 character secret')
    return value

def authorize(request:Request,role:str='admin'):
    candidate=request.headers.get('authorization','')
    provided=candidate[7:] if candidate.lower().startswith('bearer ') else ''
    try: expected=token_for(role)
    except RuntimeError:raise HTTPException(503, 'Lab access token is not configured')
    if not hmac.compare_digest(provided,expected): raise HTTPException(401,'Not authorized')
    return True

def new_token()->str:
    return secrets.token_urlsafe(40)
