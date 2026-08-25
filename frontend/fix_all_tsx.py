import glob, os

files = glob.glob('/home/spectre/Magistrate/frontend/src/**/*.tsx', recursive=True) + glob.glob('/home/spectre/Magistrate/frontend/app/**/*.tsx', recursive=True)

for path in files:
    with open(path, 'r') as f:
        text = f.read()

    # Replace unquoted prop assignments
    repl = [
        ('tint=dark', 'tint=dark'),
        ('variant=card', 'variant=card'),
        ('variant=surface', 'variant=surface'),
        ('variant=control', 'variant=control'),
        ('animationType=fade', 'animationType=fade'),
        ('animationType=slide', 'animationType=slide'),
    ]
    
    modified = text
    for old, new in repl:
        modified = modified.replace(old, new)
        
    if modified != text:
        with open(path, 'w') as f:
            f.write(modified)
        print(Fixed, os.path.basename(path))

print(All TSX files checked
