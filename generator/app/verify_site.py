import sys
from pathlib import Path
from .publish import validate
if __name__=='__main__':
    validate(Path(sys.argv[1]))
    print('Manifest and all French editions validated.')
