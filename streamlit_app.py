#!/usr/bin/env python3
"""
Entry point for Streamlit Cloud deployment
This file must be at the repository root level
"""

import sys
import os

# Add the wealthincome directory to Python path
app_dir = os.path.join(os.path.dirname(__file__), 'wealthincome')
if os.path.exists(app_dir):
    sys.path.insert(0, app_dir)
    
    # Import and run the Home.py app
    try:
        from Home import *
    except ImportError as e:
        import streamlit as st
        st.error(f"Failed to import Home.py: {e}")
        st.error(f"App directory: {app_dir}")
        st.error(f"Directory contents: {os.listdir(app_dir) if os.path.exists(app_dir) else 'Directory not found'}")
else:
    import streamlit as st
    st.error(f"Cannot find wealthincome directory at: {app_dir}")
    st.error(f"Current directory: {os.getcwd()}")
    st.error(f"Directory contents: {os.listdir('.')}")
