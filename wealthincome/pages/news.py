import sys
import os
import streamlit as st # Keep st import if needed early

# --- Start of Fix ---
# Get the absolute path of the directory containing the current script (e.g., .../pages)
current_dir = os.path.dirname(os.path.abspath(__file__))
# Get the absolute path of the parent directory (e.g., .../wealthincome)
parent_dir = os.path.dirname(current_dir)

# Add the parent directory to the Python system path if it's not already there
if parent_dir not in sys.path:
    sys.path.append(parent_dir)
# --- End of Fix ---

# Now you can import data_manager
try:
    from data_manager import data_manager
except ImportError:
    st.error("🚨 Failed to import 'data_manager'. Please ensure 'data_manager.py' exists in the root directory and the path is correct.")
    st.stop()

import streamlit as st

st.title('🗞️ Market News & Sentiment Feed')
