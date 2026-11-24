# Zombs 2.0 - Unit 3 Project: Haunted Maze AI Game
# Eddie & Rishav
#
# Two players get teleported into a zombie maze. Kill every zombie (boss too)
# to open the door, then escape to the next floor.
#
# AI systems:
#   MazeGenerator    - DFS recursive backtracking maze
#   Pathfinder       - A* with Manhattan distance
#   MarkovPredictor  - first-order Markov model of each player's moves
#   Zombie (FSM)     - WANDER -> CHASE -> ATTACK
#   Boss (BossAI)    - predicts 3 moves ahead and ambushes with U = 10P - 0.5D
#   DebugOverlay     - F1 shows paths, states, ambush tiles and Markov stats

import asyncio
import heapq
import math
import random
import sys
from array import array

import pygame

WEB = sys.platform == "emscripten"

# Layout
SCREEN_W, SCREEN_H = 1280, 720
CELL = 48
COLS, ROWS = 26, 15
MAZE_W, MAZE_H = COLS * CELL, ROWS * CELL
MAZE_X, MAZE_Y = (SCREEN_W - MAZE_W) // 2, 0     # the maze fills the whole window
FPS = 60

# Colors
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
TEXT = (232, 226, 240)
DIM_TEXT = (150, 140, 165)
BG = (14, 10, 20)
PANEL_BG = (24, 17, 34)
PANEL_EDGE = (78, 44, 112)
FLOOR_A = (86, 86, 94)
FLOOR_B = (80, 80, 88)
GROUT = (64, 63, 72)
WALL = (74, 34, 112)
WALL_LIGHT = (128, 78, 178)
WALL_SHADOW = (24, 12, 36)
P1_COLOR = (70, 145, 255)
P2_COLOR = (250, 208, 52)
ZOMBIE_GREEN = (98, 168, 78)
ZOMBIE_DARK = (52, 98, 40)
BOSS_COAT = (150, 22, 30)
BOSS_COAT_DARK = (86, 10, 16)
HEALTH = (80, 220, 100)
STAMINA = (80, 170, 255)
ORANGE = (255, 150, 40)
YELLOW = (255, 225, 60)
PURPLE_PATH = (200, 90, 255)
RED_PATH = (255, 60, 60)
CRATE = (150, 100, 52)
CRATE_DARK = (92, 58, 26)
METAL = (150, 150, 162)
METAL_DARK = (88, 88, 100)
DOOR_WOOD = (110, 68, 34)
DARK_RGB = (8, 4, 16)
CANDY_COLORS = [(255, 90, 140), (255, 170, 40), (120, 220, 255), (170, 110, 255), (120, 240, 120)]

# Directions
DIRECTIONS = ["UP", "DOWN", "LEFT", "RIGHT"]
DIR_VEC = {"UP": (0, -1), "DOWN": (0, 1), "LEFT": (-1, 0), "RIGHT": (1, 0)}
OPPOSITE = {"UP": "DOWN", "DOWN": "UP", "LEFT": "RIGHT", "RIGHT": "LEFT"}
DIR_ANGLE = {"RIGHT": 0, "DOWN": 90, "LEFT": 180, "UP": 270}

# Gameplay tuning
PLAYER_STEP = 0.17          # seconds per tile walking
SPRINT_STEP = 0.105         # seconds per tile sprinting
WEB_SLOW = 2.6              # step time multiplier when leaving a web
TRAP_TIME = 2.5
MAX_HEALTH = 100
MAX_STAMINA = 100
SPRINT_COST = 9
STAMINA_REGEN = 20
EXHAUST_RECOVER = 30
BULLET_SPEED = 640

ZOMBIE_HP = 3
ZOMBIE_VIEW = 5             # cells
ZOMBIE_FOV_DOT = 0.5        # ~120 degree cone
HEAR_RANGE = 8              # cells, for sprinting players
ZOMBIE_DAMAGE = 8
ZOMBIE_COOLDOWN = 0.9

BOSS_STEP = 0.22
BOSS_DAMAGE = 18
BOSS_COOLDOWN = 1.1
BOSS_REPLAN = 0.5
LOOKAHEAD = 3
REACH_SLACK = 2.5           # players pause, so give the boss some extra time

DEFAULT_PROBS = {"gun": 5, "potion": 5, "webs": 8, "trap": 12}
PROB_LABELS = {"gun": "Reward A: New Gun", "potion": "Reward B: Potion",
               "webs": "Hazard A: Webs", "trap": "Hazard B: Trap"}

GUNS = [
    {"name": "Pistol", "delay": 0.38, "damage": 1, "pellets": 1, "spread": 0},
    {"name": "SMG", "delay": 0.14, "damage": 1, "pellets": 1, "spread": 5},
    {"name": "Shotgun", "delay": 0.6, "damage": 1, "pellets": 5, "spread": 12},
    {"name": "Blaster", "delay": 0.22, "damage": 3, "pellets": 1, "spread": 0},
]


# Grid helpers (all world positions are local to the maze surface)
def cell_center(cell):
    return pygame.Vector2(cell[0] * CELL + CELL / 2, cell[1] * CELL + CELL / 2)


