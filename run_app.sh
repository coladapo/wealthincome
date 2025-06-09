#!/bin/bash
# Run Streamlit app

echo "Starting WealthIncome app..."
echo "Open your browser to: http://localhost:8501"
echo ""
echo "Press Ctrl+C to stop the server"
echo ""

# Run from the main directory
streamlit run streamlit_app.py --server.headless true