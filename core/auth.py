"""
Authentication Manager
Handles user authentication and session management
"""

import streamlit as st
import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from pathlib import Path

logger = logging.getLogger(__name__)

class AuthenticationManager:
    """Manages user authentication and sessions"""
    
    def __init__(self, config):
        self.config = config
        self.users_file = config.PERSISTENT_DIR / "users.json"
        self.sessions_file = config.CACHE_DIR / "sessions.json"
        
        # Initialize default admin user if no users exist
        self._init_default_user()
    
    def _init_default_user(self):
        """Initialize default admin user"""
        if not self.users_file.exists():
            default_user = {
                "admin": {
                    "password_hash": self._hash_password("admin123"),
                    "email": "admin@wealthincome.ai",
                    "role": "admin",
                    "created_at": datetime.now().isoformat(),
                    "last_login": None,
                    "is_active": True
                }
            }
            
            self._save_users(default_user)
            logger.info("Default admin user created (username: admin, password: admin123)")
    
    def _hash_password(self, password: str) -> str:
        """Hash password using SHA-256"""
        return hashlib.sha256(password.encode()).hexdigest()
    
    def _load_users(self) -> Dict[str, Any]:
        """Load users from file"""
        try:
            if self.users_file.exists():
                with open(self.users_file, 'r') as f:
                    return json.load(f)
            return {}
        except Exception as e:
            logger.error(f"Error loading users: {e}")
            return {}
    
    def _save_users(self, users: Dict[str, Any]):
        """Save users to file"""
        try:
            with open(self.users_file, 'w') as f:
                json.dump(users, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving users: {e}")
    
    def _load_sessions(self) -> Dict[str, Any]:
        """Load active sessions"""
        try:
            if self.sessions_file.exists():
                with open(self.sessions_file, 'r') as f:
                    sessions = json.load(f)
                    # Clean expired sessions
                    now = datetime.now()
                    active_sessions = {}
                    for session_id, session_data in sessions.items():
                        expires_at = datetime.fromisoformat(session_data['expires_at'])
                        if expires_at > now:
                            active_sessions[session_id] = session_data
                    return active_sessions
            return {}
        except Exception as e:
            logger.error(f"Error loading sessions: {e}")
            return {}
    
    def _save_sessions(self, sessions: Dict[str, Any]):
        """Save sessions to file"""
        try:
            with open(self.sessions_file, 'w') as f:
                json.dump(sessions, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving sessions: {e}")
    
    def authenticate(self, username: str, password: str) -> bool:
        """Authenticate user credentials"""
        users = self._load_users()
        
        if username not in users:
            return False
        
        user_data = users[username]
        
        # Check if user is active
        if not user_data.get('is_active', True):
            return False
        
        # Verify password
        password_hash = self._hash_password(password)
        if password_hash != user_data['password_hash']:
            return False
        
        # Update last login
        user_data['last_login'] = datetime.now().isoformat()
        users[username] = user_data
        self._save_users(users)
        
        # Create session
        self._create_session(username, user_data)
        
        return True
    
    def _create_session(self, username: str, user_data: Dict[str, Any]):
        """Create user session"""
        session_id = hashlib.md5(f"{username}_{datetime.now()}".encode()).hexdigest()
        
        session_data = {
            'username': username,
            'email': user_data.get('email', ''),
            'role': user_data.get('role', 'user'),
            'created_at': datetime.now().isoformat(),
            'expires_at': (datetime.now() + timedelta(seconds=self.config.SESSION_TIMEOUT)).isoformat(),
            'last_activity': datetime.now().isoformat()
        }
        
        # Store session in Streamlit session state
        st.session_state['auth_session_id'] = session_id
        st.session_state['auth_user'] = session_data
        st.session_state['auth_authenticated'] = True
        
        # Store session in file
        sessions = self._load_sessions()
        sessions[session_id] = session_data
        self._save_sessions(sessions)
    
    def is_authenticated(self) -> bool:
        """Check if user is authenticated"""
        if not st.session_state.get('auth_authenticated', False):
            return False
        
        session_id = st.session_state.get('auth_session_id')
        if not session_id:
            return False
        
        # Check session validity
        sessions = self._load_sessions()
        if session_id not in sessions:
            self.logout()
            return False
        
        session_data = sessions[session_id]
        expires_at = datetime.fromisoformat(session_data['expires_at'])
        
        if expires_at <= datetime.now():
            self.logout()
            return False
        
        # Update last activity
        session_data['last_activity'] = datetime.now().isoformat()
        sessions[session_id] = session_data
        self._save_sessions(sessions)
        
        return True
    
    def get_current_user(self) -> Optional[Dict[str, Any]]:
        """Get current authenticated user"""
        if self.is_authenticated():
            return st.session_state.get('auth_user')
        return None
    
    def logout(self):
        """Logout current user"""
        session_id = st.session_state.get('auth_session_id')
        
        # Remove session from file
        if session_id:
            sessions = self._load_sessions()
            if session_id in sessions:
                del sessions[session_id]
                self._save_sessions(sessions)
        
        # Clear session state
        for key in ['auth_session_id', 'auth_user', 'auth_authenticated']:
            if key in st.session_state:
                del st.session_state[key]
    
    def register_user(self, username: str, password: str, email: str, role: str = "user") -> bool:
        """Register new user"""
        if len(password) < self.config.PASSWORD_MIN_LENGTH:
            return False
        
        users = self._load_users()
        
        if username in users:
            return False
        
        users[username] = {
            "password_hash": self._hash_password(password),
            "email": email,
            "role": role,
            "created_at": datetime.now().isoformat(),
            "last_login": None,
            "is_active": True
        }
        
        self._save_users(users)
        return True
    
    def change_password(self, username: str, old_password: str, new_password: str) -> bool:
        """Change user password"""
        users = self._load_users()
        
        if username not in users:
            return False
        
        # Verify old password
        old_hash = self._hash_password(old_password)
        if old_hash != users[username]['password_hash']:
            return False
        
        # Validate new password
        if len(new_password) < self.config.PASSWORD_MIN_LENGTH:
            return False
        
        # Update password
        users[username]['password_hash'] = self._hash_password(new_password)
        self._save_users(users)
        
        return True
    
    def render_login(self):
        """Render login form"""
        st.markdown("### 🔐 Login to WealthIncome AI")
        
        with st.form("login_form"):
            username = st.text_input("Username", placeholder="Enter your username")
            password = st.text_input("Password", type="password", placeholder="Enter your password")
            submitted = st.form_submit_button("Login", use_container_width=True, type="primary")
            
            if submitted:
                if username and password:
                    if self.authenticate(username, password):
                        st.success("Login successful!")
                        st.rerun()
                    else:
                        st.error("Invalid username or password")
                else:
                    st.error("Please enter both username and password")
        
        # Demo credentials info
        st.info("**Demo Credentials:**\n\nUsername: `admin`\n\nPassword: `admin123`")
        
        # Registration option
        with st.expander("Create New Account"):
            with st.form("register_form"):
                new_username = st.text_input("New Username")
                new_email = st.text_input("Email")
                new_password = st.text_input("New Password", type="password")
                confirm_password = st.text_input("Confirm Password", type="password")
                register_submitted = st.form_submit_button("Register")
                
                if register_submitted:
                    if new_password != confirm_password:
                        st.error("Passwords do not match")
                    elif len(new_password) < self.config.PASSWORD_MIN_LENGTH:
                        st.error(f"Password must be at least {self.config.PASSWORD_MIN_LENGTH} characters")
                    elif self.register_user(new_username, new_password, new_email):
                        st.success("Account created successfully! You can now login.")
                    else:
                        st.error("Username already exists or registration failed")
    
    def render_user_menu(self):
        """Render user menu in sidebar"""
        user = self.get_current_user()
        if not user:
            return
        
        st.sidebar.markdown("---")
        st.sidebar.markdown(f"👤 **{user['username']}**")
        st.sidebar.caption(f"Role: {user['role']}")
        
        if st.sidebar.button("🚪 Logout", use_container_width=True):
            self.logout()
            st.rerun()
    
    def require_auth(self):
        """Decorator function to require authentication"""
        if not self.is_authenticated():
            self.render_login()
            st.stop()
    
    def get_user_preferences(self) -> Dict[str, Any]:
        """Get user preferences"""
        user = self.get_current_user()
        if not user:
            return {}
        
        prefs_file = self.config.PERSISTENT_DIR / f"preferences_{user['username']}.json"
        
        try:
            if prefs_file.exists():
                with open(prefs_file, 'r') as f:
                    return json.load(f)
            return {}
        except Exception as e:
            logger.error(f"Error loading user preferences: {e}")
            return {}
    
    def save_user_preferences(self, preferences: Dict[str, Any]):
        """Save user preferences"""
        user = self.get_current_user()
        if not user:
            return
        
        prefs_file = self.config.PERSISTENT_DIR / f"preferences_{user['username']}.json"
        
        try:
            with open(prefs_file, 'w') as f:
                json.dump(preferences, f, indent=2)
        except Exception as e:
            logger.error(f"Error saving user preferences: {e}")