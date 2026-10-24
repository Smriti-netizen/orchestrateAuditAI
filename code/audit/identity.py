"""Exact, non-fuzzy identity resolution (spec 2.2).

Strong keys only: email (current + historical), email_hash (SHA-256 of lowercased email
plus historical hashes), phone (digits only, current + historical), user_id, and
resource links via LINK_RESOURCE_IDENTITY / source_resource_id. Names are never used as a
sole match key because same-surname decoys exist on purpose.
"""
import hashlib


def _digits(value):
    return "".join(ch for ch in (value or "") if ch.isdigit())


class IdentityResolver:
    def __init__(self, identities, audit_log):
        self.identities = identities or []
        self.audit_log = audit_log or []
        self.user_by_email = {}
        self.user_by_hash = {}
        self.user_by_phone = {}
        self.email_history = {}   # lowercased email -> set(user_id)
        self.hash_history = {}    # lowercased/hex hash -> set(user_id)
        self.phone_history = {}   # digits phone -> set(user_id)
        self._build()

    def _build(self):
        for u in self.identities:
            e = (u.email or "").strip().lower()
            if e and u.user_id:
                self.user_by_email.setdefault(e, u.user_id)
            h = (u.email_hash or "").strip().lower()
            if h and u.user_id:
                self.user_by_hash.setdefault(h, u.user_id)
            p = _digits(u.phone)
            if p and u.user_id:
                self.user_by_phone.setdefault(p, u.user_id)
        for a in self.audit_log:
            if not a.user_id:
                continue
            field = (a.field_name or "").strip().lower()
            if field not in ("email", "email_hash", "phone"):
                continue
            bucket = {"email": self.email_history, "email_hash": self.hash_history,
                      "phone": self.phone_history}[field]
            for val in (a.old_value, a.new_value):
                if not val:
                    continue
                v = _digits(val) if field == "phone" else str(val).strip().lower()
                if v:
                    bucket.setdefault(v, set()).add(a.user_id)

    def resolve(self, email):
        e = (email or "").strip().lower()
        if not e:
            return None
        h = hashlib.sha256(e.encode("utf-8")).hexdigest()
        for table, key in ((self.user_by_email, e), (self.user_by_hash, e),
                           (self.user_by_hash, h), (self.email_history, e),
                           (self.hash_history, h)):
            hit = table.get(key)
            if isinstance(hit, str) and hit:
                return hit
            if hit:
                return sorted(hit)[0]
        return None

    def user_identifier_set(self, user_id):
        """All exact identifiers ever linked to this user (current + historical)."""
        emails, phones, hashes, names = set(), set(), set(), set()
        for u in self.identities:
            if u.user_id != user_id:
                continue
            if (u.email or "").strip():
                emails.add(u.email.strip().lower())
            if (u.phone or "").strip():
                phones.add(_digits(u.phone))
            if (u.email_hash or "").strip():
                hashes.add(u.email_hash.strip().lower())
            if (u.name or "").strip():
                names.add(u.name.strip())
        for a in self.audit_log:
            if a.user_id != user_id:
                continue
            field = (a.field_name or "").strip().lower()
            for val in (a.old_value, a.new_value):
                if not val:
                    continue
                if field == "email":
                    emails.add(str(val).strip().lower())
                elif field == "email_hash":
                    hashes.add(str(val).strip().lower())
                elif field == "phone":
                    phones.add(_digits(val))
                elif field == "name":
                    names.add(str(val).strip())
        return {
            "user_id": user_id,
            "emails": sorted(x for x in emails if "@" in x),
            "phones": sorted(x for x in phones if x.isdigit()),
            "hashes": sorted(x for x in hashes if len(x) == 64),
            "names": sorted(x for x in names if x),
        }


def build_resolver(identities, audit_log):
    return IdentityResolver(identities, audit_log)
