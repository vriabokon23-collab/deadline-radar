"""Overview page."""
from contextlib import closing
import streamlit as st
from app import get_connection, render_dashboard

with closing(get_connection()) as conn:
    render_dashboard(conn, st.session_state.reference_today)
