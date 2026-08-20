from __future__ import annotations

import hmac

import streamlit as st

from utils.config import ACCESS_PASSWORD, ALLOWED_EMAIL_DOMAINS, AUTH_PROVIDER, AUTH_REQUIRED


_ACCESS_GRANTED_KEY = "cc_lab_access_granted"


def _user_value(name: str) -> str:
    try:
        value = getattr(st.user, name, "")
    except Exception:
        value = ""
    return str(value or "").strip()


def require_authentication() -> None:
    """Protege la app con el OIDC configurado por Streamlit cuando se exige."""
    if ACCESS_PASSWORD:
        if not st.session_state.get(_ACCESS_GRANTED_KEY, False):
            st.title("C&C Lab")
            st.caption("Acceso restringido")
            password = st.text_input("Contraseña", type="password")
            if st.button("Ingresar", type="primary", icon=":material/login:"):
                if hmac.compare_digest(password, ACCESS_PASSWORD):
                    st.session_state[_ACCESS_GRANTED_KEY] = True
                    st.rerun()
                else:
                    st.error("Contraseña incorrecta.")
            st.stop()

        with st.sidebar:
            if st.button("Cerrar sesión", icon=":material/logout:", width="stretch"):
                st.session_state.pop(_ACCESS_GRANTED_KEY, None)
                st.rerun()
        return

    if not AUTH_REQUIRED:
        return

    if not st.user.is_logged_in:
        st.title("C&C Lab")
        st.caption("Acceso corporativo requerido")
        if st.button("Ingresar", type="primary", icon=":material/login:"):
            st.login(AUTH_PROVIDER)
        st.stop()

    email = _user_value("email").lower()
    if ALLOWED_EMAIL_DOMAINS and not any(
        email.endswith(f"@{domain}") for domain in ALLOWED_EMAIL_DOMAINS
    ):
        st.error("La cuenta autenticada no pertenece a un dominio autorizado.")
        if st.button("Cerrar sesión", icon=":material/logout:"):
            st.logout()
        st.stop()

    with st.sidebar:
        identity = _user_value("name") or email
        if identity:
            st.caption(identity)
        if st.button("Cerrar sesión", icon=":material/logout:", width="stretch"):
            st.logout()
