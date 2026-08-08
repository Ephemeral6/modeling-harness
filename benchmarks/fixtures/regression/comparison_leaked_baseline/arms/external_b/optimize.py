"""External baseline optimizer produced by the competing arm."""


def optimize(rows, budget):
    chosen = []
    spent = 0.0
    for row in sorted(rows, key=lambda item: -item["ratio"]):
        if spent + row["cost"] <= budget:
            chosen.append(row["id"])
            spent += row["cost"]
    return {"chosen": chosen, "spent": spent}
