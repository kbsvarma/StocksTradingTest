"""Application roles resolved from a verified identity, never a URL parameter."""
from dataclasses import dataclass

PERMISSIONS = {
    'viewer': {'read', 'export'},
    'analyst': {'read', 'export', 'propose'},
    'reviewer': {'read', 'export', 'review'},
    'admin': {'read', 'export', 'propose', 'review', 'operate'},
    'service': {'read', 'propose', 'operate'},
}


@dataclass(frozen=True)
class Principal:
    user: str
    tenant: str
    role: str

    def require(self, permission):
        if not self.user or not self.tenant or permission not in PERMISSIONS.get(self.role, set()):
            raise PermissionError(f'Role cannot {permission}')


def resolve_identity(identity, policy):
    """identity must come from Streamlit's verified OIDC session (st.user)."""
    if not identity.get('is_logged_in'):
        raise PermissionError('Sign in required')
    email = identity.get('email')
    if not email or identity.get('email_verified') is not True:
        raise PermissionError('Verified email identity required')
    member = (policy.get('members') or {}).get(email.lower())
    if not member or member.get('role') not in PERMISSIONS or member['role'] == 'service':
        raise PermissionError('Identity is not provisioned')
    principal = Principal(email.lower(), member.get('tenant', ''), member['role'])
    principal.require('read')
    return principal
