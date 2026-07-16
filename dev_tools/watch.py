import os
import sys
import time
import subprocess

WATCH_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot")
CMD = [sys.executable, "-m", "bot.main"]

def get_last_modified():
    max_mtime = 0
    for root, _, files in os.walk(WATCH_DIR):
        for file in files:
            if file.endswith(".py"):
                path = os.path.join(root, file)
                try:
                    mtime = os.path.getmtime(path)
                    if mtime > max_mtime:
                        max_mtime = mtime
                except OSError:
                    pass
    return max_mtime

def main():
    print(f"👀 Starting auto-reload watcher for {WATCH_DIR}...")
    process = None
    last_mtime = get_last_modified()
    
    try:
        # Start initial process
        process = subprocess.Popen(CMD)
        
        while True:
            time.sleep(1)
            
            # Check if process exited on its own
            if process.poll() is not None:
                print("⚠️ Bot process exited on its own. Restarting in 2 seconds...")
                time.sleep(2)
                process = subprocess.Popen(CMD)
                last_mtime = get_last_modified()
                continue
                
            # Check for file changes
            current_mtime = get_last_modified()
            if current_mtime > last_mtime:
                print("🔄 Change detected in bot/ directory! Restarting bot...")
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                process = subprocess.Popen(CMD)
                last_mtime = current_mtime
    except KeyboardInterrupt:
        print("\n🛑 Stopping watcher...")
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()

if __name__ == "__main__":
    main()
