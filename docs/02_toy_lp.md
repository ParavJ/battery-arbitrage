# A four-block battery LP, on paper and in PuLP

Goal: solve a battery problem small enough to do by hand, then see that PuLP gives the same answer
and learn to read what the solver gives back. The real 96-block formulation is yours to write;
this toy is deliberately hard-coded (`docs/toy_lp.py`).

## The problem

| Block t | 1 | 2 | 3 | 4 |
|---|---|---|---|---|
| Price pₜ (₹/MWh) | 3,000 | 2,000 | 9,000 | 6,000 |

Battery: 1 MW power, 1 MWh energy, starts empty and must end empty. Δt = 1 h here so the
arithmetic stays clean (the real model uses Δt = 0.25 h; every "MW × Δt" is then a quarter).

Variables per block: charge cₜ ≥ 0, discharge dₜ ≥ 0 (MW), state of charge sₜ (MWh).

```
maximise   Σₜ pₜ (dₜ − cₜ) Δt  −  k Σₜ (cₜ + dₜ) Δt
subject to sₜ = sₜ₋₁ + η_c cₜ Δt − dₜ Δt / η_d     (energy balance, s₀ = 0)
           0 ≤ cₜ, dₜ ≤ 1                          (power)
           0 ≤ sₜ ≤ 1                              (energy)
           s₄ = s₀ = 0                              (end where you started)
```

12 variables and 5 equality constraints (4 balances + the end condition). The bounds do the rest.

## Case A: ideal battery (η_c = η_d = 1, k = 0)

**On paper.** The battery holds at most 1 MWh, so think in round trips of 1 MWh. The best single
round trip is buy at the cheapest block and sell at the most expensive *later* block: buy in 2
(₹2,000), sell in 3 (₹9,000) → **₹7,000**. Could you do two round trips? A second one would have
to charge before block 2 and discharge before block 2 (only block 1 is available, so no), or
charge and discharge after block 3 (only block 4, so no). So ₹7,000 is optimal.

**In PuLP** the profit is ₹7,000, but the schedule shows `charge = discharge = 1` in blocks 1 and 4.
Charging and discharging at the same time, in a lossless battery with no degradation cost, moves
money and energy around in a perfect circle: −p + p = 0. It is *an* optimal solution, just not the
one you'd draw. LPs with ties like this have many optimal solutions ("degenerate" or "alternative
optima"), and the solver returns whichever vertex it lands on.

## Case B: real losses (η_c = η_d = 0.9, k = ₹100/MWh)

**On paper.** Round-trip efficiency is 0.9 × 0.9 = 0.81. Is a round trip still worth it?

* Buy 1 MWh from the grid at block 2: costs 2,000 + 100 (degradation) and stores 0.9 MWh.
* Sell it in block 3: 0.9 × 0.9 = 0.81 MWh reaches the grid, earning 0.81 × (9,000 − 100) = 7,209.
* Margin per MWh bought: 7,209 − 2,100 = 5,109 > 0, so yes.

Energy capacity now binds differently: the 1 MW for one hour in block 2 stores only 0.9 MWh, so
there is 0.1 MWh of room left. Fill it in the next-cheapest earlier block, block 1: that needs
0.1 / 0.9 = 0.111 MWh bought at ₹3,000. Is that still profitable? Per MWh bought:
0.81 × 8,900 − 3,100 = 4,109 > 0. Yes.

Discharge: 1 MWh stored → 0.9 MWh delivered, all in block 3 (0.9 ≤ 1 MW, so power doesn't bind).
Block 4 at ₹6,000 gets nothing: every stored MWh is worth more in block 3.

```
profit = 9,000 × 0.9  −  3,000 × 0.111  −  2,000 × 1  −  100 × (0.111 + 1 + 0.9)
       = 8,100 − 333.3 − 2,000 − 201.1  =  ₹5,565.6
```

PuLP agrees: ₹5,565.6, with c = (0.111, 1, 0, 0), d = (0, 0, 0.9, 0), s = (0.1, 1, 0, 0).
The simultaneous charge/discharge from case A is gone.

## Why simultaneous charge/discharge disappears (you'll be asked this)

Suppose an optimal schedule has cₜ > 0 and dₜ > 0 in the same block. Lower cₜ by ε and lower dₜ by
η_c η_d ε. The SoC change in that block is η_c(−ε) − (−η_c η_d ε)/η_d = 0, so every other block is
unaffected. The objective changes by

```
pₜ ε (1 − η_c η_d)        (you buy ε but stop selling only η_c η_d ε)
+ k ε (1 + η_c η_d)       (less throughput, less degradation)
```

With pₜ ≥ 0 (the DAM floor is ₹0), η_c η_d < 1 and k > 0, this is strictly positive, so the original
schedule could not have been optimal. Hence **plain LP is enough; no binary variables needed.**
It breaks when prices can go negative (paying to dump energy through losses becomes profitable),
which is why the plan's MILP fallback exists for other markets. In case A both terms are zero:
that's exactly the tie you saw.

Test idea for later: assert `min(c, d) < 1e-6` in every block whenever k > 0 and η < 1.

## Reading the solver output

* **Status** `Optimal`: a proven optimum. Other values: `Infeasible` (constraints contradict, e.g.
  end SoC unreachable), `Unbounded` (a missing bound; impossible here since all variables are boxed).
  Always check status before using the numbers.
* **Objective** and **variable values**: the schedule.
* **Duals (shadow prices)** of the balance constraints, the last column of the script's output: the
  value of one extra MWh sitting in the battery at the end of block t. In case B:
  * blocks 1–2: 3,444.4 = (3,000 + 100) / 0.9, the cost of putting 1 MWh into storage in block 1
    (the marginal block);
  * block 3: 8,010 = 0.9 × (9,000 − 100), what 1 stored MWh earns when sold in block 3.

  The jump 8,010 − 3,444.4 = **₹4,565.6 is the shadow price of energy capacity**: what one more
  MWh of capacity would add to this day's profit. That is the economic question behind "should I
  build a 2-hour or a 4-hour battery?", and your sensitivity sweeps will measure it directly.

## Check yourself

1. Write the four balance equations of case B with numbers and verify s = (0.1, 1, 0, 0).
2. Why must end SoC equal start SoC? (What would the optimiser do with the battery's starting
   energy otherwise?)
3. Change block 4's price to 9,500. Predict the new schedule before running the script.
4. Why is this an LP and not a MILP, and when would you need the binary?
5. With Δt = 0.25 h, a 1 MW / 2 MWh battery: how many full-power blocks to go from empty to full
   if η_c = 0.95?
