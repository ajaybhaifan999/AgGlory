"""
ff_runner.py - REAL Free Fire glory push
-----------------------------------------
Replace push_glory() body with your reverse-engineered Free Fire API calls.
This module is imported lazily by app.py. If you don't have it ready,
app.py will fall back to a simulation so the dashboard still works.

Function contract:
    push_glory(ff_uid: str, ff_password: str, target_glory: int) -> dict
        - Login to FF guest account using uid/password
        - Create / join CS lobby
        - Play matches in a loop until target_glory is reached
        - Return summary dict (e.g. {"matches": 10, "glory_gained": 5000})
"""

import time

def ff_login_guest(uid: str, password: str):
    """TODO: implement actual FF guest login. Return a session object/token."""
    # Example placeholder:
    # session = requests.Session()
    # r = session.post("https://ff-login.example/api/guest", json={"uid": uid, "pw": password})
    # r.raise_for_status()
    # return session
    raise NotImplementedError("Plug your FF guest-login code here")

def ff_play_cs_match(session) -> int:
    """TODO: play one Clash Squad match. Return glory gained."""
    raise NotImplementedError("Plug your FF play-match code here")

def push_glory(ff_uid: str, ff_password: str, target_glory: int) -> dict:
    session = ff_login_guest(ff_uid, ff_password)
    gained = 0
    matches = 0
    while gained < target_glory:
        g = ff_play_cs_match(session)
        gained += g
        matches += 1
        time.sleep(2)
        if matches > 200:  # safety
            break
    return {"matches": matches, "glory_gained": gained}
