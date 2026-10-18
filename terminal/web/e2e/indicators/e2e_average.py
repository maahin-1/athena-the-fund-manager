NAME = "E2E average"
PANE = "overlay"


def compute(bars):
    out = []
    for i in range(len(bars)):
        out.append(None if i < 2 else (bars[i]["c"] + bars[i - 1]["c"] + bars[i - 2]["c"]) / 3)
    return {"avg3": out}
