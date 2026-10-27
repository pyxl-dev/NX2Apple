#!/usr/bin/env python3
# Frees space in a game's NSP folder by moving what nxnative does not need to the Trash (never deletes: emptying the Trash is the user's call).
# A packed Mac app (<name>.app) is self-contained: the launcher, the host, the RomFS, the shader libraries, the DLC and the rewritten modules are inside it,
# and its save and log live in ~/Library (dm/mac/launcher.c). Three levels, each including the one before:
#   junk  download leftovers (*.html, *.url, *.nfo, readme*.txt, _junk/), .zip archives whose game files sit beside them, a stray <title ID>_build
#         beside the work folder the Mac front end uses (game_build), and a work folder's tmp/. Nothing a build or the game reads.
#   work  also the work folder's extract/ and nsp/: the next build extracts them again from the NSPs (about a minute). shader/, pso/, patched_*/ stay,
#         since they are what makes a rebuild fast. The RomFS inside an app is an APFS clone of extract/ when both are on one volume, so trashing extract/
#         frees less than its size there.
#   app   everything except the .app bundles and their .app.key: the game runs as before, but rebuilding it, or adding an update or DLC, needs the
#         NSPs again. A folder without a complete app is left as it is.
# Usage: python3 scripts/clean_game_folder.py <game folder>... [--level junk|work|app] [--each] [--apply]
#   One game per folder; --each takes every subfolder of the folders given as one game (a library folder). Without --apply it only lists what would go.
#   Stops when a build is running (build_ipa_from_nsp.py, which NS Port Builder runs too). Exit code 0 on success, 1 on error.
import argparse, os, re, subprocess, sys, zipfile
from pathlib import Path

LEVELS = ('junk', 'work', 'app')
GAME_FILES = ('.nsp', '.xci', '.nsz', '.xcz')
JUNK_SUFFIXES = ('.html', '.htm', '.url', '.nfo', '.webloc')
WORK_NAMES = re.compile(r'^(game_build|[0-9A-Fa-f]{16}_build)$')

def size(p):
    if p.is_symlink() or p.is_file(): return p.lstat().st_size
    return sum(f.lstat().st_size for f in p.rglob('*') if f.is_file() and not f.is_symlink())

def human(n):
    for u in ('B', 'KB', 'MB', 'GB'):
        if n < 1024: return f'{n:.0f} {u}' if u == 'B' else f'{n:.1f} {u}'
        n /= 1024
    return f'{n:.1f} TB'

def is_app(p):   # a packed Mac app with its game data (the launcher refuses one without)
    r = p / 'Contents/Resources'
    return p.suffix == '.app' and (p / 'Contents/MacOS/dmhost').is_file() and (r / 'dm').is_dir() and ((r / 'romfs.img').is_file() or (r / 'romfs').is_dir())

def extracted_zip(z):   # a .zip whose game files are all beside it already
    try:
        names = [Path(n).name for n in zipfile.ZipFile(z).namelist() if Path(n).suffix.lower() in GAME_FILES]
    except (zipfile.BadZipFile, OSError):
        return False
    return bool(names) and all((z.parent / n).exists() for n in names)

def walk(game):   # every path in the game folder, without going into .app bundles or work folders (hundreds of thousands of files)
    for root, dirs, files in os.walk(game):
        r = Path(root)
        for d in dirs: yield r / d
        for f in files: yield r / f
        dirs[:] = [d for d in dirs if not (d.endswith('.app') or WORK_NAMES.match(d))]

def work_folders(game):
    return [d for d in walk(game) if d.is_dir() and WORK_NAMES.match(d.name)]

def junk(game):
    out = []
    for p in walk(game):
        n = p.name.lower()
        if p.is_dir() and n == '_junk': out.append(p)
        elif p.is_file() and (p.suffix.lower() in JUNK_SUFFIXES or (n.startswith('readme') and p.suffix.lower() == '.txt')): out.append(p)
        elif p.is_file() and p.suffix.lower() == '.zip' and extracted_zip(p): out.append(p)
    for w in work_folders(game):
        if w.name != 'game_build' and (w.parent / 'game_build').is_dir(): out.append(w)   # a run without --work-dir game_build left it
        elif (w / 'tmp').is_dir(): out.append(w / 'tmp')
    return [p for p in out if not any(q in p.parents for q in out)]

def work(game):
    return [w / d for w in work_folders(game) if not (w.name != 'game_build' and (w.parent / 'game_build').is_dir()) for d in ('extract', 'nsp') if (w / d).exists()]

def app(game):
    apps = [p for p in walk(game) if is_app(p)]
    if not apps: return None
    keep = set(apps) | {a.with_name(a.name + '.key') for a in apps}
    above = {q for a in apps for q in a.parents}   # folders holding an app: walk into them, do not trash them
    out, todo = [], [game]
    while todo:
        for p in sorted(todo.pop().iterdir()):
            if p in keep or p.name == '.DS_Store': continue
            if p in above: todo.append(p)
            else: out.append(p)
    return out

def plan(game, level):
    items = junk(game)
    if level in ('work', 'app'): items += work(game)
    if level == 'app':
        a = app(game)
        if a is None:
            print(f'{game}: no complete .app inside, level app skipped (junk and work only)')
        else:
            items += a
    items = sorted(set(items))
    return [p for p in items if not any(q in p.parents for q in items)]

def main():
    ap = argparse.ArgumentParser(description='Move what nxnative does not need from game folders to the Trash.')
    ap.add_argument('folders', nargs='+', type=Path, help='game folders (one game each), or library folders with --each')
    ap.add_argument('--level', choices=LEVELS, default='junk', help='junk (default), work or app; see the top of this file')
    ap.add_argument('--each', action='store_true', help='take every subfolder of the folders given as one game')
    ap.add_argument('--apply', action='store_true', help='move the files to the Trash (default: only list them)')
    args = ap.parse_args()
    games = []
    for f in args.folders:
        f = f.expanduser().resolve()
        if not f.is_dir(): sys.exit(f'error: {f} is not a folder')
        games += sorted(d for d in f.iterdir() if d.is_dir() and not d.name.startswith('.')) if args.each else [f]
    if args.apply and subprocess.run(['pgrep', '-f', 'build_ipa_from_nsp'], capture_output=True).returncode == 0:
        sys.exit('error: a build is running (build_ipa_from_nsp.py); clean once it has finished')
    total = 0
    for g in games:
        items = plan(g, args.level)
        if not items: continue
        print(f'{g}:')
        for p in items:
            n = size(p); total += n
            print(f'  {human(n):>9}  {p.relative_to(g)}{"/" if p.is_dir() else ""}')
    if not total:
        print('nothing to clean'); return 0
    print(f'{human(total)} {"moved to the Trash" if args.apply else "would go to the Trash (run again with --apply)"}')
    if args.apply:
        for g in games:
            items = plan(g, args.level)
            if items and subprocess.run(['/usr/bin/trash', *map(str, items)]).returncode:
                sys.exit(f'error: /usr/bin/trash failed in {g}')
    return 0

if __name__ == '__main__':
    sys.exit(main())
