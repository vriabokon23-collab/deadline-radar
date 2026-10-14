"""Planner page."""
from contextlib import closing
import streamlit as st
from app import get_connection, render_plan_my_week

with closing(get_connection()) as conn:
    render_plan_my_week(conn, st.session_state.reference_today)
