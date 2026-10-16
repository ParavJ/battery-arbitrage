"""Four-block toy battery LP, small enough to solve on paper first (see docs/02_toy_lp.md).

Deliberately hard-coded and throwaway: the real, general formulation is written separately in
src/battery_arb/strategies/. Run with:

    uv run python docs/toy_lp.py
"""

import pulp

PRICES = [3_000, 2_000, 9_000, 6_000]  # Rs/MWh, one per block
DT = 1.0  # hours per block (1 h here to keep the paper arithmetic clean; the real model uses 0.25)
P_MAX = 1.0  # MW, power rating (charge and discharge)
E_MAX = 1.0  # MWh, energy capacity
S0 = 0.0  # MWh, starting state of charge (end SoC must equal it)


def solve(eta_c: float, eta_d: float, k: float) -> None:
    T = range(len(PRICES))
    prob = pulp.LpProblem("toy_battery", pulp.LpMaximize)

    # Decision variables; the bounds ARE the power and SoC limits.
    c = pulp.LpVariable.dicts("charge_MW", T, lowBound=0, upBound=P_MAX)
    d = pulp.LpVariable.dicts("discharge_MW", T, lowBound=0, upBound=P_MAX)
    s = pulp.LpVariable.dicts("soc_MWh", T, lowBound=0, upBound=E_MAX)

    # Objective: market cash flow minus degradation cost on throughput.
    prob += pulp.lpSum(PRICES[t] * (d[t] - c[t]) * DT - k * (c[t] + d[t]) * DT for t in T)

    # Energy balance, one equality per block. Losses on the way in AND on the way out.
    for t in T:
        prev = S0 if t == 0 else s[t - 1]
        prob += s[t] == prev + eta_c * c[t] * DT - d[t] * DT / eta_d, f"balance_{t}"
    prob += s[len(PRICES) - 1] == S0, "end_soc"

    prob.solve(pulp.PULP_CBC_CMD(msg=False))

    print(f"\neta_c={eta_c}, eta_d={eta_d}, k={k}  ->  status: {pulp.LpStatus[prob.status]}")
    print(f"profit: Rs {pulp.value(prob.objective):,.1f}")
    print(" t  price   charge  discharge   SoC   balance dual")
    for t in T:
        dual = prob.constraints[f"balance_{t}"].pi
        print(
            f" {t + 1}  {PRICES[t]:5d}  {c[t].value():7.3f}  {d[t].value():9.3f}  "
            f"{s[t].value():5.3f}  {dual:12.1f}"
        )


if __name__ == "__main__":
    solve(eta_c=1.0, eta_d=1.0, k=0)  # case A: ideal battery
    solve(eta_c=0.9, eta_d=0.9, k=100)  # case B: losses + degradation
