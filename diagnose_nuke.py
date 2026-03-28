#!/usr/bin/env python3
"""
Debug script to understand why Nuke script detection is failing.
Run this while you have a .nk script open in Nuke.
"""

import psutil
import platform
import os

def diagnose_nuke_detection():
    print("=" * 80)
    print("NUKE DETECTION DIAGNOSTIC")
    print(f"Platform: {platform.system()}")
    print("=" * 80)
    
    nuke_procs = []
    
    # Find all Nuke processes
    print("\n1. Finding Nuke processes...")
    for proc in psutil.process_iter(['pid', 'name']):
        try:
            name = (proc.info['name'] or '').lower()
            if 'nuke' in name and 'python' not in name:
                nuke_procs.append(proc)
        except:
            pass
    
    print(f"   Found {len(nuke_procs)} Nuke process(es)")
    
    if not nuke_procs:
        print("\n   No Nuke processes found. Is Nuke running?")
        return
    
    # Analyze each process
    for proc in nuke_procs:
        print(f"\n{'=' * 60}")
        print(f"Process: {proc.name()} (PID: {proc.pid})")
        print(f"{'=' * 60}")
        
        # Method 1: Command line
        print("\n   [Method 1: Command Line]")
        try:
            cmdline = proc.cmdline()
            print(f"   Full cmdline ({len(cmdline)} args):")
            for i, arg in enumerate(cmdline):
                print(f"      [{i}] {arg}")
            
            # Check for .nk files
            nk_args = [a for a in cmdline if a.endswith('.nk')]
            if nk_args:
                print(f"   ✓ Found .nk in cmdline: {nk_args}")
            else:
                print(f"   ✗ No .nk file in command line")
        except psutil.AccessDenied:
            print("   ✗ Access denied reading cmdline")
        except Exception as e:
            print(f"   ✗ Error: {e}")
        
        # Method 2: Open files
        print("\n   [Method 2: Open Files]")
        try:
            open_files = proc.open_files()
            print(f"   Process has {len(open_files)} open file handles")
            
            nk_files = [f for f in open_files if f.path.endswith('.nk')]
            if nk_files:
                print(f"   ✓ Found .nk in open files:")
                for f in nk_files:
                    print(f"      {f.path}")
            else:
                print(f"   ✗ No .nk file in open files")
                
            # Show some interesting files
            interesting = [f for f in open_files if any(x in f.path.lower() for x in ['.nk', '.py', 'nuke', 'script', 'project'])]
            if interesting and not nk_files:
                print(f"   Interesting files found:")
                for f in interesting[:10]:
                    print(f"      {f.path}")
        except psutil.AccessDenied:
            print("   ✗ Access denied reading open files")
        except Exception as e:
            print(f"   ✗ Error: {e}")
        
        # Method 3: Memory maps (might show loaded files)
        print("\n   [Method 3: Memory Maps]")
        try:
            mmaps = proc.memory_maps()
            nk_maps = [m for m in mmaps if '.nk' in m.path.lower()]
            if nk_maps:
                print(f"   ✓ Found .nk in memory maps:")
                for m in nk_maps[:5]:
                    print(f"      {m.path}")
            else:
                print(f"   ✗ No .nk in memory maps")
        except psutil.AccessDenied:
            print("   ✗ Access denied reading memory maps")
        except Exception as e:
            print(f"   ✗ Error: {e}")
        
        # Method 4: Environment variables
        print("\n   [Method 4: Environment Variables]")
        try:
            env = proc.environ()
            interesting_vars = {k: v for k, v in env.items() 
                              if any(x in k.upper() for x in ['NUKE', 'SCRIPT', 'FILE', 'PROJECT', 'COMP'])}
            if interesting_vars:
                print(f"   Potentially useful env vars:")
                for k, v in list(interesting_vars.items())[:10]:
                    print(f"      {k}={v[:100]}...")
            else:
                print(f"   No obviously useful env vars")
        except psutil.AccessDenied:
            print("   ✗ Access denied reading environment")
        except Exception as e:
            print(f"   ✗ Error: {e}")
        
        # Method 5: Current working directory
        print("\n   [Method 5: Current Working Directory]")
        try:
            cwd = proc.cwd()
            print(f"   CWD: {cwd}")
            
            # Check if there are .nk files in cwd
            if os.path.isdir(cwd):
                nk_in_cwd = [f for f in os.listdir(cwd) if f.endswith('.nk')]
                if nk_in_cwd:
                    print(f"   .nk files in CWD: {nk_in_cwd[:5]}")
        except psutil.AccessDenied:
            print("   ✗ Access denied reading CWD")
        except Exception as e:
            print(f"   ✗ Error: {e}")
    
    print("\n" + "=" * 80)
    print("DIAGNOSIS COMPLETE")
    print("=" * 80)
    print("""
NOTES:
- If cmdline shows the .nk path, that's the easiest detection method
- If open_files shows .nk, the file handle method works
- If neither works, we may need alternative approaches:
  1. Monitor Nuke's autosave/backup files
  2. Parse Nuke's recent files list
  3. Use a Nuke Python plugin to report open scripts
  4. Monitor file system for .nk access
""")

if __name__ == "__main__":
    diagnose_nuke_detection()
