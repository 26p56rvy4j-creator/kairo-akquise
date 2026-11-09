from __future__ import annotations


def can(user, action: str, resource=None) -> bool:
    get = user.get if isinstance(user, dict) else lambda key, default=None: getattr(user, key, default)
    if not user or not get("active", False):
        return False
    role = get("role")
    if action in {"read", "record_attempt", "update_lead", "hand_over"}:
        return role in {"admin", "member"}
    if action == "void_attempt":
        return role == "admin" or (role == "member" and resource is not None
                                    and str(resource.get("caller_id")) == str(get("id")))
    if action in {"manage_users", "export"}:
        return role == "admin"
    return False
