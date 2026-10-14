"""Settings page."""
from contextlib import closing
import streamlit as st
from app import get_connection, render_settings

with closing(get_connection()) as conn:
    render_settings(conn)
