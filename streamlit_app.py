#!/usr/bin/env python3
"""
Entry point for Streamlit Cloud deployment
This version properly handles the wealthincome subdirectory structure
"""

import os
import sys
import runpy

# Get the absolute path to this script
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Path to the wealthincome subdirectory
APP_DIR = os.path.join(SCRIPT_DIR, 'wealthincome')

# CRITICAL: Add the app directory to Python path FIRST
# This allows all imports within the app to work correctly
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

# Also add the script directory for any root-level imports
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

# Change to the app directory so relative paths work
os.chdir(APP_DIR)

# Now run Home.py using runpy to maintain proper context
home_path = os.path.join(APP_DIR, 'Home.py')

if os.path.exists(home_path):
    # Run Home.py in the current namespace
    runpy.run_path(home_path, run_name="__main__")
else:
    import streamlit as st
    st.error(f"Cannot find Home.py at {home_path}")
    st.error(f"Current directory: {os.getcwd()}")
    st.error(f"Python path: {sys.path}")
