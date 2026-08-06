#!/usr/bin/env python3
"""
Script to add MPL-2.0 license headers with copyright to all Python source files.
"""
import os
import glob
import datetime

# Get the current year for the copyright
current_year = datetime.datetime.now().year

# MPL-2.0 License Header with Lineai Inc. copyright
MPL_HEADER = f"""# Copyright (C) {current_year} Lineai Inc.
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""

def add_license_header(file_path):
    """Adds the MPL-2.0 license header with copyright to a file if it doesn't already have it."""
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Check if header is already present (approximate check)
    if "Mozilla Public License" in content and "Lineai Inc." in content:
        print(f"Skipping {file_path} - header appears to be present")
        return
    
    # Preserve any shebang or encoding comment at the top
    lines = content.splitlines()
    prefix = ""
    if lines and (lines[0].startswith('#!') or '# -*- coding' in lines[0]):
        prefix = lines[0] + '\n'
        content = '\n'.join(lines[1:])
    
    # Add a blank line after the license header if the file isn't empty
    if content.strip():
        new_content = prefix + MPL_HEADER + '\n' + content
    else:
        new_content = prefix + MPL_HEADER + content
    
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    
    print(f"Added license header with copyright to {file_path}")

def main():
    """Find all Python files and add the license header with copyright."""
    # Get all Python files in the src directory
    python_files = []
    
    # Add files from src directory
    for root, _, files in os.walk('src'):
        for file in files:
            if file.endswith('.py'):
                python_files.append(os.path.join(root, file))
    
    # Add files from scripts or other top-level Python files
    for py_file in glob.glob('*.py'):
        python_files.append(py_file)
    
    print(f"Found {len(python_files)} Python files")
    
    # Add license header to each file
    for file_path in python_files:
        add_license_header(file_path)

if __name__ == "__main__":
    main()