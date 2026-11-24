"""Package the Pygame source as a static webplayer using Pygbag."""

from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory


root = Path(__file__).resolve().parent
with TemporaryDirectory() as temp_dir:
    game = Path(temp_dir) / 'zombs-2'
    game.mkdir()
    shutil.copy2(root / 'main.py', game / 'main.py')
    subprocess.run(
        [sys.executable, '-m', 'pygbag', '--build', '--title', 'Zombs 2.0', *sys.argv[1:], str(game)],
        check=True,
    )

    generated = game / 'build' / 'web'
    files = ('index.html', 'favicon.png', 'zombs-2.tar.gz', 'zombs-2.apk')
    for filename in files:
        if not (generated / filename).is_file():
            raise FileNotFoundError(generated / filename)

    public = root / 'public'
    public.mkdir(exist_ok=True)
    for filename in files:
        shutil.copy2(generated / filename, public / filename)
