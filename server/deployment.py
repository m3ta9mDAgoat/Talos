"""Fail closed before starting an internet-exposed Talos research control plane."""
import os

def validate_hosted():
    if os.getenv('TALOS_MODE','').lower() != 'hosted':
        return {'mode':'local','live_orders':False}
    url=os.getenv('TALOS_LAB_DATABASE_URL','')
    if not url.startswith(('postgresql://','postgresql+psycopg://')):
        raise RuntimeError('TALOS_MODE=hosted requires PostgreSQL/Neon, not transient SQLite')
    admin=os.getenv('TALOS_LAB_ADMIN_TOKEN','')
    worker=os.getenv('TALOS_LAB_WORKER_TOKEN','')
    if len(admin)<32 or len(worker)<32 or admin==worker:
        raise RuntimeError('Hosted mode requires two distinct random bearer tokens >=32 characters')
    if os.getenv('TALOS_LAB_CORS_ORIGINS','').strip()=='*':
        raise RuntimeError('Wildcard CORS is not allowed in hosted mode')
    return {'mode':'hosted','live_orders':False}

if __name__=='__main__':
    cfg=validate_hosted()
    print('Talos hosted configuration validated; LIVE ORDERS DISABLED',cfg['mode'])
