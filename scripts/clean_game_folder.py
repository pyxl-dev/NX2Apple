#!/usr/bin/env python3
# Frees space in a game's NSP folder by moving what nxnative does not need to the Trash (never deletes: emptying the Trash is the user's call).
# A packed Mac app (<name>.app) is self-contained: the launcher, the host, the RomFS, the shader libraries, the DLC and the rewritten modules are inside it,
# and its save and log live in ~/Library (dm/mac/launcher.c). Three levels, each including the one before:
#   junk  download leftovers (*.html, *.url, *.nfo, readme*.txt, _junk/), archives (.zip, .rar, .7z, with their other volumes) whose game files sit
#         beside them or in the folder named after them, a stray <title ID>_build
#         beside the work folder the Mac front end uses (game_build), and a work folder's tmp/. Nothing a build or the game reads.
#   work  also the work folder's extract/ and nsp/: the next build extracts them again from the NSPs (about a minute). shader/, pso/, patched_*/ stay,
#         since they are what makes a rebuild fast. The RomFS inside an app is an APFS clone of extract/ when both are on one volume, so trashing extract/
#         frees less than its size there.
#   app   everything except the .app bundles and their .app.key: the game runs as before, but rebuilding it, or adding an update or DLC, needs the
#         NSPs again. A folder without a complete app is left as it is.
# --gather first moves the game files (.nsp, .xci, .nsz, .xcz), the .app bundles with their .app.key and the work folders found in subfolders up into the game
# folder, since the NSP script reads only that level and NS Port Builder takes only the update and DLC beside the base game. Of several copies of one title ID the
# one the build would take stays (highest [v] version, then largest) and the others go to the Trash, as do the subfolders left without a game file, an app or a
# work folder (download leftovers). Paths in NS Port Builder's library (library.json) follow the moves. Moves are renames on one volume: the build's reuse keys
# hold file names, sizes and times, not paths, so nothing is extracted again. Before that, an archive holding a game file that is nowhere in the game folder is
# unpacked into the folder named after it (with 7zz or 7z from Homebrew's sevenzip, else macOS's bsdtar; a password in the archive's comment, as download sites
# leave one, is tried when none works), so the files are gathered and the archive goes with the junk.
# Usage: python3 scripts/clean_game_folder.py <game folder>... [--level junk|work|app] [--gather] [--each] [--apply]
#   One game per folder; --each takes every subfolder of the folders given as one game (a library folder). Without --apply it only lists what would go.
#   Stops when a build is running (build_ipa_from_nsp.py, which NS Port Builder runs too); --gather --apply also stops while NS Port Builder or a game is open.
#   Exit code 0 on success, 1 on error.
import argparse, json, os, re, shutil, subprocess, sys
from pathlib import Path

LEVELS = ('junk', 'work', 'app')
GAME_FILES = ('.nsp', '.xci', '.nsz', '.xcz')
JUNK_SUFFIXES = ('.html', '.htm', '.url', '.nfo', '.webloc')
WORK_NAMES = re.compile(r'^(game_build|[0-9A-Fa-f]{16}_build)$')
LIBRARY = Path.home() / 'Library/Application Support/NS Port Builder/library.json'

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

ARCHIVES = ('.zip', '.rar', '.7z')
SEVENZIP = shutil.which('7zz') or shutil.which('7z') or next((t for t in ('/opt/homebrew/bin/7zz', '/usr/local/bin/7zz') if os.access(t, os.X_OK)), None)
LISTED = {}

def is_archive(p):   # the first volume of an archive (a .partN.rar other than part1 is a later volume)
    m = re.search(r'\.part(\d+)\.rar$', p.name, re.I)
    return p.is_file() and p.suffix.lower() in ARCHIVES and not (m and int(m.group(1)) != 1)

def volumes(a):   # the archive and its other volumes: name.part2.rar ..., name.r00 ..., name.z01 ..., name.7z.001 ...
    m = re.match(r'(.*)\.part\d+\.rar$', a.name, re.I)
    pat = re.compile(re.escape(m.group(1)) + r'\.part\d+\.rar$' if m else re.escape(a.stem) + r'\.(r|z)\d\d$|' + re.escape(a.name) + r'\.\d{3}$', re.I)
    return [a] + sorted(p for p in a.parent.iterdir() if p != a and pat.match(p.name))

