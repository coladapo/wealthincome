#!/usr/bin/env python3
"""
Quick fix script for patterns.py
This will patch the specific error in your patterns.py file
Run from your wealthincome directory:
python fix_patterns.py
"""

import os
import shutil
from datetime import datetime

def fix_patterns_file():
    """Fix the get_comprehensive_data error in patterns.py"""
    
    patterns_path = "pages/patterns.py"
    
    # Check if file exists
    if not os.path.exists(patterns_path):
        print(f"❌ Error: {patterns_path} not found!")
        print(f"   Current directory: {os.getcwd()}")
        return False
    
    # Create backup
    backup_path = f"pages/patterns_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.py"
    shutil.copy2(patterns_path, backup_path)
    print(f"✅ Created backup: {backup_path}")
    
    # Read the file
    with open(patterns_path, 'r') as f:
        lines = f.readlines()
    
    # Find and fix the problematic line
    fixed = False
    new_lines = []
    i = 0
    
    while i < len(lines):
        line = lines[i]
        
        # Look for the problematic line
        if 'data_manager.get_comprehensive_data' in line:
            print(f"🔍 Found problematic line {i+1}: {line.strip()}")
            
            # Get the indentation
            indent = len(line) - len(line.lstrip())
            spaces = ' ' * indent
            
            # Replace with the correct code
            new_code = f'''{spaces}# Get stock data using data_manager
{spaces}stock_data = data_manager.get_stock_data([ticker], period=period)
{spaces}
{spaces}if stock_data and ticker in stock_data:
{spaces}    hist_data = stock_data[ticker].get('history')
{spaces}    
{spaces}    if hist_data is not None and not hist_data.empty:
{spaces}        # Store results in session state
{spaces}        st.session_state.pattern_results[ticker] = {{
{spaces}            'data': hist_data,
{spaces}            'info': stock_data[ticker].get('info', {{}}),
{spaces}            'patterns': {{}},
{spaces}            'timestamp': datetime.now()
{spaces}        }}
'''
            new_lines.append(new_code)
            
            # Skip the next few lines that were part of the old error handling
            while i + 1 < len(lines) and ('error' in lines[i + 1] or 'Failed to fetch data' in lines[i + 1]):
                i += 1
                print(f"   Skipping old error handling line: {lines[i].strip()}")
            
            fixed = True
            print("✅ Replaced with correct code")
        else:
            new_lines.append(line)
        
        i += 1
    
    if fixed:
        # Write the fixed file
        with open(patterns_path, 'w') as f:
            f.writelines(new_lines)
        print(f"\n✅ Successfully fixed {patterns_path}")
        print("   The get_comprehensive_data error should be resolved!")
        print("\n📝 Next steps:")
        print("   1. Restart your Streamlit app")
        print("   2. Try the Pattern Recognition page again")
        return True
    else:
        print("\n⚠️  Could not find the problematic line!")
        print("   The file might already be fixed or has a different structure")
        
        # Let's check what's actually in the file around the button
        print("\n🔍 Searching for analyze button...")
        for i, line in enumerate(lines):
            if '"🔄 Analyze"' in line or "'🔄 Analyze'" in line:
                print(f"\nFound analyze button at line {i+1}")
                print("Context:")
                for j in range(max(0, i-2), min(len(lines), i+10)):
                    print(f"  {j+1}: {lines[j].rstrip()}")
                break
        
        return False

def main():
    print("🔧 Patterns.py Quick Fix Tool")
    print("=" * 50)
    
    # First, let's make sure we're in the right directory
    if os.path.exists("wealthincome/pages/patterns.py"):
        os.chdir("wealthincome")
        print("📁 Changed to wealthincome directory")
    elif not os.path.exists("pages/patterns.py"):
        print("❌ Error: Cannot find patterns.py")
        print("   Please run this script from your project root or wealthincome directory")
        return
    
    # Run the fix
    if fix_patterns_file():
        print("\n✅ Fix completed successfully!")
    else:
        print("\n❌ Fix could not be applied")
        print("\n💡 Alternative: Replace the entire file with the provided artifact code")

if __name__ == "__main__":
    main()
