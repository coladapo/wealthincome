# data_manager.py
"""
Centralized data management for the trading platform
Handles caching, data sharing between pages, and API optimization
"""

import streamlit as st
import yfinance as yf
import pandas as pd
import json
from datetime import datetime, timedelta
from pathlib import Path
import pickle
import pytz
import numpy as np

# Attempt to import openai
OPENAI_INSTALLED_DM = False
openai_client_dm = None
OPENAI_AUTH_ERROR_MESSAGE_DM = None

try:
    import openai
    OPENAI_INSTALLED_DM = True
except ImportError:
    pass

class DataManager:
    """Manages all data operations across the platform"""
    
    def __init__(self):
        # Update the cache directory path
        self.cache_dir = Path("data/cache")
        # Ensure the directory exists on initialization
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.use_ai_sentiment_dm = False
        
        # Also create persistent directory if needed
        self.persistent_dir = Path("data/persistent")
        self.persistent_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize OpenAI if available
        if OPENAI_INSTALLED_DM:
            openai_api_key_from_secrets = st.secrets.get("OPENAI_API_KEY")
            if openai_api_key_from_secrets:
                try:
                    global openai_client_dm
                    openai_client_dm = openai.OpenAI(api_key=openai_api_key_from_secrets)
                    self.use_ai_sentiment_dm = True
                except openai.AuthenticationError as auth_err:
                    global OPENAI_AUTH_ERROR_MESSAGE_DM
                    OPENAI_AUTH_ERROR_MESSAGE_DM = f"DM OpenAI AuthError: {auth_err}. Basic sentiment will be used."
                except Exception as e_client_init:
                    pass