def listing(a):   # (passwords to try, [(game file name, size)]) of an archive, or None when it cannot be read; the comment's password is tried after none
    if a in LISTED: return LISTED[a]
    def run(pw):
        if SEVENZIP:
            r = subprocess.run([SEVENZIP, 'l', '-slt', '-p' + pw, str(a)], capture_output=True, text=True, stdin=subprocess.DEVNULL)
            if r.returncode: return None, r.stdout
            files, cur = [], {}
            for line in r.stdout.splitlines() + ['']:
                if line.startswith(('Path = ', 'Size = ', 'Folder = ')): k, _, v = line.partition(' = '); cur[k] = v
                elif not line and cur:
                    if cur.get('Folder') != '+' and 'Size' in cur and Path(cur.get('Path', '')).suffix.lower() in GAME_FILES: files.append((Path(cur['Path']).name, int(cur['Size'])))
                    cur = {}
            return files, r.stdout
        r = subprocess.run(['bsdtar', '-tvf', str(a)] + (['--passphrase', pw] if pw else []), capture_output=True, text=True, stdin=subprocess.DEVNULL)
        if r.returncode: return None, r.stdout + r.stderr
        files = [(Path(m.group(2)).name, int(m.group(1))) for m in (re.match(r'\S+\s+\S+\s+\S+\s+\S+\s+(\d+)\s+\S+\s+\S+\s+\S+\s+(.*)$', l) for l in r.stdout.splitlines()) if m and Path(m.group(2)).suffix.lower() in GAME_FILES]
        return files, r.stdout
    files, out = run('')
    hint = re.search(r'password[^:\n]*:\s*(\S+)', out, re.I)
    if not files and hint and (f := run(hint.group(1))[0]): files = f   # encrypted names (7z, rar)
    LISTED[a] = None if files is None else ([''] + ([hint.group(1)] if hint else []), files)   # a zip lists its names without its password
    return LISTED[a]

def extracted(a):   # an archive whose game files are all beside it already, or in the folder it was extracted to (<name>/ beside <name>.zip)
    l = listing(a)
    return bool(l and l[1]) and all((a.parent / n).exists() or (a.parent / a.stem / n).exists() for n, _ in l[1])

def unpack(a, pws):   # into <name>/ beside the archive, through <name>.partial/ so a stopped extraction is never taken for a whole one
    dst = a.parent / a.stem
    while dst.exists(): dst = dst.with_name(dst.name + ' (unpacked)')
    tmp = dst.with_name(dst.name + '.partial')
    for pw in pws:
        shutil.rmtree(tmp, ignore_errors=True); tmp.mkdir()
        cmd = [SEVENZIP, 'x', '-y', '-p' + pw, '-o' + str(tmp), str(a)] if SEVENZIP else ['bsdtar', '-xf', str(a), '-C', str(tmp)] + (['--passphrase', pw] if pw else [])
        if subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True).returncode == 0: tmp.rename(dst); return
    shutil.rmtree(tmp, ignore_errors=True); sys.exit(f'error: could not unpack {a} (damaged, or a password that is not in its comment)')

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
        elif is_archive(p) and extracted(p): out += volumes(p)
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

def title_of(p):   # the title ID in a file name, as the NSP script reads it
    m = re.search(r'\[([0-9A-Fa-f]{16})\]', p.name)
    return int(m.group(1), 16) if m else None

def version_of(p):
    m = re.search(r'\[v(\d+)\]', p.name)
    return int(m.group(1)) if m else 0

def gather(game):
    """(moves [(from, to)], extra copies {copy: the kept one}, folders left without game data) for --gather, or None when the folder holds more than one game"""
    found = list(walk(game))
    files = [p for p in found if p.is_file() and p.suffix.lower() in GAME_FILES and p.stat().st_size]
    apps = {(t & ~0xFFF) - 0x1000 if t & 0xFFF not in (0, 0x800) else t & ~0xFFF for t in map(title_of, files) if t is not None}
    if len(apps) > 1: return None
    best = {}   # as extract_aoc keeps one copy per DLC: highest version, then largest; a copy already in the game folder wins a tie
    for p in files:
        k, t = (version_of(p), p.stat().st_size, p.parent == game), title_of(p)
        t = p.name if t is None else t
        if t not in best or k > best[t][0]: best[t] = (k, p)
    keep = {p for _, p in best.values()}
    copies = {p: best[p.name if title_of(p) is None else title_of(p)][1] for p in files if p not in keep}
    bring = [p for p in found if p.parent != game and (p in keep or (p.is_dir() and (p.suffix == '.app' or WORK_NAMES.match(p.name))) or p.name.endswith('.app.key'))]
    moves, taken = [], {p.name for p in game.iterdir()}
    for p in bring:
        if p.name in taken: print(f'{game}: {p.relative_to(game)} stays, {p.name} is already in the game folder'); continue
        taken.add(p.name); moves.append((p, game / p.name))
    moved = {p for p, _ in moves} | set(copies)
    to = dict(moves); copies = {c: to.get(k, k) for c, k in copies.items()}
    data = lambda p: (p.is_file() and p.suffix.lower() in GAME_FILES) or p.suffix == '.app' or p.name.endswith('.app.key') or WORK_NAMES.match(p.name)
    left = [d for d in game.iterdir() if d.is_dir() and not d.name.startswith('.') and d.suffix != '.app' and not WORK_NAMES.match(d.name)
            and not any(data(p) and p not in moved and not any(q in moved for q in p.parents) for p in walk(d))]
    return moves, copies, left

