import json, os, sys
import athena
print(json.dumps({'executable': sys.executable, 'prefix': os.path.realpath(sys.prefix), 'version': sys.version.split()[0], 'athena': os.path.realpath(athena.__file__)}))