def pixel_to_cell(pos):
    return int(pos.x // CELL), int(pos.y // CELL)


def step_cell(cell, direction):
    dx, dy = DIR_VEC[direction]
    return cell[0] + dx, cell[1] + dy


def direction_between(a, b):
    for d in DIRECTIONS:
        if step_cell(a, d) == b:
            return d
    return None


def manhattan(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def ease(t):
    return t * t * (3 - 2 * t)


def turn_toward(current, target, max_turn):
    diff = (target - current + 180) % 360 - 180
    return current + max(-max_turn, min(max_turn, diff))


# Sound (generated, so the game doesn't need any asset files)
class SoundBank:

    def __init__(self):
        self.sounds = {}
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init(22050, -16, 1, 512)
            self.freq, _size, self.channels = pygame.mixer.get_init()
        except Exception:
            return
        recipes = {
            "chime": (lambda: self.sweep(0.09, 988, 988, decay=10) + self.sweep(0.26, 1319, 1319, decay=9), 0.35),
            "candy": (lambda: self.sweep(0.12, 1568, 2093, decay=18), 0.25),
            "snap": (lambda: self.sweep(0.16, 150, 60, "square", decay=28, noise=0.6), 0.45),
            "creak": (lambda: self.sweep(1.0, 78, 52, "saw", decay=1.4, noise=0.15, vibrato=16, vib_rate=5, attack=6), 0.35),
            "shoot": (lambda: self.sweep(0.07, 420, 120, "square", decay=45, noise=0.7), 0.18),
            "hit": (lambda: self.sweep(0.08, 230, 110, decay=30), 0.3),
            "hurt": (lambda: self.sweep(0.22, 300, 120, "square", decay=10, noise=0.1), 0.25),
            "die": (lambda: self.sweep(0.35, 110, 50, "saw", decay=7, noise=0.3, vibrato=15, vib_rate=9), 0.35),
        }
        for name, (maker, volume) in recipes.items():
            try:
                self.sounds[name] = self.build(maker(), volume)
            except Exception:
                pass

    def sweep(self, duration, f0, f1, shape="sine", decay=8.0, noise=0.0, vibrato=0.0, vib_rate=0.0, attack=200):
        rng = random.Random(7)
        n = int(self.freq * duration)
        out = []
        phase = 0.0
        for i in range(n):
            t = i / self.freq
            f = f0 + (f1 - f0) * (i / n) + vibrato * math.sin(2 * math.pi * vib_rate * t)
            phase += f / self.freq
            if shape == "square":
                v = 1.0 if phase % 1 < 0.5 else -1.0
            elif shape == "saw":
                v = 2 * (phase % 1) - 1
            else:
                v = math.sin(2 * math.pi * phase)
            v = v * (1 - noise) + rng.uniform(-1, 1) * noise
            out.append(v * math.exp(-t * decay) * min(1.0, t * attack))
        return out

    def build(self, samples, volume):
        data = array("h")
        for s in samples:
            v = int(max(-1.0, min(1.0, s)) * 32767 * volume)
            for _ in range(self.channels):
                data.append(v)
        return pygame.mixer.Sound(buffer=data.tobytes())

    def play(self, name):
        sound = self.sounds.get(name)
        if sound:
            sound.play()


# MazeGenerator - DFS recursive backtracking
class MazeGenerator:

    def __init__(self, cols, rows, rng):
        self.cols = cols
        self.rows = rows
        # every cell starts with all four walls
        self.walls = [[{d: True for d in DIRECTIONS} for _ in range(cols)] for _ in range(rows)]
        self.cells = [(c, r) for r in range(rows) for c in range(cols)]
        self.generate(rng)

    def in_bounds(self, cell):
        return 0 <= cell[0] < self.cols and 0 <= cell[1] < self.rows

    def generate(self, rng):
        start = (rng.randrange(self.cols), rng.randrange(self.rows))
        visited = {start}
        stack = [start]
        while stack:
            cell = stack[-1]
            options = [d for d in DIRECTIONS
                       if self.in_bounds(step_cell(cell, d)) and step_cell(cell, d) not in visited]
            if not options:
                stack.pop()          # dead end -> backtrack
                continue
            d = rng.choice(options)
            nxt = step_cell(cell, d)
            # open the wall on both sides
            self.walls[cell[1]][cell[0]][d] = False
            self.walls[nxt[1]][nxt[0]][OPPOSITE[d]] = False
            visited.add(nxt)         # mark right away so we never carve back in
            stack.append(nxt)

    def can_move(self, cell, direction):
        return self.in_bounds(cell) and not self.walls[cell[1]][cell[0]][direction]

    def open_directions(self, cell):
        return [d for d in DIRECTIONS if self.can_move(cell, d)]

    def neighbors(self, cell):
        return [step_cell(cell, d) for d in self.open_directions(cell)]

    def distances_from(self, start):
        dist = {start: 0}
        queue = [start]
        for cell in queue:
            for nxt in self.neighbors(cell):
                if nxt not in dist:
                    dist[nxt] = dist[cell] + 1
                    queue.append(nxt)
        return dist

    def crossing_blocked(self, a, b):
        # is there a wall between two touching cells? (used for bullets + line of sight)
        if a == b:
            return False
        if not self.in_bounds(b):
            return True
        dx, dy = b[0] - a[0], b[1] - a[1]
        if abs(dx) + abs(dy) == 1:
            return not self.can_move(a, direction_between(a, b))
        if abs(dx) == 1 and abs(dy) == 1:
            h = "RIGHT" if dx > 0 else "LEFT"
            v = "DOWN" if dy > 0 else "UP"
            route1 = self.can_move(a, h) and self.can_move(step_cell(a, h), v)
            route2 = self.can_move(a, v) and self.can_move(step_cell(a, v), h)
            return not (route1 or route2)
        return True

    def segment_clear(self, p1, p2, step=6):
        diff = p2 - p1
        n = max(1, int(diff.length() / step))
        prev = pixel_to_cell(p1)
        for i in range(1, n + 1):
            cell = pixel_to_cell(p1 + diff * (i / n))
            if cell != prev:
                if self.crossing_blocked(prev, cell):
                    return False
                prev = cell
        return True


# Pathfinder - A*
class Pathfinder:

    def __init__(self, maze):
        self.maze = maze

    def find_path(self, start, goal):
        if start == goal:
            return [start]
        counter = 0
        open_heap = [(manhattan(start, goal), counter, start)]
        g = {start: 0}
        came_from = {}
        while open_heap:
            f, _, cell = heapq.heappop(open_heap)
            if cell == goal:
                # follow the saved parents back to the start, then reverse
                path = [cell]
                while cell in came_from:
                    cell = came_from[cell]
                    path.append(cell)
                path.reverse()
                return path
            if f > g[cell] + manhattan(cell, goal):
                continue    # old entry, we already found a shorter way here
            for nxt in self.maze.neighbors(cell):
                new_g = g[cell] + 1
                if new_g < g.get(nxt, 10 ** 9):
                    came_from[nxt] = cell
                    g[nxt] = new_g
                    counter += 1
                    heapq.heappush(open_heap, (new_g + manhattan(nxt, goal), counter, nxt))
        return None


# MarkovPredictor - first-order transition counts per player
class MarkovPredictor:

    def __init__(self, player_ids, rng=None):
        self.rng = rng or random.Random()
        self.counts = {}
        self.stats = {}
        for pid in player_ids:
            self.counts[pid] = {a: {b: 0 for b in DIRECTIONS} for a in DIRECTIONS}
            self.stats[pid] = {"last": None, "guess": None, "guess_p": 0.0,
                               "transitions": 0, "checked": 0, "correct": 0}

    def probabilities(self, pid, prev, allowed=DIRECTIONS):
        allowed = list(allowed)
        if not allowed:
            return {}
        row = self.counts[pid][prev] if prev else None
        total = sum(row[d] for d in allowed) if row else 0
        if total == 0:
            return {d: 1 / len(allowed) for d in allowed}
        return {d: row[d] / total for d in allowed}

    def predict(self, pid):
        probs = self.probabilities(pid, self.stats[pid]["last"])
        best = max(probs.values())
        choice = self.rng.choice([d for d, p in probs.items() if p == best])   # ties are random
        return choice, best

    def record_move(self, pid, direction):
        s = self.stats[pid]
        # check the saved guess before learning from the real move
        if s["guess"] is not None:
            s["checked"] += 1
            if s["guess"] == direction:
                s["correct"] += 1
        if s["last"] is not None:
            self.counts[pid][s["last"]][direction] += 1
            s["transitions"] += 1
        s["last"] = direction
        s["guess"], s["guess_p"] = self.predict(pid)

    def accuracy(self, pid):
        s = self.stats[pid]
        if s["checked"] == 0:
            return None
        return 100 * s["correct"] / s["checked"]

    def project(self, pid, start, last, maze, steps=LOOKAHEAD):
        # chance of ending on each tile after `steps` moves (guesses only, not real moves)
        layer = {(start, last): 1.0}
        for _ in range(steps):
            nxt = {}
            for (cell, prev), chance in layer.items():
                allowed = maze.open_directions(cell)
                for d, p in self.probabilities(pid, prev, allowed).items():
                    key = (step_cell(cell, d), d)
                    nxt[key] = nxt.get(key, 0.0) + chance * p
            layer = nxt
        tiles = {}
        for (cell, _), chance in layer.items():
            tiles[cell] = tiles.get(cell, 0.0) + chance
        return tiles


# Things on the floor
class Item:

    def __init__(self, kind, cell):
        self.kind = kind          # gun, potion, webs, trap
        self.cell = cell
        self.sprung = False

    def draw(self, surf, t):
        center = cell_center(self.cell)
        if self.kind in ("gun", "potion"):
            draw_crate(surf, center, 26)
        elif self.kind == "trap":
            draw_trap(surf, center, self.sprung)
        # webs are baked into the floor surface


def draw_crate(surf, center, size):
    rect = pygame.Rect(0, 0, size, size)
    rect.center = (int(center.x), int(center.y))
    pygame.draw.rect(surf, (20, 14, 10), rect.move(2, 3), border_radius=3)
    pygame.draw.rect(surf, CRATE, rect, border_radius=3)
    pygame.draw.rect(surf, CRATE_DARK, rect, 2, border_radius=3)
    pygame.draw.line(surf, CRATE_DARK, rect.topleft, rect.bottomright, 3)
    pygame.draw.line(surf, CRATE_DARK, rect.topright, rect.bottomleft, 3)
    pygame.draw.line(surf, (190, 140, 80), (rect.left + 3, rect.top + 3), (rect.right - 4, rect.top + 3), 1)


def draw_trap(surf, center, sprung):
    c = (int(center.x), int(center.y))
    if not sprung:
        pygame.draw.circle(surf, METAL_DARK, c, 16, 4)
        pygame.draw.circle(surf, METAL, c, 14, 2)
        for i in range(10):
            a = math.radians(i * 36)
            outer = center + pygame.Vector2(math.cos(a), math.sin(a)) * 13
            left = center + pygame.Vector2(math.cos(a - 0.15), math.sin(a - 0.15)) * 13
            tip = center + pygame.Vector2(math.cos(a + 0.05), math.sin(a + 0.05)) * 7
            pygame.draw.polygon(surf, (210, 210, 220), [outer, left, tip])
        pygame.draw.circle(surf, (120, 110, 60), c, 5)
    else:
        pygame.draw.ellipse(surf, METAL_DARK, (c[0] - 16, c[1] - 6, 32, 12))
        points = []
        for i in range(9):
            x = c[0] - 14 + i * 3.5
            points.append((x, c[1] + (-4 if i % 2 == 0 else 4)))
        pygame.draw.lines(surf, (220, 220, 230), False, points, 2)
        pygame.draw.circle(surf, (140, 30, 30), (c[0] + 9, c[1] + 6), 2)


def draw_web(surf, center, radius=21):
    color = (214, 214, 226)
    spokes = 8
    ends = []
    for i in range(spokes):
        a = math.radians(i * 360 / spokes + 10)
        end = center + pygame.Vector2(math.cos(a), math.sin(a)) * radius
        ends.append(a)
        pygame.draw.line(surf, color, center, end, 1)
    for ring in (6, 12, 18):
        pts = [center + pygame.Vector2(math.cos(a), math.sin(a)) * (ring + (i % 2) * 1.5) for i, a in enumerate(ends)]
        pygame.draw.polygon(surf, color, pts, 1)


def draw_candy(surf, center, color):
    x, y = center
    pygame.draw.polygon(surf, color, [(x - 6, y), (x - 12, y - 5), (x - 12, y + 5)])
    pygame.draw.polygon(surf, color, [(x + 6, y), (x + 12, y - 5), (x + 12, y + 5)])
    pygame.draw.ellipse(surf, color, (x - 7, y - 5, 14, 10))
    pygame.draw.ellipse(surf, WHITE, (x - 4, y - 3, 5, 3))
    pygame.draw.ellipse(surf, tuple(int(c * 0.6) for c in color), (x - 7, y - 5, 14, 10), 1)


def draw_potion(surf, center):
    x, y = int(center.x), int(center.y)
    pygame.draw.circle(surf, (220, 60, 120), (x, y + 3), 9)
    pygame.draw.rect(surf, (200, 200, 220), (x - 3, y - 11, 6, 8))
    pygame.draw.rect(surf, (120, 80, 40), (x - 4, y - 13, 8, 3))
    pygame.draw.circle(surf, WHITE, (x - 3, y + 1), 2)


def draw_door(surf, cell, is_open, t):
    rect = pygame.Rect(cell[0] * CELL + 6, cell[1] * CELL + 4, CELL - 12, CELL - 8)
    if is_open:
        glow = 120 + int(60 * math.sin(t * 4))
        pygame.draw.rect(surf, (glow // 3, 20, glow // 2), rect.inflate(6, 6), border_radius=10)
        pygame.draw.rect(surf, (6, 2, 10), rect, border_top_left_radius=14, border_top_right_radius=14)
        bob = math.sin(t * 5) * 3
        cx = rect.centerx
        pygame.draw.polygon(surf, (180, 255, 160), [(cx - 7, rect.centery - 6 + bob),
                                                    (cx + 7, rect.centery - 6 + bob),
                                                    (cx, rect.centery + 4 + bob)])
    else:
        pygame.draw.rect(surf, DOOR_WOOD, rect, border_top_left_radius=14, border_top_right_radius=14)
        for i in range(1, 4):
            x = rect.left + i * rect.width // 4
            pygame.draw.line(surf, (70, 40, 18), (x, rect.top + 6), (x, rect.bottom - 2), 2)
        pygame.draw.rect(surf, METAL_DARK, (rect.left, rect.top + 10, rect.width, 4))
        pygame.draw.rect(surf, METAL_DARK, (rect.left, rect.bottom - 12, rect.width, 4))
        pygame.draw.circle(surf, (230, 190, 60), (rect.right - 9, rect.centery), 3)
        pygame.draw.rect(surf, (60, 30, 12), rect, 2, border_top_left_radius=14, border_top_right_radius=14)


# Moving on the grid (players, zombies and the boss all step tile to tile)
class GridMover:

    def __init__(self, cell):
        self.cell = cell
        self.from_cell = cell
        self.to_cell = cell
        self.progress = 1.0
        self.step_time = 0.2
        self.facing = "DOWN"

    @property
    def moving(self):
        return self.progress < 1.0

    @property
    def plan_cell(self):
        return self.to_cell if self.moving else self.cell

    def start_step(self, direction, step_time):
        self.facing = direction
        self.from_cell = self.cell
        self.to_cell = step_cell(self.cell, direction)
        self.step_time = step_time
        self.progress = 0.0

    def update_step(self, dt):
        if not self.moving:
            return False
        self.progress = min(1.0, self.progress + dt / self.step_time)
        if self.progress >= 1.0:
            self.cell = self.to_cell
            self.from_cell = self.cell
            return True
        return False

    def pixel_pos(self):
        a = cell_center(self.from_cell)
        if not self.moving:
            return a
        return a.lerp(cell_center(self.to_cell), ease(self.progress))

    def follow_path(self, dt, game, step_time):
        if self.moving or not self.path:
            return
        nxt = self.path[0]
        d = direction_between(self.cell, nxt)
        if d is None or not game.maze.can_move(self.cell, d):
            self.path = []
            return
        # never walk on top of a player, stop next to them (that's attack range)
        if any(p.cell == nxt or p.plan_cell == nxt for p in game.living_players()):
            self.facing = d
            return
        # don't stack on another zombie, but don't wait forever either
        if game.cell_taken(self, nxt) and self.wait_timer < 0.6:
            self.wait_timer += dt
            self.facing = d
            return
        self.wait_timer = 0
        self.start_step(d, step_time)


# Player
class Player(GridMover):

    def __init__(self, pid, cell, color, carry=None):
        super().__init__(cell)
        self.pid = pid
        self.color = color
        carry = carry or {}
        self.health = carry.get("health", MAX_HEALTH)
        self.stamina = carry.get("stamina", MAX_STAMINA)
        self.gun_level = carry.get("gun", 0)
        self.down = False
        self.exhausted = False
        self.sprinting = False
        self.stuck_timer = 0.0
        self.shoot_timer = 0.0
        self.hurt_timer = 0.0
        self.invuln = 0.0
        self.draw_angle = DIR_ANGLE[self.facing]

    @property
    def gun(self):
        return GUNS[self.gun_level]

    def status(self, game):
        if self.down:
            return "DOWN"
        if self.stuck_timer > 0:
            return f"TRAPPED {self.stuck_timer:.1f}s"
        item = game.items.get(self.cell)
        if item and item.kind == "webs":
            return "WEBBED"
        if self.exhausted:
            return "TIRED"
        return "OK"

    def update(self, dt, intent, game):
        if self.down:
            return
        direction, shoot, sprint = intent
        self.shoot_timer -= dt
        self.hurt_timer -= dt
        self.invuln -= dt
        if self.stuck_timer > 0:
            self.stuck_timer -= dt

        if self.update_step(dt):
            game.on_player_arrive(self)

        if not self.moving and self.stuck_timer <= 0 and direction:
            self.facing = direction
            # can't walk through walls or zombies
            if game.maze.can_move(self.cell, direction) and not game.enemy_at(step_cell(self.cell, direction)):
                step_time = PLAYER_STEP
                self.sprinting = False
                if sprint and not self.exhausted and self.stamina > 0:
                    step_time = SPRINT_STEP
                    self.sprinting = True
                    self.stamina -= SPRINT_COST
                    if self.stamina <= 0:
                        self.stamina = 0
                        self.exhausted = True
                item = game.items.get(self.cell)
                if item and item.kind == "webs":
                    step_time *= WEB_SLOW
                self.start_step(direction, step_time)
                # only real moves teach the boss (bumping into a wall doesn't count)
                game.predictor.record_move(self.pid, direction)

        if not self.moving:
            self.sprinting = False
        if not self.sprinting:
            self.stamina = min(MAX_STAMINA, self.stamina + STAMINA_REGEN * dt)
            if self.exhausted and self.stamina >= EXHAUST_RECOVER:
                self.exhausted = False

        if shoot and self.shoot_timer <= 0:
            game.fire(self)
            self.shoot_timer = self.gun["delay"]

        self.draw_angle = turn_toward(self.draw_angle, DIR_ANGLE[self.facing], 900 * dt)

    def take_damage(self, amount, game):
        if self.down or self.invuln > 0:
            return
        self.health -= amount
        self.hurt_timer = 0.25
        self.invuln = 0.2
        game.shake = max(game.shake, 7)
        game.sounds.play("hurt")
        game.float_text(f"-{amount}", self.pixel_pos(), (255, 90, 90))
        if self.health <= 0:
            self.health = 0
            self.down = True
            self.sprinting = False
            game.banner(f"{self.pid} is down!")

    def draw(self, surf, font, t):
        pos = self.pixel_pos()
        if self.down:
            pygame.draw.ellipse(surf, (0, 0, 0), (pos.x - 15, pos.y + 6, 30, 10))
            pygame.draw.circle(surf, (110, 110, 120), pos, 14)
            pygame.draw.circle(surf, (60, 60, 70), pos, 14, 2)
            for s in (-5, 5):
                pygame.draw.line(surf, (40, 40, 50), (pos.x + s - 3, pos.y - 5), (pos.x + s + 3, pos.y + 1), 2)
                pygame.draw.line(surf, (40, 40, 50), (pos.x + s + 3, pos.y - 5), (pos.x + s - 3, pos.y + 1), 2)
            label = font.render(f"{self.pid} DOWN", True, (200, 200, 210))
            surf.blit(label, (pos.x - label.get_width() / 2, pos.y - 34))
            return

        forward = pygame.Vector2(1, 0).rotate(self.draw_angle)
        side = forward.rotate(90)
        body = self.color
        if self.hurt_timer > 0:
            body = (255, 120, 120)
        outline = tuple(int(c * 0.45) for c in self.color)

        pygame.draw.ellipse(surf, (20, 18, 26), (pos.x - 14, pos.y + 7, 28, 10))
        # gun in the right hand
        hand_r = pos + forward * 11 + side * 9
        hand_l = pos + forward * 11 - side * 9
        tip = hand_r + forward * 15
        gun_poly = [hand_r - forward * 2 + side * 2.5, tip + side * 2.5, tip - side * 2.5, hand_r - forward * 2 - side * 2.5]
        pygame.draw.polygon(surf, (40, 40, 48), gun_poly)
        pygame.draw.circle(surf, body, pos, 14)
        pygame.draw.circle(surf, outline, pos, 14, 2)
        for hand in (hand_r, hand_l):
            pygame.draw.circle(surf, body, hand, 5)
            pygame.draw.circle(surf, outline, hand, 5, 2)
        if self.stuck_timer > 0:
            pygame.draw.circle(surf, METAL, pos, 19, 2)

    def draw_tag(self, surf, font):
        # drawn last so walls and darkness never hide who's who
        if self.down:
            return
        pos = self.pixel_pos()
        label = font.render(self.pid, True, BLACK)
        tag = pygame.Rect(0, 0, label.get_width() + 10, label.get_height() + 2)
        tag.center = (pos.x, pos.y - 27)
        pygame.draw.rect(surf, self.color, tag, border_radius=7)
        surf.blit(label, (tag.x + 5, tag.y + 1))


# Zombie - EnemyFSM
# 3 states:
#   WANDER - walks to a random cell using A*
#   CHASE  - follows an A* path to where it last saw/heard the player
#   ATTACK - player is one open step away, hits them once per cooldown
# Transitions (same as our design doc table):
#   WANDER -> CHASE   player is in its view cone (or sprinting within 8 cells)
#   CHASE  -> ATTACK  target is right next to it with no wall in between
#   ATTACK -> CHASE   target stepped out of reach, go after their cell
#   CHASE  -> WANDER  reached the last known cell and still can't detect anyone
#   any    -> WANDER  target went down
class Zombie(GridMover):

    def __init__(self, cell, floor, rng):
        super().__init__(cell)
        self.rng = rng
        self.max_hp = ZOMBIE_HP + (floor - 1) // 3
        self.hp = self.max_hp
        self.base_step = rng.uniform(0.30, 0.46) * (0.96 ** (floor - 1))
        self.state = "WANDER"
        self.target = None          # which player it's after
        self.last_known = None      # last cell it saw/heard the target in
        self.path = []
        self.attack_timer = 0.0
        self.wait_timer = 0.0
        self.hurt_timer = 0.0
        self.swing = 0.0
        self.facing = rng.choice(DIRECTIONS)
        self.draw_angle = DIR_ANGLE[self.facing]
        self.wobble = rng.uniform(0, 6.28)
        self.radius = 12

    def forward(self):
        return pygame.Vector2(DIR_VEC[self.facing])

    def face_toward(self, point):
        diff = point - self.pixel_pos()
        if abs(diff.x) > abs(diff.y):
            self.facing = "RIGHT" if diff.x > 0 else "LEFT"
        elif diff.length() > 0:
            self.facing = "DOWN" if diff.y > 0 else "UP"

    def can_detect(self, player, maze):
        if player.down:
            return False
        to_player = player.pixel_pos() - self.pixel_pos()
        dist = to_player.length()
        # sprinting is loud
        if player.sprinting and dist <= HEAR_RANGE * CELL:
            return True
        # otherwise it has to SEE you: close enough...
        if dist > ZOMBIE_VIEW * CELL:
            return False
        if dist < 1:
            return True
        # ...in front of it (dot product vs its facing direction)...
        if self.forward().dot(to_player.normalize()) < ZOMBIE_FOV_DOT:
            return False
        # ...and no wall in the way
        return maze.segment_clear(self.pixel_pos(), player.pixel_pos())

    def update(self, dt, game):
        self.attack_timer -= dt
        self.hurt_timer -= dt
        self.swing = max(0.0, self.swing - dt * 4)
        if self.update_step(dt) and self.path and self.path[0] == self.cell:
            self.path.pop(0)

        # PERCEPTION - which players can it see or hear right now
        detected = [p for p in game.living_players() if self.can_detect(p, game.maze)]

        # DECISION - pick the next state
        # any -> WANDER: target is down, nothing left to chase
        if self.state in ("CHASE", "ATTACK") and (self.target is None or self.target.down):
            self.state, self.target, self.path = "WANDER", None, []
        # WANDER -> CHASE: lock onto the closest player it detected and turn to face them
        if self.state == "WANDER" and detected:
            self.target = min(detected, key=lambda p: p.pixel_pos().distance_to(self.pixel_pos()))
            self.last_known = self.target.cell
            self.state = "CHASE"
            self.path = []
            self.face_toward(self.target.pixel_pos())
        if self.state == "CHASE":
            # still sees/hears them -> update the last known cell and repath
            if self.target in detected and self.target.cell != self.last_known:
                self.last_known = self.target.cell
                self.path = []
            # CHASE -> ATTACK: one open step away
            if game.adjacent(self, self.target):
                self.state = "ATTACK"
            elif self.cell == self.last_known and not self.moving and self.target not in detected:
                # CHASE -> WANDER: got to the last known cell and nobody's here
                self.state, self.target, self.path = "WANDER", None, []
        # ATTACK -> CHASE: they got away, follow them to their cell
        elif self.state == "ATTACK" and not game.adjacent(self, self.target):
            self.state = "CHASE"
            self.last_known = self.target.cell
            self.path = []

        # ACTION - do whatever the current state says
        if self.state == "ATTACK":
            # stand still, face them, hit once per cooldown
            if not self.moving:
                self.face_toward(self.target.pixel_pos())
            if self.attack_timer <= 0:
                self.target.take_damage(ZOMBIE_DAMAGE, game)
                self.attack_timer = ZOMBIE_COOLDOWN
                self.swing = 1.0
        elif self.state == "CHASE":
            # A* to the last known cell (repaths whenever that cell changes)
            if not self.path:
                route = game.pathfinder.find_path(self.plan_cell, self.last_known)
                self.path = route[1:] if route else []
            self.follow_path(dt, game, self.base_step)
        else:
            # WANDER: pick a random cell, A* there, slower walk, repeat
            if not self.path and not self.moving:
                goal = self.rng.choice(game.maze.cells)
                route = game.pathfinder.find_path(self.cell, goal)
                self.path = route[1:] if route else []
            self.follow_path(dt, game, self.base_step * 1.4)

        target_angle = DIR_ANGLE[self.facing]
        self.draw_angle = turn_toward(self.draw_angle, target_angle, 500 * dt)
        if self.moving:
            self.wobble += dt * 9

    def draw(self, surf, t):
        pos = self.pixel_pos()
        angle = self.draw_angle + (math.sin(self.wobble) * 12 if self.moving else 0)
        forward = pygame.Vector2(1, 0).rotate(angle)
        side = forward.rotate(90)
        body = (230, 255, 230) if self.hurt_timer > 0 else ZOMBIE_GREEN
        reach = 15 + self.swing * 6
        pygame.draw.ellipse(surf, (20, 18, 26), (pos.x - 12, pos.y + 6, 24, 9))
        pygame.draw.circle(surf, body, pos, self.radius)
        pygame.draw.circle(surf, ZOMBIE_DARK, pos, self.radius, 2)
        for s in (-1, 1):
            hand = pos + forward * reach + side * 7 * s
            pygame.draw.circle(surf, body, hand, 4)
            pygame.draw.circle(surf, ZOMBIE_DARK, hand, 4, 1)
            eye = pos + forward * 6 + side * 4 * s
            pygame.draw.circle(surf, (230, 40, 40), eye, 2)
        if self.hp < self.max_hp:
            w = 24
            pygame.draw.rect(surf, (60, 20, 20), (pos.x - w / 2, pos.y - 21, w, 4))
            pygame.draw.rect(surf, HEALTH, (pos.x - w / 2, pos.y - 21, w * self.hp / self.max_hp, 4))


# Boss - BossAI (predictive ambush)
class Boss(GridMover):

    def __init__(self, cell, floor):
        super().__init__(cell)
        self.max_hp = 12 + 6 * floor
        self.hp = self.max_hp
        self.mode = "CHASE"
        self.target = None
        self.path = []
        self.candidates = []
        self.chosen = None
        self.utility = None
        self.replan_timer = 0.0
        self.attack_timer = 0.0
        self.wait_timer = 0.0
        self.hurt_timer = 0.0
        self.swing = 0.0
        self.draw_angle = 90
        self.wobble = 0.0
        self.radius = 20
        self.step = BOSS_STEP * (0.97 ** (floor - 1))

    def update(self, dt, game):
        self.attack_timer -= dt
        self.hurt_timer -= dt
        self.replan_timer -= dt
        self.swing = max(0.0, self.swing - dt * 3)
        if self.update_step(dt) and self.path and self.path[0] == self.cell:
            self.path.pop(0)

        if not game.living_players():
            self.mode, self.path, self.target = "WAITING", [], None
            return

        if self.target is None or self.target.down:
            self.replan_timer = 0

        if self.mode != "ATTACK" and self.target and not self.moving and game.adjacent(self, self.target):
            self.mode, self.path = "ATTACK", []

        if self.mode == "ATTACK":
            if game.adjacent(self, self.target):
                self.facing = direction_between(self.cell, self.target.cell) or self.facing
                if self.attack_timer <= 0:
                    self.target.take_damage(BOSS_DAMAGE, game)
                    self.attack_timer = BOSS_COOLDOWN
                    self.swing = 1.0
                self.draw_angle = turn_toward(self.draw_angle, DIR_ANGLE[self.facing], 400 * dt)
                return
            self.replan_timer = 0

        if self.replan_timer <= 0 or (not self.path and self.mode in ("CHASE", "POSITIONING")):
            self.plan(game)
            self.replan_timer = BOSS_REPLAN

        if self.path:
            self.follow_path(dt, game, self.step)
        elif self.mode == "POSITIONING" and not self.moving:
            self.mode = "WAITING"

        self.draw_angle = turn_toward(self.draw_angle, DIR_ANGLE[self.facing], 400 * dt)
        if self.moving:
            self.wobble += dt * 7

    def plan(self, game):
        start = self.plan_cell
        # target = the living player the boss can reach in the fewest steps
        best_len = None
        for p in game.living_players():
            route = game.pathfinder.find_path(start, p.cell)
            if route and (best_len is None or len(route) < best_len):
                best_len, self.target = len(route), p
        if self.target is None:
            return
        if not self.moving and game.adjacent(self, self.target):
            self.mode, self.path = "ATTACK", []
            return

        pid = self.target.pid
        last = game.predictor.stats[pid]["last"]
        tiles = game.predictor.project(pid, self.target.plan_cell, last, game.maze, LOOKAHEAD)
        time_limit = LOOKAHEAD * PLAYER_STEP * REACH_SLACK

        self.candidates = []
        best = None
        for cell, chance in tiles.items():
            if chance <= 0:
                continue
            route = game.pathfinder.find_path(start, cell)
            if route is None:
                continue
            d = len(route) - 1
            score = 10 * chance - 0.5 * d          # U = 10P - 0.5D
            in_time = d * self.step <= time_limit
            entry = {"cell": cell, "p": chance, "d": d, "u": score, "ok": in_time}
            self.candidates.append(entry)
            if in_time and (best is None or score > best["u"]):
                best = dict(entry, route=route)

        if best:
            self.chosen, self.utility = best["cell"], best["u"]
            self.path = best["route"][1:]
            self.mode = "WAITING" if best["d"] == 0 else "POSITIONING"
        else:
            # can't cut them off in time -> just chase
            self.chosen, self.utility = None, None
            route = game.pathfinder.find_path(start, self.target.plan_cell)
            self.path = route[1:] if route else []
            self.mode = "CHASE"

    def draw(self, surf, font, t):
        pos = self.pixel_pos()
        angle = self.draw_angle + (math.sin(self.wobble) * 7 if self.moving else 0)
        forward = pygame.Vector2(1, 0).rotate(angle)
        side = forward.rotate(90)

        def local(f, s):
            return pos + forward * f + side * s

        pygame.draw.ellipse(surf, (16, 12, 20), (pos.x - 21, pos.y + 10, 42, 13))
        # torn red coat
        coat = [local(8, -15), local(-6, -21), local(-18, -19), local(-14, -12), local(-22, -8),
                local(-16, -2), local(-24, 3), local(-15, 9), local(-21, 15), local(-8, 21), local(8, 15)]
        coat_color = (255, 120, 120) if self.hurt_timer > 0 else BOSS_COAT
        pygame.draw.polygon(surf, coat_color, coat)
        pygame.draw.polygon(surf, BOSS_COAT_DARK, coat, 2)
        pygame.draw.line(surf, BOSS_COAT_DARK, local(-4, -10), local(-14, -6), 2)
        pygame.draw.line(surf, BOSS_COAT_DARK, local(-2, 8), local(-12, 12), 2)
        # arms
        reach = 22 + self.swing * 8
        for s in (-1, 1):
            shoulder = local(4, 14 * s)
            hand = local(reach, 12 * s)
            pygame.draw.line(surf, BOSS_COAT_DARK, shoulder, hand, 8)
            pygame.draw.circle(surf, ZOMBIE_GREEN, hand, 6)
            pygame.draw.circle(surf, ZOMBIE_DARK, hand, 6, 2)
        head = local(3, 0)
        pygame.draw.circle(surf, ZOMBIE_GREEN, head, 11)
        pygame.draw.circle(surf, ZOMBIE_DARK, head, 11, 2)
        for s in (-1, 1):
            pygame.draw.circle(surf, YELLOW, head + forward * 5 + side * 4 * s, 2)
        # health bar
        w = 50
        pygame.draw.rect(surf, (40, 10, 14), (pos.x - w / 2 - 1, pos.y - 35, w + 2, 7))
        pygame.draw.rect(surf, (230, 50, 60), (pos.x - w / 2, pos.y - 34, w * max(0, self.hp) / self.max_hp, 5))
        label = font.render("BOSS", True, (255, 150, 150))
        surf.blit(label, (pos.x - label.get_width() / 2, pos.y - 48))


# Effects
class Bullet:

    def __init__(self, pos, velocity, damage, color, owner):
        self.pos = pygame.Vector2(pos)
        self.prev = owner.pixel_pos()      # so point-blank shots still count
        self.velocity = velocity
        self.damage = damage
        self.color = color
        self.owner = owner
        self.alive = True

    def update(self, dt, maze):
        # two small steps so fast bullets can't skip a wall
        for _ in range(2):
            old_cell = pixel_to_cell(self.pos)
            self.pos += self.velocity * dt / 2
            new_cell = pixel_to_cell(self.pos)
            if maze.crossing_blocked(old_cell, new_cell):
                self.alive = False
                return

    def hits(self, center, radius):
        # closest distance from the circle to the line the bullet travelled this frame
        a, b = self.prev, self.pos
        ab = b - a
        if ab.length_squared() == 0:
            return a.distance_to(center) < radius
        t = max(0.0, min(1.0, (center - a).dot(ab) / ab.length_squared()))
        return (a + ab * t).distance_to(center) < radius

    def draw(self, surf):
        tail = self.pos - self.velocity.normalize() * 10
        pygame.draw.line(surf, self.color, tail, self.pos, 3)
        pygame.draw.circle(surf, WHITE, self.pos, 2)


class Particle:

    def __init__(self, pos, velocity, color, life, size):
        self.pos = pygame.Vector2(pos)
        self.velocity = pygame.Vector2(velocity)
        self.color = color
        self.life = life
        self.max_life = life
        self.size = size

    def update(self, dt):
        self.pos += self.velocity * dt
        self.velocity *= 0.9
        self.life -= dt

    def draw(self, surf):
        r = max(1, int(self.size * self.life / self.max_life))
        pygame.draw.circle(surf, self.color, self.pos, r)


# Rendering helpers
def render_floor(maze, rng, webs=()):
    surf = pygame.Surface((maze.cols * CELL, maze.rows * CELL))
    surf.fill(GROUT)
    for c, r in maze.cells:
        base = FLOOR_A if (c + r) % 2 == 0 else FLOOR_B
        shade = rng.randint(-5, 5)
        color = tuple(max(0, min(255, v + shade)) for v in base)
        rect = pygame.Rect(c * CELL + 1, r * CELL + 1, CELL - 2, CELL - 2)
        pygame.draw.rect(surf, color, rect)
        for _ in range(4):
            x = rect.x + rng.randint(2, CELL - 6)
            y = rect.y + rng.randint(2, CELL - 6)
            pygame.draw.circle(surf, tuple(v - 10 for v in color), (x, y), rng.randint(1, 2))
        if rng.random() < 0.15:
            x, y = rect.x + rng.randint(6, 30), rect.y + rng.randint(6, 30)
            pygame.draw.lines(surf, tuple(v - 18 for v in color), False,
                              [(x, y), (x + rng.randint(4, 10), y + rng.randint(-4, 6)),
                               (x + rng.randint(10, 16), y + rng.randint(2, 10))], 1)
    for cell in webs:
        draw_web(surf, cell_center(cell))
    return surf


def render_walls(maze):
    surf = pygame.Surface((maze.cols * CELL, maze.rows * CELL), pygame.SRCALPHA)
    segments = []
    for c, r in maze.cells:
        x, y = c * CELL, r * CELL
        if maze.walls[r][c]["UP"]:
            segments.append(((x, y), (x + CELL, y)))
        if maze.walls[r][c]["LEFT"]:
            segments.append(((x, y), (x, y + CELL)))
        if r == maze.rows - 1 and maze.walls[r][c]["DOWN"]:
            segments.append(((x, y + CELL), (x + CELL, y + CELL)))
        if c == maze.cols - 1 and maze.walls[r][c]["RIGHT"]:
            segments.append(((x + CELL, y), (x + CELL, y + CELL)))
    for (a, b) in segments:
        pygame.draw.line(surf, WALL_SHADOW, (a[0] + 2, a[1] + 4), (b[0] + 2, b[1] + 4), 9)
    for (a, b) in segments:
        pygame.draw.line(surf, WALL, a, b, 9)
        pygame.draw.circle(surf, WALL, a, 4)
        pygame.draw.circle(surf, WALL, b, 4)
    for (a, b) in segments:
        pygame.draw.line(surf, WALL_LIGHT, (a[0], a[1] - 2), (b[0], b[1] - 2), 2)
    return surf


def make_light(radius, alpha):
    surf = pygame.Surface((radius * 2, radius * 2), pygame.SRCALPHA)
    surf.fill((*DARK_RGB, alpha))
    for r in range(radius, 0, -3):
        t = r / radius
        a = int(alpha * max(0.0, (t - 0.35) / 0.65) ** 1.5)
        pygame.draw.circle(surf, (*DARK_RGB, a), (radius, radius), r)
    return surf


class Button:

    def __init__(self, rect, text, color=(90, 50, 130)):
        self.rect = pygame.Rect(rect)
        self.text = text
        self.color = color

    def draw(self, screen, font, mouse, enabled=True, selected=False):
        hover = self.rect.collidepoint(mouse) and enabled
        color = self.color
        if selected:
            color = tuple(min(255, c + 60) for c in self.color)
        elif hover:
            color = tuple(min(255, c + 30) for c in self.color)
        if not enabled:
            color = (50, 46, 56)
        pygame.draw.rect(screen, (8, 4, 12), self.rect.move(0, 3), border_radius=8)
        pygame.draw.rect(screen, color, self.rect, border_radius=8)
        pygame.draw.rect(screen, (220, 200, 240) if selected else (30, 18, 40), self.rect, 2, border_radius=8)
        label = font.render(self.text, True, TEXT if enabled else DIM_TEXT)
        screen.blit(label, label.get_rect(center=self.rect.center))

    def clicked(self, pos):
        return self.rect.collidepoint(pos)


# DebugOverlay (F1)
class DebugOverlay:

    def draw_world(self, game, surf):
        font = game.font_tiny
        # boss ambush candidates
        boss = game.boss
        if boss:
            tile_surf = pygame.Surface((CELL, CELL), pygame.SRCALPHA)
            for c in boss.candidates:
                rect = pygame.Rect(c["cell"][0] * CELL, c["cell"][1] * CELL, CELL, CELL)
                chosen = c["cell"] == boss.chosen
                tile_surf.fill((255, 225, 60, 130) if chosen else (255, 150, 40, 70 if c["ok"] else 30))
                surf.blit(tile_surf, rect)
                pygame.draw.rect(surf, YELLOW if chosen else ORANGE, rect, 2 if chosen else 1)
                surf.blit(font.render(f"P{c['p']:.2f}", True, WHITE), (rect.x + 3, rect.y + 3))
                surf.blit(font.render(f"U{c['u']:.1f}", True, WHITE), (rect.x + 3, rect.y + 16))
                surf.blit(font.render(f"D{c['d']}", True, WHITE), (rect.x + 3, rect.y + 29))
            self.draw_path(surf, boss, RED_PATH, 4)
            pos = boss.pixel_pos()
            who = boss.target.pid if boss.target else "-"
            label = game.font_small.render(f"BOSS -> {who}: {boss.mode}", True, (255, 160, 160))
            box = label.get_rect(midtop=(pos.x, pos.y + 24)).inflate(8, 4)
            pygame.draw.rect(surf, (20, 0, 0), box, border_radius=4)
            surf.blit(label, label.get_rect(center=box.center))

        for z in game.zombies:
            pos = z.pixel_pos()
            pygame.draw.circle(surf, (120, 255, 120), pos, ZOMBIE_VIEW * CELL, 1)
            forward = z.forward()
            half = math.degrees(math.acos(ZOMBIE_FOV_DOT))
            for side in (-half, half):
                pygame.draw.line(surf, (120, 255, 120), pos, pos + forward.rotate(side) * ZOMBIE_VIEW * CELL, 1)
            self.draw_path(surf, z, PURPLE_PATH, 3)
            who = z.target.pid if z.target else "-"
            label = font.render(f"{z.state} {who}", True, WHITE)
            box = label.get_rect(midtop=(pos.x, pos.y + 16)).inflate(6, 2)
            pygame.draw.rect(surf, (30, 10, 40), box, border_radius=3)
            surf.blit(label, label.get_rect(center=box.center))

    def draw_path(self, surf, mover, color, width):
        if not mover.path:
            return
        points = [mover.pixel_pos()] + [cell_center(c) for c in mover.path]
        if len(points) > 1:
            pygame.draw.lines(surf, color, False, points, width)
        for p in points[1:]:
            pygame.draw.circle(surf, color, p, 3)

    def draw_text(self, game, screen):
        # plain white telemetry in the top left
        lines = []
        for p in game.players:
            s = game.predictor.stats[p.pid]
            acc = game.predictor.accuracy(p.pid)
            acc_text = "N/A" if acc is None else f"{acc:.0f}%"
            guess = f"{s['guess']} ({s['guess_p'] * 100:.0f}%)" if s["guess"] else "-"
            lines.append(f"{p.pid}  last move: {s['last'] or '-'}   predicted: {guess}   "
                         f"transitions: {s['transitions']}   accuracy: {acc_text}")
        boss = game.boss
        if boss:
            who = boss.target.pid if boss.target else "-"
            dest = boss.chosen if boss.chosen else (boss.path[-1] if boss.path else boss.cell)
            util = "-" if boss.utility is None else f"{boss.utility:.2f}"
            lines.append(f"BOSS -> {who}: {boss.mode}   destination: {dest}   path length: {len(boss.path)}   utility: {util}")
        else:
            lines.append("BOSS: defeated")
        lines.append(f"FPS: {game.clock.get_fps():.0f}")
        y = 8
        for line in lines:
            label = game.font_small.render(line, True, WHITE)
            screen.blit(game.font_small.render(line, True, BLACK), (MAZE_X + 11, y + 1))
            screen.blit(label, (MAZE_X + 10, y))
            y += 18


# Game
class Game:

    def __init__(self):
        self.screen = pygame.display.set_mode((SCREEN_W, SCREEN_H))
        pygame.display.set_caption("Zombs 2.0")
        self.clock = pygame.time.Clock()
        self.font_title = pygame.font.Font(None, 120)
        self.font_big = pygame.font.Font(None, 64)
        self.font_med = pygame.font.Font(None, 30)
        self.font_reg = pygame.font.Font(None, 26)
        self.font_small = pygame.font.Font(None, 20)
        self.font_tiny = pygame.font.Font(None, 16)
        self.sounds = SoundBank()
        self.debug_overlay = DebugOverlay()
        self.light = make_light(210, 190)
        self.small_light = make_light(90, 190)
        self.dark = pygame.Surface((MAZE_W, MAZE_H), pygame.SRCALPHA)
        self.world = pygame.Surface((MAZE_W, MAZE_H))

        self.running = True
        self.scene = "TITLE"
        self.debug = False
        self.probs = dict(DEFAULT_PROBS)
        self.num_players = 2
        self.time = 0.0
        self.held = {"P1": [], "P2": []}

        # title screen background
        bg_rng = random.Random(99)
        bg_maze = MazeGenerator(COLS + 7, ROWS + 1, bg_rng)
        self.title_bg = render_floor(bg_maze, bg_rng)
        self.title_bg.blit(render_walls(bg_maze), (0, 0))
        shade = pygame.Surface(self.title_bg.get_size(), pygame.SRCALPHA)
        shade.fill((10, 4, 18, 200))
        self.title_bg.blit(shade, (0, 0))

        # title buttons
        self.prob_buttons = {}
        for i, key in enumerate(DEFAULT_PROBS):
            y = 270 + i * 54
            self.prob_buttons[key] = (Button((462, y, 40, 36), "-"), Button((512, y, 40, 36), "+"))
        self.reset_button = Button((448, 492, 120, 32), "Defaults", (60, 50, 80))
        self.one_player_button = Button((410, 572, 210, 50), "1 PLAYER")
        self.two_player_button = Button((660, 572, 210, 50), "2 PLAYERS")
        self.start_button = Button((515, 638, 250, 62), "START", (40, 130, 60))

    # Setup
    def start_run(self):
        self.floor = 1
        self.score = 0
        pids = ["P1", "P2"][:self.num_players]
        self.predictor = MarkovPredictor(pids)
        carry = {pid: {} for pid in pids}
        self.load_floor(random.randrange(10 ** 9), carry)
        self.scene = "PLAYING"

    def load_floor(self, seed, carry):
        self.floor_seed = seed
        self.floor_carry = {pid: dict(c) for pid, c in carry.items()}
        self.floor_start_score = self.score
        rng = random.Random(seed)
        self.rng = rng
        self.maze = MazeGenerator(COLS, ROWS, rng)
        self.pathfinder = Pathfinder(self.maze)

        # players spawn randomly
        cells = list(self.maze.cells)
        spawns = rng.sample(cells, len(carry))
        colors = {"P1": P1_COLOR, "P2": P2_COLOR}
        self.players = [Player(pid, spawns[i], colors[pid], carry[pid]) for i, pid in enumerate(carry)]
        for p in self.players:
            p.facing = rng.choice(self.maze.open_directions(p.cell))
            p.draw_angle = DIR_ANGLE[p.facing]

        dists = [self.maze.distances_from(p.cell) for p in self.players]

        def min_dist(cell):
            return min(d[cell] for d in dists)

        self.exit_cell = max(cells, key=min_dist)
        reserved = {p.cell for p in self.players} | {self.exit_cell}

        zombie_count = min(3 + 2 * self.floor, 15)
        far = [c for c in cells if min_dist(c) >= 6 and c not in reserved]
        if len(far) < zombie_count + 1:
            far = [c for c in cells if min_dist(c) >= 3 and c not in reserved]
        rng.shuffle(far)
        boss_options = [c for c in far if min_dist(c) >= 8] or far
        boss_cell = boss_options[0]
        zombie_cells = [c for c in far if c != boss_cell][:zombie_count]
        self.boss = Boss(boss_cell, self.floor)
        self.zombies = [Zombie(c, self.floor, rng) for c in zombie_cells]
        reserved |= set(zombie_cells) | {boss_cell}

        # roll once for every free cell
        gun_p = self.probs["gun"] / self.floor
        potion_p = self.probs["potion"] / self.floor
        webs_p = self.probs["webs"]
        trap_p = self.probs["trap"]
        self.items = {}
        for cell in cells:
            if cell in reserved:
                continue
            roll = rng.random() * 100
            if roll < gun_p:
                kind = "gun"
            elif roll < gun_p + potion_p:
                kind = "potion"
            elif roll < gun_p + potion_p + webs_p:
                kind = "webs"
            elif roll < gun_p + potion_p + webs_p + trap_p:
                kind = "trap"
            else:
                continue
            self.items[cell] = Item(kind, cell)

        webs = [c for c, item in self.items.items() if item.kind == "webs"]
        self.floor_surface = render_floor(self.maze, random.Random(seed + 1), webs)
        self.wall_surface = render_walls(self.maze)
        self.decals = pygame.Surface((MAZE_W, MAZE_H), pygame.SRCALPHA)
        self.candy = {}
        self.bullets = []
        self.particles = []
        self.texts = []
        self.door_open = False
        self.shake = 0.0
        self.banner_text = f"FLOOR {self.floor}"
        self.banner_timer = 2.0
        self.held = {"P1": [], "P2": []}

    def carry_stats(self):
        carry = {}
        for p in self.players:
            health = p.health if not p.down else MAX_HEALTH // 2
            carry[p.pid] = {"health": health, "stamina": MAX_STAMINA, "gun": p.gun_level}
        return carry

    def restart_floor(self):
        self.score = self.floor_start_score
        self.load_floor(self.floor_seed, self.floor_carry)
        self.scene = "PLAYING"

    def next_floor(self):
        carry = self.carry_stats()
        self.floor += 1
        self.load_floor(random.randrange(10 ** 9), carry)
        self.scene = "PLAYING"

    # Helpers
    def living_players(self):
        return [p for p in self.players if not p.down]

    def adjacent(self, mover, player):
        if player is None or player.down:
            return False
        if mover.pixel_pos().distance_to(player.pixel_pos()) > CELL * 1.35:
            return False
        if mover.cell == player.cell:
            return True
        d = direction_between(mover.cell, player.cell)
        return d is not None and self.maze.can_move(mover.cell, d)

    def enemy_at(self, cell):
        others = self.zombies + ([self.boss] if self.boss else [])
        return any(o.cell == cell or o.plan_cell == cell for o in others)

    def cell_taken(self, mover, cell):
        others = self.zombies + ([self.boss] if self.boss else [])
        return any(o is not mover and (o.cell == cell or o.plan_cell == cell) for o in others)

    def float_text(self, text, pos, color):
        self.texts.append([text, pygame.Vector2(pos), color, 1.0])

    def banner(self, text, time=2.5):
        self.banner_text = text
        self.banner_timer = time

    def burst(self, pos, color, count, speed, life=0.5, size=4):
        for _ in range(count):
            angle = random.uniform(0, math.tau)
            vel = pygame.Vector2(math.cos(angle), math.sin(angle)) * random.uniform(speed * 0.3, speed)
            self.particles.append(Particle(pos, vel, color, random.uniform(life * 0.5, life), size))

    def splat(self, pos, color, size):
        for _ in range(7):
            offset = pygame.Vector2(random.uniform(-1, 1), random.uniform(-1, 1)) * size
            pygame.draw.circle(self.decals, (*color, 150), pos + offset, random.randint(3, max(4, size // 2)))

    # Actions
    def fire(self, player):
        gun = player.gun
        base = DIR_ANGLE[player.facing]
        forward = pygame.Vector2(1, 0).rotate(base)
        side = forward.rotate(90)
        muzzle = player.pixel_pos() + forward * 24 + side * 9
        # bullets start inside the player's own cell so they can't skip through a wall
        start = player.pixel_pos() + forward * 10 + side * 9
        color = (150, 210, 255) if player.pid == "P1" else (255, 235, 140)
        for i in range(gun["pellets"]):
            if gun["pellets"] > 1:
                angle = base - gun["spread"] + 2 * gun["spread"] * i / (gun["pellets"] - 1)
            else:
                angle = base + random.uniform(-gun["spread"], gun["spread"])
            velocity = pygame.Vector2(1, 0).rotate(angle) * BULLET_SPEED
            self.bullets.append(Bullet(start, velocity, gun["damage"], color, player))
        if not self.maze.crossing_blocked(player.cell, pixel_to_cell(muzzle)):
            self.burst(muzzle, (255, 230, 150), 4, 120, 0.12, 3)
        self.sounds.play("shoot")

    def on_player_arrive(self, player):
        cell = player.cell
        item = self.items.get(cell)
        pos = player.pixel_pos()
        if item:
            if item.kind == "trap" and not item.sprung:
                item.sprung = True
                player.stuck_timer = TRAP_TIME
                self.sounds.play("snap")
                self.float_text("TRAPPED!", pos, (220, 220, 230))
                self.shake = max(self.shake, 4)
            elif item.kind == "gun":
                del self.items[cell]
                if player.gun_level < len(GUNS) - 1:
                    player.gun_level += 1
                    self.float_text(f"NEW GUN: {player.gun['name']}!", pos, YELLOW)
                else:
                    self.score += 50
                    self.float_text("+50 (max gun)", pos, YELLOW)
                self.sounds.play("chime")
                self.burst(pos, (255, 220, 120), 14, 160)
            elif item.kind == "potion":
                del self.items[cell]
                if random.random() < 0.5:
                    player.health = min(MAX_HEALTH, player.health + 40)
                    self.float_text("+40 HEALTH", pos, HEALTH)
                else:
                    player.stamina = MAX_STAMINA
                    player.exhausted = False
                    self.float_text("STAMINA RESET", pos, STAMINA)
                self.sounds.play("chime")
                self.burst(pos, (255, 120, 200), 14, 160)
            elif item.kind == "webs":
                self.float_text("webbed...", pos, (220, 220, 230))
        if self.door_open and cell == self.exit_cell:
            self.sounds.play("chime")
            self.scene = "TRANSITION"
            self.transition_timer = 1.8

    def kill_zombie(self, z, is_boss=False):
        pos = z.pixel_pos()
        count = 3 if is_boss else 1
        pile = self.candy.setdefault(z.cell, [])
        for _ in range(count):
            pile.append(random.choice(CANDY_COLORS))
        self.splat(pos, (70, 140, 50) if not is_boss else (140, 20, 30), 22 if is_boss else 14)
        self.burst(pos, ZOMBIE_GREEN, 26 if is_boss else 12, 220, 0.6, 5)
        self.sounds.play("die")
        if is_boss:
            self.score += 300
            self.boss = None
            self.banner("THE BOSS IS DOWN!")
            self.shake = 12
        else:
            self.score += 50
            self.zombies.remove(z)

    # Input
    def keymap(self):
        if self.num_players == 1:
            return {"P1": {"UP": [pygame.K_w, pygame.K_UP], "DOWN": [pygame.K_s, pygame.K_DOWN],
                           "LEFT": [pygame.K_a, pygame.K_LEFT], "RIGHT": [pygame.K_d, pygame.K_RIGHT],
                           "shoot": [pygame.K_SPACE, pygame.K_RETURN, pygame.K_KP_ENTER],
                           "sprint": [pygame.K_LSHIFT, pygame.K_RSHIFT]}}
        return {"P1": {"UP": [pygame.K_w], "DOWN": [pygame.K_s], "LEFT": [pygame.K_a], "RIGHT": [pygame.K_d],
                       "shoot": [pygame.K_SPACE], "sprint": [pygame.K_LSHIFT]},
                "P2": {"UP": [pygame.K_UP], "DOWN": [pygame.K_DOWN], "LEFT": [pygame.K_LEFT],
                       "RIGHT": [pygame.K_RIGHT], "shoot": [pygame.K_RETURN, pygame.K_KP_ENTER],
                       "sprint": [pygame.K_RSHIFT]}}

    def get_intents(self):
        pressed = pygame.key.get_pressed()
        intents = {}
        for pid, keys in self.keymap().items():
            held = [d for d in self.held[pid] if any(pressed[k] for k in keys[d])]
            self.held[pid] = held
            direction = held[-1] if held else None     # the most recently pressed direction wins
            shoot = any(pressed[k] for k in keys["shoot"])
            sprint = any(pressed[k] for k in keys["sprint"])
            intents[pid] = (direction, shoot, sprint)
        return intents

    def handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            elif event.type == pygame.KEYDOWN:
                self.on_key(event.key)
            elif event.type == pygame.KEYUP and self.scene == "PLAYING":
                for pid, keys in self.keymap().items():
                    for d in DIRECTIONS:
                        if event.key in keys[d] and d in self.held[pid]:
                            self.held[pid].remove(d)
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                self.on_click(event.pos)

    def on_key(self, key):
        if self.scene == "TITLE":
            if key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self.start_run()
            elif key == pygame.K_ESCAPE and not WEB:
                self.running = False
            return
        if key in (pygame.K_ESCAPE, pygame.K_q):
            self.scene = "TITLE"
            return
        if key == pygame.K_F1:
            self.debug = not self.debug
        if key == pygame.K_r and self.scene in ("PLAYING", "FLOOR_OVER"):
            self.restart_floor()
            return
        if self.scene == "PLAYING":
            for pid, keys in self.keymap().items():
                for d in DIRECTIONS:
                    if key in keys[d]:
                        if d in self.held[pid]:
                            self.held[pid].remove(d)
                        self.held[pid].append(d)

    def on_click(self, pos):
        if self.scene == "TITLE":
            for key, (minus, plus) in self.prob_buttons.items():
                if minus.clicked(pos) and self.probs[key] > 0:
                    self.probs[key] -= 1
                if plus.clicked(pos) and sum(self.probs.values()) < 100:
                    self.probs[key] += 1
            if self.reset_button.clicked(pos):
                self.probs = dict(DEFAULT_PROBS)
            if self.one_player_button.clicked(pos):
                self.num_players = 1
            if self.two_player_button.clicked(pos):
                self.num_players = 2
            if self.start_button.clicked(pos):
                self.start_run()

    # Update
    def update(self, dt):
        self.time += dt
        if self.scene == "TRANSITION":
            self.transition_timer -= dt
            if self.transition_timer <= 0:
                self.next_floor()
            return
        if self.scene != "PLAYING":
            return

        intents = self.get_intents()
        for p in self.players:
            p.update(dt, intents.get(p.pid, (None, False, False)), self)
            if self.scene != "PLAYING":
                return
            pile = self.candy.get(p.cell)
            if pile and not p.down:
                for _ in pile:
                    self.score += 10
                    p.health = min(MAX_HEALTH, p.health + 5)
                self.float_text(f"+{10 * len(pile)} candy", p.pixel_pos(), (255, 150, 200))
                self.sounds.play("candy")
                del self.candy[p.cell]

        for z in list(self.zombies):
            z.update(dt, self)
        if self.boss:
            self.boss.update(dt, self)

        for b in self.bullets:
            b.update(dt, self.maze)
            if not b.alive:
                self.burst(b.pos, (200, 170, 230), 3, 80, 0.15, 2)    # sparks on the wall
            targets = list(self.zombies) + ([self.boss] if self.boss else [])
            for z in targets:
                if b.hits(z.pixel_pos(), z.radius + 3):
                    b.alive = False
                    z.hp -= b.damage
                    z.hurt_timer = 0.1
                    self.burst(b.pos, ZOMBIE_GREEN, 5, 140, 0.3, 3)
                    self.sounds.play("hit")
                    if z.hp <= 0:
                        self.kill_zombie(z, is_boss=(z is self.boss))
                    break
            b.prev = pygame.Vector2(b.pos)
        self.bullets = [b for b in self.bullets if b.alive]

        for part in self.particles:
            part.update(dt)
        self.particles = [p for p in self.particles if p.life > 0]
        for t in self.texts:
            t[1].y -= 30 * dt
            t[3] -= dt
        self.texts = [t for t in self.texts if t[3] > 0]
        self.shake = max(0.0, self.shake - 30 * dt)
        self.banner_timer -= dt

        if not self.door_open and not self.zombies and self.boss is None:
            self.door_open = True
            self.sounds.play("creak")
            self.banner("The door creaks open... get to the EXIT!", 3.5)

        if not self.living_players():
            self.scene = "FLOOR_OVER"

    # Drawing
    def draw(self):
        if self.scene == "TITLE":
            self.draw_title()
            return
        self.draw_world()
        self.draw_hud()
        if self.debug:
            self.debug_overlay.draw_text(self, self.screen)
        if self.scene == "FLOOR_OVER":
            self.draw_floor_over()
        elif self.scene == "TRANSITION":
            fade = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
            fade.fill((0, 0, 0, int(255 * min(1, (1.8 - self.transition_timer) / 1.2))))
            self.screen.blit(fade, (0, 0))
            text = self.font_big.render(f"FLOOR {self.floor} CLEARED", True, (180, 255, 160))
            self.screen.blit(text, text.get_rect(center=(SCREEN_W / 2, SCREEN_H / 2)))

    def draw_world(self):
        w = self.world
        t = self.time
        w.blit(self.floor_surface, (0, 0))
        w.blit(self.decals, (0, 0))
        draw_door(w, self.exit_cell, self.door_open, t)
        for item in self.items.values():
            item.draw(w, t)
        for cell, pile in self.candy.items():
            center = cell_center(cell)
            for i, color in enumerate(pile):
                offset = pygame.Vector2((i - (len(pile) - 1) / 2) * 12, math.sin(t * 3 + i) * 2)
                draw_candy(w, center + offset, color)
        for b in self.bullets:
            b.draw(w)
        for z in self.zombies:
            z.draw(w, t)
        if self.boss:
            self.boss.draw(w, self.font_tiny, t)
        for p in self.players:
            p.draw(w, self.font_small, t)
        for part in self.particles:
            part.draw(w)
        w.blit(self.wall_surface, (0, 0))

        if not self.debug:
            self.dark.fill((*DARK_RGB, 190))
            for p in self.players:
                light = self.small_light if p.down else self.light
                self.dark.blit(light, p.pixel_pos() - pygame.Vector2(light.get_size()) / 2,
                               special_flags=pygame.BLEND_RGBA_MIN)
            if self.door_open:
                self.dark.blit(self.small_light, cell_center(self.exit_cell) - pygame.Vector2(90, 90),
                               special_flags=pygame.BLEND_RGBA_MIN)
            w.blit(self.dark, (0, 0))
        else:
            self.debug_overlay.draw_world(self, w)

        for p in self.players:
            p.draw_tag(w, self.font_small)
        for text, pos, color, life in self.texts:
            label = self.font_small.render(text, True, color)
            w.blit(label, label.get_rect(center=pos))

        if self.banner_timer > 0 and self.scene == "PLAYING":
            label = self.font_med.render(self.banner_text, True, TEXT)
            box = label.get_rect(midtop=(MAZE_W / 2, 14)).inflate(28, 14)
            pygame.draw.rect(w, (20, 10, 30), box, border_radius=10)
            pygame.draw.rect(w, PANEL_EDGE, box, 2, border_radius=10)
            w.blit(label, label.get_rect(center=box.center))

        ox = random.uniform(-self.shake, self.shake) if self.shake > 0 else 0
        oy = random.uniform(-self.shake, self.shake) if self.shake > 0 else 0
        self.screen.fill(BG)
        self.screen.blit(w, (MAZE_X + ox, MAZE_Y + oy))

    def draw_bar(self, x, y, w, h, frac, color, label):
        pygame.draw.rect(self.screen, (40, 32, 50), (x, y, w, h), border_radius=4)
        pygame.draw.rect(self.screen, color, (x, y, int(w * max(0, frac)), h), border_radius=4)
        pygame.draw.rect(self.screen, (10, 6, 14), (x, y, w, h), 1, border_radius=4)
        if label:
            text = self.font_tiny.render(label, True, WHITE)
            self.screen.blit(text, (x + 6, y + (h - text.get_height()) / 2 + 1))

    def draw_box(self, rect, border):
        box = pygame.Surface(rect.size, pygame.SRCALPHA)
        pygame.draw.rect(box, (14, 8, 22, 215), box.get_rect(), border_radius=10)
        self.screen.blit(box, rect.topleft)
        pygame.draw.rect(self.screen, border, rect, 2, border_radius=10)

    def draw_hud(self):
        s = self.screen
        # floor / zombies / score in the top right
        if self.door_open:
            info = f"FLOOR {self.floor}   Door open - get to the EXIT!   Score: {self.score}"
        else:
            boss = " + BOSS" if self.boss else ""
            info = f"FLOOR {self.floor}   Zombies: {len(self.zombies)}{boss}   Score: {self.score}"
        label = self.font_reg.render(info, True, TEXT)
        rect = label.get_rect(topright=(SCREEN_W - 24, 12)).inflate(20, 12)
        self.draw_box(rect, PANEL_EDGE)
        s.blit(label, label.get_rect(center=rect.center))

        # player health in the bottom right
        w, h, gap = 230, 78, 10
        x = SCREEN_W - 24 - len(self.players) * w - (len(self.players) - 1) * gap
        y = SCREEN_H - 12 - h
        for p in self.players:
            rect = pygame.Rect(x, y, w, h)
            name_color = p.color if not p.down else (130, 130, 140)
            self.shadow_text(self.font_reg, p.pid, name_color, (x + 10, y + 7))
            status = p.status(self)
            st_color = (255, 110, 110) if status == "DOWN" else (TEXT if status == "OK" else ORANGE)
            st = self.font_small.render(status, True, st_color)
            self.shadow_text(self.font_small, status, st_color, (rect.right - st.get_width() - 10, y + 10))
            self.draw_bar(x + 10, y + 30, w - 20, 15, p.health / MAX_HEALTH, HEALTH, f"HP {int(p.health)}")
            self.draw_bar(x + 10, y + 49, w - 20, 8, p.stamina / MAX_STAMINA,
                          (110, 110, 130) if p.exhausted else STAMINA, "")
            self.shadow_text(self.font_small, f"Gun: {p.gun['name']}", TEXT, (x + 10, y + 60))
            x += w + gap

    def shadow_text(self, font, text, color, pos):
        # plain text with a dark outline so it reads on any floor
        shadow = font.render(text, True, BLACK)
        for dx, dy in ((1, 1), (-1, 1), (1, -1), (-1, -1), (2, 2)):
            self.screen.blit(shadow, (pos[0] + dx, pos[1] + dy))
        self.screen.blit(font.render(text, True, color), pos)

    def draw_floor_over(self):
        shade = pygame.Surface((SCREEN_W, SCREEN_H), pygame.SRCALPHA)
        shade.fill((10, 0, 8, 190))
        self.screen.blit(shade, (0, 0))
        title = "YOU WENT DOWN" if self.num_players == 1 else "BOTH OF YOU WENT DOWN"
        text = self.font_big.render(title, True, (255, 90, 90))
        self.screen.blit(text, text.get_rect(center=(SCREEN_W / 2, 280)))
        lines = [(f"Floor {self.floor} is over.  Score: {self.score}", TEXT),
                 ("Press R to restart the floor", (180, 255, 160)),
                 ("Press Q to quit to the start screen", (255, 170, 170))]
        for i, (line, color) in enumerate(lines):
            label = self.font_med.render(line, True, color)
            self.screen.blit(label, label.get_rect(center=(SCREEN_W / 2, 350 + i * 40)))

    def draw_title(self):
        s = self.screen
        s.fill(BG)
        drift = (self.time * 8) % CELL
        s.blit(self.title_bg, (-drift, -drift / 2))
        t = self.time

        # dripping title
        title = self.font_title.render("ZOMBS 2.0", True, (120, 230, 90))
        rect = title.get_rect(center=(SCREEN_W / 2, 92))
        s.blit(self.font_title.render("ZOMBS 2.0", True, (20, 50, 10)), rect.move(4, 6))
        s.blit(title, rect)
        for i, dx in enumerate((-160, -95, -20, 60, 140, 195)):
            length = 14 + 10 * (0.5 + 0.5 * math.sin(t * 1.3 + i * 1.7))
            x = rect.centerx + dx
            pygame.draw.line(s, (120, 230, 90), (x, rect.bottom - 18), (x, rect.bottom - 18 + length), 5)
            pygame.draw.circle(s, (120, 230, 90), (x, int(rect.bottom - 18 + length)), 4)
        story = "You helped a mysterious man... and he teleported you into a maze full of zombies."
        label = self.font_reg.render(story, True, (210, 190, 230))
        s.blit(label, label.get_rect(center=(SCREEN_W / 2, 172)))

        mouse = pygame.mouse.get_pos()
        # spawn chance panel
        left = pygame.Rect(70, 208, 520, 350)
        pygame.draw.rect(s, (24, 16, 34), left, border_radius=14)
        pygame.draw.rect(s, PANEL_EDGE, left, 2, border_radius=14)
        s.blit(self.font_med.render("SPAWN CHANCES (per free cell)", True, YELLOW), (left.x + 20, left.y + 18))
        total = sum(self.probs.values())
        for i, key in enumerate(DEFAULT_PROBS):
            y = 270 + i * 54
            icon = pygame.Vector2(left.x + 34, y + 18)
            if key in ("gun", "potion"):
                draw_crate(s, icon, 22)
                if key == "potion":
                    draw_potion(s, icon + pygame.Vector2(14, -10))
                else:
                    pygame.draw.rect(s, (40, 40, 48), (icon.x + 6, icon.y - 16, 16, 5))
            elif key == "webs":
                pygame.draw.rect(s, FLOOR_A, (icon.x - 18, icon.y - 18, 36, 36), border_radius=4)
                draw_web(s, icon, 16)
            else:
                draw_trap(s, icon, False)
            s.blit(self.font_reg.render(PROB_LABELS[key], True, TEXT), (left.x + 66, y + 8))
            s.blit(self.font_med.render(f"{self.probs[key]}%", True, WHITE), (left.x + 320, y + 7))
            minus, plus = self.prob_buttons[key]
            minus.draw(s, self.font_med, mouse, enabled=self.probs[key] > 0)
            plus.draw(s, self.font_med, mouse, enabled=total < 100)
        s.blit(self.font_reg.render(f"Empty cell: {100 - total}%   (total {total}% / 100%)", True,
                                    (180, 255, 160)), (left.x + 20, 498))
        self.reset_button.draw(s, self.font_small, mouse)
        s.blit(self.font_small.render("Guns & potions get rarer each floor (chance / floor #)", True, DIM_TEXT),
               (left.x + 20, 526 - 4))

        # how to play panel
        right = pygame.Rect(690, 208, 520, 350)
        pygame.draw.rect(s, (24, 16, 34), right, border_radius=14)
        pygame.draw.rect(s, PANEL_EDGE, right, 2, border_radius=14)
        s.blit(self.font_med.render("HOW TO PLAY", True, YELLOW), (right.x + 20, right.y + 18))
        goal = ["Kill every zombie, boss included, to open the door.",
                "Then escape through the door to reach the next floor."]
        for j, line in enumerate(goal):
            s.blit(self.font_reg.render(line, True, (180, 255, 160)), (right.x + 20, right.y + 50 + j * 24))
        cols = [("P1", P1_COLOR, ["WASD - move", "Space - shoot", "L-Shift - sprint"]),
                ("P2", P2_COLOR, ["Arrows - move", "Enter - shoot", "R-Shift - sprint"])]
        for i, (name, color, lines) in enumerate(cols):
            cx = right.x + 30 + i * 250
            pygame.draw.circle(s, color, (cx + 10, right.y + 118), 10)
            s.blit(self.font_med.render(name, True, color), (cx + 28, right.y + 108))
            for j, line in enumerate(lines):
                s.blit(self.font_reg.render(line, True, TEXT), (cx, right.y + 140 + j * 24))
        keys = "R - restart floor      Q - quit      F1 - AI debug"
        s.blit(self.font_reg.render(keys, True, ORANGE), (right.x + 30, right.y + 222))
        tips = ["You shoot in the direction you last moved.",
                "Crates: new gun or potion.   Candy: +10 score, +5 HP.",
                "Webs slow you down. Traps hold you for a few seconds.",
                "If one player goes down, the other keeps going.",
                "Playing solo? Either set of keys works."]
        for j, line in enumerate(tips):
            s.blit(self.font_small.render(line, True, (210, 200, 225)), (right.x + 30, right.y + 256 + j * 18))

        self.one_player_button.draw(s, self.font_med, mouse, selected=self.num_players == 1)
        self.two_player_button.draw(s, self.font_med, mouse, selected=self.num_players == 2)
        self.start_button.draw(s, self.font_big, mouse)


# Main loop (async so pygbag can run it in the browser)
async def main():
    pygame.mixer.pre_init(22050, -16, 1, 512)
    pygame.init()
    game = Game()
    while game.running:
        dt = min(game.clock.tick(FPS) / 1000, 0.05)
        game.handle_events()
        game.update(dt)
        game.draw()
        pygame.display.flip()
        await asyncio.sleep(0)
    pygame.quit()


if __name__ == "__main__":
    asyncio.run(main())
