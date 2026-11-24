# Zombs 2.0

A haunted maze game by Eddie and Rishav (Dead Devs). Clear every zombie and the boss to unlock the exit and reach the next floor. The game uses DFS maze generation, A* pathfinding, finite-state zombie behavior, and a Markov predictor for boss ambushes.

| Controls | Player 1 | Player 2 |
| --- | --- | --- |
| Move | WASD | Arrow keys |
| Shoot | Space | Enter |
| Sprint | Left Shift | Right Shift |

In solo mode, either set of controls works. Press **F1** for the AI overlay, **R** to restart the floor, or **Q** to quit the run. Click the browser player once to start it and enable audio.

## Run locally

```sh
python -m pip install -r requirements.txt
python main.py
```

## Build the webplayer

```sh
python build_web.py
python -m http.server --directory public
```

Open `http://localhost:8000/`. Vercel serves the committed `public/` directory as a static site. The browser loads the Pygbag runtime from the pygame-web CDN.
