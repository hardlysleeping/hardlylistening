"""Member administration and the browser's own listen report.

`do_members_update` is the only place a role changes, and it refuses to demote the last
administrator or let an admin demote themselves — the check is here because it needs the current
member, and it happens before any update. `do_activity_listen` is the page reporting one play:
deduped server-side, and attributed to the session's member, never to an id from the body.
"""

from __future__ import annotations

from ..runtime import boot, security


class MemberActions:
    def do_members_update(self) -> None:
        """Approve, demote or re-approve one member. Admins only (route gate)."""
        if not self.member or self.member['role'] != 'admin':
            self.json({"error": "Administrator required"}, 403)
            return
        payload = self.body()
        member_id = str((payload or {}).get("id", ""))
        role = (payload or {}).get("role", "")
        if member_id == self.member['id'] and role != 'admin':
            self.json({'error': 'Cannot demote or block yourself'}, 400)
            return
        if role not in ("member", "admin", "pending", "blocked"):
            self.json({"error": "role must be member, admin or pending"}, 400)
            return
        store, _ = boot()
        security().schema(store)
        with store.lock:
            row = store.conn.execute("SELECT role FROM members WHERE id=?", (member_id,)).fetchone()
            if not row:
                self.json({"error": "No such member"}, 404)
                return
            if row["role"] == "admin" and role != "admin":
                admins = store.conn.execute(
                    "SELECT COUNT(*) FROM members WHERE role='admin'").fetchone()[0]
                if admins <= 1:
                    self.json({"error": "At least one admin must remain"}, 400)
                    return
            store.conn.execute("UPDATE members SET role=? WHERE id=?", (role, member_id))
            store.conn.commit()
        if role not in ('member', 'admin'):
            security().revoke(store, member_id)
        self.json({"ok": True})

    def do_activity_listen(self) -> None:
        """The browser reporting one play. Deduped server-side, stacked on read."""
        payload = self.body()
        track_id = int((payload or {}).get("id") or 0)
        if track_id <= 0:
            raise ValueError("id is required")
        store, _ = boot()
        from social import service as social_service
        social_service.listen(store, self.member, track_id)
        self.json({"ok": True})
