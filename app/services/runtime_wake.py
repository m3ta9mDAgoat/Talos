from __future__ import annotations

import threading

# In-process wake signals. These let long-lived workers sleep without polling Neon.
# They are intentionally process-local; durable recovery is still handled from SQL
# when the process starts.
JOB_WAKE_EVENT = threading.Event()
SOURCE_WAKE_EVENT = threading.Event()
SIM_WAKE_EVENT = threading.Event()


def wake_jobs() -> None:
    JOB_WAKE_EVENT.set()


def wake_sources() -> None:
    SOURCE_WAKE_EVENT.set()


def wake_simulations() -> None:
    SIM_WAKE_EVENT.set()
