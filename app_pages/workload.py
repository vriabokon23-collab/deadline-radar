"""Workload page."""
from contextlib import closing
import streamlit as st
from app import get_connection, render_workload_heatmap

with closing(get_connection()) as conn:
    render_workload_heatmap(conn, st.session_state.reference_today)
