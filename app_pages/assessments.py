"""Assessments page."""
from contextlib import closing
import streamlit as st
from app import get_connection, render_assessments

with closing(get_connection()) as conn:
    render_assessments(conn, st.session_state.reference_today)
