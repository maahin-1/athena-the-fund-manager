import subprocess, sys
r = subprocess.run([sys.executable, "-c", "import athena; print(athena.__file__)"], capture_output=True, text=True)
print(r.stdout.strip())
