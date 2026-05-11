"""User lookup module with a planted None-deref bug.

`get_display_name` calls `.upper()` on the user record's name without
checking if the lookup returned None. The benchmark expects the agent
to add a guard.
"""

_USERS = {
    1: {"name": "alice"},
    2: {"name": "bob"},
}


def lookup(user_id):
    return _USERS.get(user_id)


def get_display_name(user_id):
    user = lookup(user_id)
    # BUG: no None check before .get(...).upper()
    return user.get("name").upper()
