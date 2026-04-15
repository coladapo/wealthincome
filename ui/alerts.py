"""
Alert and notification rendering utilities
"""

import streamlit as st
from typing import Optional


def render_alert_card(title: str, message: str, alert_type: str = "info"):
    """Render an alert card"""
    icons = {"info": "ℹ️", "success": "✅", "warning": "⚠️", "error": "❌"}
    icon = icons.get(alert_type, "ℹ️")

    if alert_type == "success":
        st.success(f"{icon} **{title}** — {message}")
    elif alert_type == "warning":
        st.warning(f"{icon} **{title}** — {message}")
    elif alert_type == "error":
        st.error(f"{icon} **{title}** — {message}")
    else:
        st.info(f"{icon} **{title}** — {message}")


def render_notification_toast(message: str, icon: Optional[str] = "🔔"):
    """Render a toast notification"""
    st.toast(message, icon=icon)