def relink(moves):   # NS Port Builder's library follows the moves (paths of inputs, apps and output folders; a trashed copy's path becomes the kept one's, a trashed folder the game folder)
    if not moves or not LIBRARY.is_file(): return
    pairs = [(str(a), str(b)) for a, b in moves]
    def fix(v):
        if isinstance(v, dict): return {k: fix(x) for k, x in v.items()}
        if isinstance(v, list): return list(dict.fromkeys(map(fix, v))) if all(isinstance(x, str) for x in v) else [fix(x) for x in v]
        if isinstance(v, str):
            for a, b in pairs:
                if v == a or v.startswith(a + '/'): return b + v[len(a):]
        return v
    old = json.loads(LIBRARY.read_text()); new = fix(old)
    if new == old: return
    tmp = LIBRARY.with_suffix('.json.tmp'); tmp.write_text(json.dumps(new)); tmp.replace(LIBRARY)
    print(f'updated {LIBRARY}')

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
    ap.add_argument('--gather', action='store_true', help='first move game files, apps and work folders from subfolders up into the game folder')
    ap.add_argument('--each', action='store_true', help='take every subfolder of the folders given as one game')
    ap.add_argument('--apply', action='store_true', help='move the files to the Trash (default: only list them)')
    args = ap.parse_args()
    games = []
    for f in args.folders:
        f = f.expanduser().resolve()
        if not f.is_dir(): sys.exit(f'error: {f} is not a folder')
        games += sorted(d for d in f.iterdir() if d.is_dir() and not d.name.startswith('.')) if args.each else [f]
    running = lambda pat: subprocess.run(['pgrep', '-f', pat], capture_output=True).returncode == 0
    if args.apply and running('build_ipa_from_nsp'):
        sys.exit('error: a build is running (build_ipa_from_nsp.py); clean once it has finished')
    if args.apply and args.gather and (running('NS Port Builder') or running('NSPortBuilder') or running('Contents/MacOS/dmhost')):
        sys.exit('error: NS Port Builder or a game is open; quit it first (it rewrites library.json, and an open app must not move)')
    total = 0
    for g in games if args.gather else ():
        have = {p.name for p in walk(g) if p.is_file()}
        todo = [(x, l) for x in walk(g) if is_archive(x) and (l := listing(x)) is not None and any(n not in have for n, _ in l[1])]
        for x in walk(g):
            if is_archive(x) and listing(x) is None: print(f'{g}: [RED] cannot read {x.relative_to(g)} (damaged, or a password that is not in its comment)')
        need = sum(n for _, (_, l) in todo for _, n in l)
        if todo and need > shutil.disk_usage(g).free - (2 << 30):
            sys.exit(f'error: unpacking in {g} needs {human(need)} and {human(shutil.disk_usage(g).free)} is free')
        if todo: print(f'{g}:')
        for x, (pw, l) in todo:
            print(f'  {"unpacked" if args.apply else "unpack"}  {x.relative_to(g)}  ({len(l)} game file{"s" if len(l) > 1 else ""}, {human(sum(n for _, n in l))})')
            if args.apply: unpack(x, pw)
        if todo and not args.apply: print('  (the unpacked files are gathered with --apply)')
        r = gather(g)
        if r is None: print(f'{g}: more than one game inside, --gather skipped'); continue
        moves, copies, left = r
        if not (moves or copies or left): continue
        print(f'{g}:')
        groups = {}
        for a, _ in moves: groups.setdefault(a.parent, []).append(a)
        for d, ps in sorted(groups.items()):
            files = [x for x in ps if x.is_file() and x.suffix.lower() in GAME_FILES]
            if files: print(f'  {"moved" if args.apply else "move"} up  {len(files)} game file{"s" if len(files) > 1 else ""} from {d.relative_to(g)}/')
            for x in sorted(set(ps) - set(files)): print(f'  {"moved" if args.apply else "move"} up  {x.relative_to(g)}{"/" if x.is_dir() else ""}')
        trash = sorted({*copies, *left}); trash = [p for p in trash if not any(q in p.parents for q in trash)]
        for p in copies: print(f'  {human(size(p)):>9}  {p.relative_to(g)}  (another copy of this title is kept)')
        for p in trash:
            n = size(p) - sum(size(a) for a, _ in moves if p in a.parents); total += n   # what is left once the game data moved out
            if p not in copies: print(f'  {human(n):>9}  {p.relative_to(g)}/')
        if args.apply:
            for a, b in moves: a.rename(b)
            relink(moves + list(copies.items()) + [(d, g) for d in left])   # moves first: a path inside a trashed folder that moved keeps its name
            if trash and subprocess.run(['/usr/bin/trash', *map(str, trash)]).returncode:
                sys.exit(f'error: /usr/bin/trash failed in {g}')
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
