"""Run Kronos on the inputs exported by `python -m athena.evaluation.kronos_test export`. Run it with the Kronos environment's Python
(it imports nothing from Athena):

    D:/projects/learn-project/kronos/.venv/Scripts/python.exe scripts/kronos_forecast.py \
        --kronos-dir D:/projects/learn-project/kronos --inputs .athena/kronos/inputs.jsonl --out .athena/kronos/forecasts.jsonl

Resumable: dates already in the output are skipped. Each input line is one date with the same-length history of every stock; the output
has one line per stock and date with the predicted return after 63 and 126 trading days (average of the sampled paths)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HORIZONS = (63, 126)


def predicted_returns(closes, last_close, horizons=HORIZONS):
    """Predicted return at each horizon (1-based step) from a forecast close path."""
    return {f"ret{h}": float(closes[h - 1] / last_close - 1) for h in horizons if len(closes) >= h and last_close}


def frame(rows):
    import pandas as pd

    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    return df[["open", "high", "low", "close", "volume"]], pd.Series(pd.to_datetime(df["date"]))


def future_stamps(last_day, pred_len):
    import pandas as pd

    return pd.Series(pd.bdate_range(pd.Timestamp(last_day) + pd.Timedelta(days=1), periods=pred_len))


def done_dates(path):
    if not path.exists():
        return set()
    seen = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            seen.add(json.loads(line)["as_of"])
        except (ValueError, KeyError):
            continue
    return seen


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--kronos-dir", required=True)
    parser.add_argument("--inputs", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--model", default="NeoQuasar/Kronos-small")
    parser.add_argument("--tokenizer", default="NeoQuasar/Kronos-Tokenizer-base")
    parser.add_argument("--max-context", type=int, default=512)
    parser.add_argument("--pred-len", type=int, default=max(HORIZONS))
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument("--chunk", type=int, default=10, help="stocks forecast together")
    parser.add_argument("--device", default=None)
    parser.add_argument("--limit", type=int, default=0, help="stop after this many dates (a smoke run)")
    args = parser.parse_args(argv)

    sys.path.insert(0, args.kronos_dir)
    import numpy as np
    import torch
    from model import Kronos, KronosPredictor, KronosTokenizer

    device = args.device or ("cuda:0" if torch.cuda.is_available() else "cpu")
    tokenizer = KronosTokenizer.from_pretrained(args.tokenizer)
    model = Kronos.from_pretrained(args.model)
    predictor = KronosPredictor(model, tokenizer, device=device, max_context=args.max_context)
    print(f"device {device}", flush=True)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    skip = done_dates(out)
    made = 0
    for line in Path(args.inputs).read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        day = entry["as_of"]
        if day in skip:
            continue
        symbols = sorted(entry["stocks"])
        rows = []
        for start in range(0, len(symbols), args.chunk):
            chunk = symbols[start:start + args.chunk]
            frames, stamps = zip(*(frame(entry["stocks"][s]) for s in chunk))
            future = future_stamps(entry["stocks"][chunk[0]][-1][0], args.pred_len)
            seed = args.seed + int(day.replace("-", "")) + start
            torch.manual_seed(seed)
            np.random.seed(seed % (2 ** 32))
            preds = predictor.predict_batch(
                df_list=list(frames), x_timestamp_list=list(stamps), y_timestamp_list=[future] * len(chunk),
                pred_len=args.pred_len, T=1.0, top_p=0.9, sample_count=args.samples, verbose=False,
            )
            for symbol, pred in zip(chunk, preds):
                last_close = entry["stocks"][symbol][-1][4]
                rows.append({"as_of": day, "symbol": symbol, "lookback": entry["lookback"], "samples": args.samples, "seed": seed,
                             **predicted_returns(pred["close"].to_numpy(), last_close)})
        with out.open("a", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")
        made += 1
        print(f"{day}: {len(rows)} stocks forecast", flush=True)
        if args.limit and made >= args.limit:
            break
    print(f"{made} dates forecast")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
