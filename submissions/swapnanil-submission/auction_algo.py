"""
Auction Placer

Treats macro placement as a combinatorial auction:
- Macros bid on canvas slots based on a wirelength + density value function
- Contested slots have rising prices until one macro backs off
- Followed by largest-first legalization (spiral push)
- Lightweight local swap refinement (neighbour-only, no full overlap scan)

Usage:
    uv run evaluate submissions/swapnanil-submission/auction_algo.py --all
    uv run evaluate submissions/swapnanil-submission/auction_algo.py --ng45

"""

import random
from collections import defaultdict, deque

import numpy as np
import torch

from macro_place.benchmark import Benchmark


class AuctionPlacer:
    """
    Simple auction-based macro placer.

    Parameters
    ----------
    grid_resolution : int
        Canvas divided into grid_resolution x grid_resolution slots.
    alpha : float
        Weight on density term (matches proxy cost default 0.5).
    n_swaps : int
        Number of random pair-swap attempts in local refinement.
    gap : float
        Minimum gap between macros to avoid float-precision edge overlaps.
    """

    def __init__(
        self,
        grid_resolution: int = 16,
        alpha: float = 0.5,
        n_swaps: int = 150,
        gap: float = 0.02,
    ):
        self.grid_resolution = grid_resolution
        self.alpha = alpha
        self.n_swaps = n_swaps
        self.gap = gap

    def place(self, benchmark: Benchmark) -> torch.Tensor:
        placement = benchmark.macro_positions.clone()
        movable = benchmark.get_movable_mask() & benchmark.get_hard_macro_mask()
        movable_indices = torch.where(movable)[0].tolist()

        if not movable_indices:
            return placement

        sizes = benchmark.macro_sizes.numpy()
        canvas_w = float(benchmark.canvas_width)
        canvas_h = float(benchmark.canvas_height)

        adjacency = self._build_adjacency(benchmark, movable_indices)
        slots = self._build_slots(canvas_w, canvas_h)
        slots_np = np.array(slots, dtype=np.float32)

        epsilon = 1.0 / len(movable_indices)

        positions = self._run_auction(
            movable_indices, slots, slots_np, adjacency,
            canvas_w, canvas_h, epsilon,
        )

        positions = self._legalize(movable_indices, positions, sizes, canvas_w, canvas_h)

        positions = self._local_swap(
            movable_indices, positions, sizes, adjacency, canvas_w, canvas_h
        )

        for idx in movable_indices:
            if positions.get(idx) is not None:
                placement[idx, 0] = float(positions[idx][0])
                placement[idx, 1] = float(positions[idx][1])

        return placement

     
    # Graph + slots
     

    def _build_adjacency(self, benchmark: Benchmark, movable_indices):
        adjacency = defaultdict(lambda: defaultdict(float))
        movable_set = set(movable_indices)

        nets = getattr(benchmark, "nets", None)
        if nets is None:
            return adjacency

        for net in nets:
            members_raw = (
                getattr(net, "macro_ids", None)
                or getattr(net, "node_ids", None)
                or []
            )
            members = [m for m in members_raw if m in movable_set]
            if len(members) < 2:
                continue

            weight = float(getattr(net, "weight", 1.0))
            for m1 in members:
                for m2 in members:
                    if m1 != m2:
                        adjacency[m1][m2] += weight

        return adjacency

    def _build_slots(self, canvas_w: float, canvas_h: float):
        res = self.grid_resolution
        return [
            ((i + 0.5) * canvas_w / res, (j + 0.5) * canvas_h / res)
            for i in range(res)
            for j in range(res)
        ]

     
    # Value function
     

    def _compute_values(
        self,
        macro_id,
        slots_np,
        positions,
        adjacency,
        canvas_w,
        canvas_h,
    ):
        sx = slots_np[:, 0]
        sy = slots_np[:, 1]

        wl_cost = np.zeros(len(slots_np), dtype=np.float32)
        dens_cost = np.zeros(len(slots_np), dtype=np.float32)

        neighbours = adjacency.get(macro_id, {})
        norm_len = canvas_w + canvas_h

        for nid, weight in neighbours.items():
            pos = positions.get(nid)
            nx, ny = pos if pos is not None else (canvas_w / 2, canvas_h / 2)

            wl_cost += weight * (np.abs(sx - nx) + np.abs(sy - ny)) / norm_len

        # density
        placed = [p for k, p in positions.items() if p is not None and k != macro_id][:20]

        if placed:
            bin_size = canvas_w / self.grid_resolution
            radius = bin_size * 2.0

            for ox, oy in placed:
                dist = np.sqrt((sx - ox) ** 2 + (sy - oy) ** 2)
                mask = dist < radius
                dens_cost[mask] += 1.0 / np.maximum(dist[mask], 1e-3)

            dens_cost /= len(placed)

        return -(wl_cost + self.alpha * dens_cost)

     
    # Auction
     

    def _run_auction(
        self,
        movable_indices,
        slots,
        slots_np,
        adjacency,
        canvas_w,
        canvas_h,
        epsilon,
    ):
        M = len(slots)
        prices = np.zeros(M, dtype=np.float32)
        slot_occupant = [None] * M
        positions = {m: None for m in movable_indices}

        fanout = {m: sum(adjacency.get(m, {}).values()) for m in movable_indices}
        queue = deque(sorted(movable_indices, key=lambda m: -fanout[m]))

        max_iters = len(movable_indices) * 50   # ✅ MUCH larger
        stall_counter = 0

        for _ in range(max_iters):
            if not queue:
                break

            macro_id = queue.popleft()

            values = self._compute_values(
                macro_id, slots_np, positions, adjacency, canvas_w, canvas_h
            )
            net = values - prices

            best = int(np.argmax(net))
            second = np.partition(net, -2)[-2] if M > 1 else net[best]

            bid = prices[best] + (net[best] - second) + epsilon

            prev = slot_occupant[best]
            if prev == macro_id:
                stall_counter += 1
                if stall_counter > len(movable_indices):
                    break
                continue

            stall_counter = 0

            if prev is not None:
                positions[prev] = None
                queue.append(prev)

            slot_occupant[best] = macro_id
            prices[best] = bid
            positions[macro_id] = slots[best]

        return positions

     
    # Legalization
     

    def _legalize(self, movable_indices, positions, sizes, canvas_w, canvas_h):
        ordered = sorted(movable_indices, key=lambda i: -(sizes[i, 0] * sizes[i, 1]))
        placed = []
        final = {}

        for i in ordered:
            w, h = float(sizes[i, 0]), float(sizes[i, 1])
            tx, ty = positions.get(i) or (canvas_w / 2, canvas_h / 2)

            cx, cy = self._find_free_position(tx, ty, w, h, placed, canvas_w, canvas_h)
            placed.append((cx, cy, w, h))
            final[i] = (cx, cy)

        return final

    def _find_free_position(self, tx, ty, w, h, placed, canvas_w, canvas_h):
        step = max(w, h) * 0.55

        for r in range(80):
            angles = np.linspace(0, 2 * np.pi, max(8, r * 5), endpoint=False)
            for a in angles:
                cx = tx + r * step * np.cos(a)
                cy = ty + r * step * np.sin(a)

                cx = float(np.clip(cx, w / 2, canvas_w - w / 2))
                cy = float(np.clip(cy, h / 2, canvas_h - h / 2))

                if not any(
                    self._overlaps(cx, cy, w, h, px, py, pw, ph)
                    for px, py, pw, ph in placed
                ):
                    return cx, cy

        return tx, ty

    def _overlaps(self, x1, y1, w1, h1, x2, y2, w2, h2):
        return (
            abs(x1 - x2) < (w1 + w2) / 2 + self.gap
            and abs(y1 - y2) < (h1 + h2) / 2 + self.gap
        )

     
    # Local refinement
     

    def _wirelength_estimate(self, positions, adjacency, canvas_w, canvas_h):
        total = 0.0
        seen = set()
        for m1, neigh in adjacency.items():
            for m2, w in neigh.items():
                key = (min(m1, m2), max(m1, m2))
                if key in seen:
                    continue
                seen.add(key)
                p1, p2 = positions.get(m1), positions.get(m2)
                if p1 and p2:
                    total += w * (abs(p1[0] - p2[0]) + abs(p1[1] - p2[1]))
        return total / (canvas_w + canvas_h)

    def _local_swap(self, movable_indices, positions, sizes, adjacency, canvas_w, canvas_h):
        indices = list(movable_indices)
        if len(indices) < 2:
            return positions

        cost = self._wirelength_estimate(positions, adjacency, canvas_w, canvas_h)

        for _ in range(self.n_swaps):
            i, j = random.sample(indices, 2)
            pi, pj = positions.get(i), positions.get(j)
            if pi is None or pj is None:
                continue

            positions[i], positions[j] = pj, pi

            new_cost = self._wirelength_estimate(positions, adjacency, canvas_w, canvas_h)
            if new_cost < cost:
                cost = new_cost
            else:
                positions[i], positions[j] = pi, pj

        return positions


def place(benchmark: Benchmark) -> torch.Tensor:
    return AuctionPlacer().place(benchmark)