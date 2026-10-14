"""Import Handbooks page."""
from contextlib import closing
import streamlit as st
from app import get_connection, render_upload_and_review

with closing(get_connection()) as conn:
    render_upload_and_review(conn)
