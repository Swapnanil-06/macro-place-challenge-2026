# Auction Placer — Partcl x HRT Macro Placement Challenge 2026

A combinatorial auction-based macro placer for the [Partcl x HRT Macro Placement Challenge](https://github.com/partcleda/macro-place-challenge-2026).

## Results

| Suite | Avg Proxy | vs SA | vs RePlAce | Overlaps | Runtime |
|-------|-----------|-------|------------|----------|---------|
| IBM benchmarks (×17) | 2.0433 | +3.8% | -40.2% | 0 | ~223s total |
| NG45 benchmarks (×4) | 0.9966 | — | — | 0 | ~1s total |

### NG45 breakdown

| Benchmark | Proxy | WL | Density | Congestion |
|-----------|-------|----|---------|------------|
| ariane133 | 0.8979 | 0.089 | 0.854 | 0.763 |
| ariane136 | 0.9271 | 0.095 | 0.880 | 0.784 |
| mempool_tile | 1.1154 | 0.068 | 1.268 | 0.827 |
| nvdla | 1.0458 | 0.155 | 0.900 | 0.881 |

## How it works

Macro placement is framed as a combinatorial auction:

1. **Auction** — the canvas is divided into a 16×16 grid of candidate slots. Each macro bids on the slot that maximises its value function `-(wirelength + α·density)`. When two macros want the same slot, the loser is re-queued and bids again with updated prices. High-fanout macros bid first.

2. **Legalization** — macros are placed largest-first. Each macro is positioned at its auction-assigned slot, or spiral-searched outward until a non-overlapping canvas-legal position is found.

3. **Local refinement** — random pairs of macros swap positions; the swap is kept if it reduces HPWL, discarded otherwise.

## Usage

```bash
# Single benchmark
uv run evaluate submissions/swapnanil-submission/auction_algo.py -b ibm01

# All IBM benchmarks
uv run evaluate submissions/swapnanil-submission/auction_algo.py --all

# NG45 designs
uv run evaluate submissions/swapnanil-submission/auction_algo.py --ng45

# With visualization
uv run evaluate submissions/swapnanil-submission/auction_algo.py -b ibm01 --vis
```

## Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `grid_resolution` | 16 | Canvas grid size (16×16 = 256 slots) |
| `alpha` | 0.5 | Density weight in value function |
| `n_swaps` | 150 | Local swap refinement iterations |
| `gap` | 0.02 | Minimum gap between macros (μm) |

## Requirements

Follows the standard competition setup — see the root [SETUP.md](../../SETUP.md).

```bash
git clone https://github.com/your-username/macro-place-challenge-2026
cd macro-place-challenge-2026
git submodule update --init external/MacroPlacement
uv sync
```

## License

Apache 2.0